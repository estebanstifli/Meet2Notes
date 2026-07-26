from __future__ import annotations

import platform

from local_meeting_ai.domain.protocols import AudioCaptureBackend


def create_audio_capture_backend() -> AudioCaptureBackend:
    system = platform.system()
    if system == "Windows":
        from local_meeting_ai.adapters.audio_capture.windows_wasapi import (
            WindowsWasapiCaptureBackend,
        )

        return WindowsWasapiCaptureBackend()
    if system in {"Darwin", "Linux"}:
        from local_meeting_ai.adapters.audio_capture.portaudio import (
            PortAudioCaptureBackend,
        )

        return PortAudioCaptureBackend(system)

    from local_meeting_ai.adapters.audio_capture.unavailable import (
        UnavailableCaptureBackend,
    )

    return UnavailableCaptureBackend(
        platform_name=system,
        reason=f"Native audio capture is not implemented for {system}.",
    )
