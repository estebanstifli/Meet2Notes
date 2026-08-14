from __future__ import annotations

import pytest

from local_meeting_ai.adapters import litellm_compat


@pytest.fixture(autouse=True)
def clear_rejected_parameters() -> None:
    with litellm_compat._rejected_parameters_lock:
        litellm_compat._rejected_parameters.clear()


class FakeLiteLLM:
    def __init__(self, *, supported: list[str] | None, failure: Exception | None = None) -> None:
        self.supported = supported
        self.failure = failure
        self.calls: list[dict[str, object]] = []

    def get_llm_provider(self, **_: object) -> tuple[str, str, None, None]:
        return "example-model", "openai", None, None

    def get_supported_openai_params(self, **_: object) -> list[str] | None:
        return self.supported

    def completion(self, **arguments: object) -> dict[str, object]:
        self.calls.append(arguments)
        if self.failure is not None and len(self.calls) == 1:
            raise self.failure
        return {"choices": []}


def test_completion_omits_parameters_not_supported_by_model() -> None:
    client = FakeLiteLLM(supported=["max_completion_tokens"])

    litellm_compat.completion(
        client,
        {
            "model": "openai/example-model",
            "messages": [],
            "max_tokens": 40,
            "temperature": 0.2,
            "top_p": 0.9,
        },
    )

    assert client.calls == [
        {
            "model": "openai/example-model",
            "messages": [],
            "max_completion_tokens": 40,
            "drop_params": True,
        }
    ]


def test_completion_retries_once_without_value_restricted_parameter_and_caches_it() -> None:
    client = FakeLiteLLM(
        supported=["temperature", "max_tokens"],
        failure=ValueError("Unsupported value: 'temperature' only the default value is supported"),
    )
    arguments = {
        "model": "openai/example-model",
        "messages": [],
        "max_tokens": 40,
        "temperature": 0.2,
    }

    litellm_compat.completion(client, arguments)
    litellm_compat.completion(client, arguments)

    assert len(client.calls) == 3
    assert client.calls[0]["temperature"] == 0.2
    assert "temperature" not in client.calls[1]
    assert "temperature" not in client.calls[2]


def test_completion_does_not_retry_unrelated_errors() -> None:
    client = FakeLiteLLM(supported=None, failure=ValueError("Invalid API key"))

    with pytest.raises(ValueError, match="Invalid API key"):
        litellm_compat.completion(client, {"model": "openai/example-model", "messages": []})

    assert len(client.calls) == 1
