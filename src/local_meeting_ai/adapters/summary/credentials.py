from __future__ import annotations

import importlib
import importlib.util
from typing import Any, cast

from local_meeting_ai.domain.errors import CapabilityUnavailableError

SERVICE_NAME = "Meet2Notes"
LITELLM_ACCOUNT = "summary/litellm-api-key"


def secure_storage_status() -> dict[str, bool]:
    """Return keyring availability and whether the LiteLLM secret exists."""
    if importlib.util.find_spec("keyring") is None:
        return {"available": False, "configured": False}
    try:
        return {
            "available": True,
            "configured": bool(_keyring().get_password(SERVICE_NAME, LITELLM_ACCOUNT)),
        }
    except Exception:
        return {"available": False, "configured": False}


def get_litellm_api_key() -> str | None:
    if importlib.util.find_spec("keyring") is None:
        return None
    try:
        return cast(str | None, _keyring().get_password(SERVICE_NAME, LITELLM_ACCOUNT))
    except Exception:
        return None


def set_litellm_api_key(value: str) -> None:
    if importlib.util.find_spec("keyring") is None:
        raise CapabilityUnavailableError(
            "Secure OS credential storage is unavailable. Install the keyring dependency."
        )
    try:
        _keyring().set_password(SERVICE_NAME, LITELLM_ACCOUNT, value)
    except Exception as error:
        raise CapabilityUnavailableError(
            f"The API key could not be saved in the operating-system credential store: {error}"
        ) from error


def delete_litellm_api_key() -> None:
    if importlib.util.find_spec("keyring") is None:
        return
    try:
        _keyring().delete_password(SERVICE_NAME, LITELLM_ACCOUNT)
    except Exception as error:
        # Most backends raise when the credential does not exist; deletion is idempotent.
        if "not found" not in str(error).lower() and "no password" not in str(error).lower():
            raise CapabilityUnavailableError(
                f"The API key could not be removed from the credential store: {error}"
            ) from error


def _keyring() -> Any:
    return importlib.import_module("keyring")
