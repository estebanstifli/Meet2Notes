from __future__ import annotations

import importlib
import importlib.util
from typing import Any, Protocol, cast

from local_meeting_ai.domain.errors import CapabilityUnavailableError

SERVICE_NAME = "Meet2Notes"
ACCOUNT_NAME = "live-assistant/litellm-api-key"


class LiveAssistantCredentialStore(Protocol):
    def available(self) -> bool: ...

    def get(self) -> str | None: ...

    def set(self, value: str) -> None: ...

    def delete(self) -> None: ...

    def status(self) -> dict[str, bool]: ...


class KeyringLiveAssistantCredentialStore:
    def available(self) -> bool:
        if importlib.util.find_spec("keyring") is None:
            return False
        try:
            _keyring().get_password(SERVICE_NAME, ACCOUNT_NAME)
        except Exception:
            return False
        return True

    def get(self) -> str | None:
        if not self.available():
            return None
        try:
            return cast(str | None, _keyring().get_password(SERVICE_NAME, ACCOUNT_NAME))
        except Exception:
            return None

    def set(self, value: str) -> None:
        if not self.available():
            raise CapabilityUnavailableError(
                "Secure OS credential storage is unavailable for the Live AI Assistant"
            )
        try:
            _keyring().set_password(SERVICE_NAME, ACCOUNT_NAME, value)
        except Exception as error:
            raise CapabilityUnavailableError(
                "The Live AI Assistant API key could not be saved in the operating-system "
                f"credential store: {error}"
            ) from error

    def delete(self) -> None:
        if importlib.util.find_spec("keyring") is None:
            return
        try:
            _keyring().delete_password(SERVICE_NAME, ACCOUNT_NAME)
        except Exception as error:
            detail = str(error).lower()
            if "not found" not in detail and "no password" not in detail:
                raise CapabilityUnavailableError(
                    f"The Live AI Assistant API key could not be removed: {error}"
                ) from error

    def status(self) -> dict[str, bool]:
        available = self.available()
        return {"available": available, "configured": bool(self.get()) if available else False}


class MemoryLiveAssistantCredentialStore:
    def __init__(self) -> None:
        self.value: str | None = None

    def available(self) -> bool:
        return True

    def get(self) -> str | None:
        return self.value

    def set(self, value: str) -> None:
        self.value = value

    def delete(self) -> None:
        self.value = None

    def status(self) -> dict[str, bool]:
        return {"available": True, "configured": bool(self.value)}


def _keyring() -> Any:
    return importlib.import_module("keyring")
