from __future__ import annotations

from pathlib import Path

from local_meeting_ai.domain.entities import (
    AudioCaptureSource,
    AudioFrameBatch,
    CapturedAudio,
    CaptureStatus,
)
from local_meeting_ai.domain.errors import CapabilityUnavailableError


class UnavailableCaptureBackend:
    name = "unavailable"

    def __init__(self, *, platform_name: str, reason: str) -> None:
        self.platform_name = platform_name
        self.reason = reason

    def capability(self) -> dict[str, object]:
        return {
            "available": False,
            "backend": self.name,
            "platform": self.platform_name,
            "reason": self.reason,
            "supports_microphones": False,
            "supports_system_audio": False,
        }

    def list_sources(self) -> list[AudioCaptureSource]:
        return []

    def probe_level(self, source_id: str) -> float:
        del source_id
        raise CapabilityUnavailableError(self.reason)

    def start(
        self,
        *,
        session_id: str,
        source_id: str,
        destination: Path,
    ) -> CaptureStatus:
        del session_id, source_id, destination
        raise CapabilityUnavailableError(self.reason)

    def status(self) -> CaptureStatus | None:
        return None

    def drain_frames(self) -> AudioFrameBatch | None:
        return None

    def pause(self) -> CaptureStatus:
        raise CapabilityUnavailableError(self.reason)

    def resume(self) -> CaptureStatus:
        raise CapabilityUnavailableError(self.reason)

    def stop(self) -> CapturedAudio:
        raise CapabilityUnavailableError(self.reason)

    def shutdown(self) -> None:
        return None
