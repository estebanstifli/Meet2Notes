"""Compatibility helpers for LiteLLM's provider- and model-specific parameters.

LiteLLM can discard parameters a provider does not support, but some providers
also accept a parameter only with their default value. This module makes a
single, narrowly scoped retry after that kind of validation error and remembers
the restriction for the lifetime of the process.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from threading import Lock
from typing import Any

logger = logging.getLogger(__name__)

_MODEL_PARAMETERS = (
    "temperature",
    "top_p",
    "presence_penalty",
    "frequency_penalty",
    "seed",
    "max_tokens",
    "max_completion_tokens",
)
_RESTRICTED_PARAMETER = re.compile(r"['\"](?P<name>[a-z_]+)['\"]", re.IGNORECASE)
_UNSUPPORTED_WORDS = ("unsupported", "not support", "unknown parameter", "only the default")
_rejected_parameters: set[tuple[str, str]] = set()
_rejected_parameters_lock = Lock()


def completion(litellm: Any, arguments: dict[str, Any]) -> Any:
    """Call ``litellm.completion`` using compatible optional parameters."""
    return _invoke(litellm, "completion", arguments, filter_model_parameters=True)


def embedding(litellm: Any, arguments: dict[str, Any]) -> Any:
    """Call ``litellm.embedding`` through the shared compatibility boundary."""
    return _invoke(litellm, "embedding", arguments, filter_model_parameters=False)


def _invoke(
    litellm: Any,
    operation: str,
    arguments: dict[str, Any],
    *,
    filter_model_parameters: bool,
) -> Any:
    request = dict(arguments)
    model = str(request.get("model") or "")
    if filter_model_parameters:
        request = _filter_supported_parameters(litellm, request, model)
        request.setdefault("drop_params", True)

    invoke: Callable[..., Any] = getattr(litellm, operation)
    while True:
        try:
            return invoke(**request)
        except Exception as error:
            parameter = _restricted_parameter(error, request)
            if not parameter:
                raise
            _remember_rejected(model, parameter)
            request.pop(parameter, None)
            logger.warning(
                "LiteLLM %s rejected %s for model %s; retrying without it",
                operation,
                parameter,
                model,
            )


def _filter_supported_parameters(
    litellm: Any,
    arguments: dict[str, Any],
    model: str,
) -> dict[str, Any]:
    supported = _supported_parameters(litellm, model, arguments)
    filtered = dict(arguments)
    if supported is not None:
        supported_set = set(supported)
        if "max_tokens" in filtered and "max_tokens" not in supported_set:
            if "max_completion_tokens" in supported_set:
                filtered["max_completion_tokens"] = filtered.pop("max_tokens")
            else:
                filtered.pop("max_tokens")
        for parameter in _MODEL_PARAMETERS:
            if parameter in filtered and parameter not in supported_set:
                filtered.pop(parameter)
    with _rejected_parameters_lock:
        rejected = {
            parameter
            for rejected_model, parameter in _rejected_parameters
            if rejected_model == model
        }
    for parameter in rejected:
        filtered.pop(parameter, None)
    return filtered


def _supported_parameters(litellm: Any, model: str, arguments: dict[str, Any]) -> list[str] | None:
    if not model or not hasattr(litellm, "get_supported_openai_params"):
        return None
    try:
        provider = None
        if hasattr(litellm, "get_llm_provider"):
            _, provider, _, _ = litellm.get_llm_provider(
                model=model,
                api_base=arguments.get("api_base"),
                api_key=arguments.get("api_key"),
            )
        supported = litellm.get_supported_openai_params(
            model=model,
            custom_llm_provider=provider,
        )
        return [str(parameter) for parameter in supported] if isinstance(supported, list) else None
    except Exception as error:
        logger.debug("Could not determine LiteLLM parameter support for %s: %s", model, error)
        return None


def _restricted_parameter(error: Exception, request: dict[str, Any]) -> str | None:
    message = str(error).lower()
    if not any(marker in message for marker in _UNSUPPORTED_WORDS):
        return None
    match = _RESTRICTED_PARAMETER.search(message)
    if not match:
        return None
    parameter = match.group("name")
    return parameter if parameter in _MODEL_PARAMETERS and parameter in request else None


def _remember_rejected(model: str, parameter: str) -> None:
    with _rejected_parameters_lock:
        _rejected_parameters.add((model, parameter))
