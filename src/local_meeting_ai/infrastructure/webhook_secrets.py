from __future__ import annotations

import importlib
import importlib.util
from typing import Any, Protocol, cast

from local_meeting_ai.domain.errors import CapabilityUnavailableError

SERVICE_NAME = "Meet2Notes"


class WebhookSecretStore(Protocol):
    def get(self, endpoint_id: str) -> str | None: ...

    def set(self, endpoint_id: str, value: str) -> None: ...

    def delete(self, endpoint_id: str) -> None: ...

    def available(self) -> bool: ...


class KeyringWebhookSecretStore:
    def available(self) -> bool:
        if importlib.util.find_spec("keyring") is None:
            return False
        try:
            _keyring().get_password(SERVICE_NAME, "webhook/status-check")
        except Exception:
            return False
        return True

    def get(self, endpoint_id: str) -> str | None:
        if importlib.util.find_spec("keyring") is None:
            return None
        try:
            return cast(
                str | None,
                _keyring().get_password(SERVICE_NAME, _account(endpoint_id)),
            )
        except Exception:
            return None

    def set(self, endpoint_id: str, value: str) -> None:
        if importlib.util.find_spec("keyring") is None:
            raise CapabilityUnavailableError(
                "Secure OS credential storage is unavailable for webhook signing"
            )
        try:
            _keyring().set_password(SERVICE_NAME, _account(endpoint_id), value)
        except Exception as error:
            raise CapabilityUnavailableError(
                "The webhook signing secret could not be saved in the operating-system "
                f"credential store: {error}"
            ) from error

    def delete(self, endpoint_id: str) -> None:
        if importlib.util.find_spec("keyring") is None:
            return
        try:
            _keyring().delete_password(SERVICE_NAME, _account(endpoint_id))
        except Exception as error:
            lowered = str(error).lower()
            if "not found" not in lowered and "no password" not in lowered:
                raise CapabilityUnavailableError(
                    f"The webhook signing secret could not be removed: {error}"
                ) from error


class MemoryWebhookSecretStore:
    """Test-only secret store; production secrets always use the OS keyring."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def available(self) -> bool:
        return True

    def get(self, endpoint_id: str) -> str | None:
        return self.values.get(endpoint_id)

    def set(self, endpoint_id: str, value: str) -> None:
        self.values[endpoint_id] = value

    def delete(self, endpoint_id: str) -> None:
        self.values.pop(endpoint_id, None)


def _account(endpoint_id: str) -> str:
    return f"webhook/{endpoint_id}/signing-secret"


def _keyring() -> Any:
    return importlib.import_module("keyring")
