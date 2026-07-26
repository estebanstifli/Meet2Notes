from __future__ import annotations

from local_meeting_ai.adapters.audio_capture.windows_wasapi import (
    _default_loopback_index,
    _prefer_native_sources,
)
from local_meeting_ai.application.capture_service import _trim_duplicate_prefix
from local_meeting_ai.domain.entities import AudioCaptureSource


def _source(
    source_id: str,
    name: str,
    host_api: str,
    *,
    is_loopback: bool = False,
) -> AudioCaptureSource:
    return AudioCaptureSource(
        id=source_id,
        name=name,
        kind="system" if is_loopback else "microphone",
        backend="wasapi",
        host_api=host_api,
        channels=2,
        sample_rate=48000,
        is_loopback=is_loopback,
    )


def test_windows_source_list_prefers_wasapi_and_keeps_loopback_endpoints() -> None:
    sources = _prefer_native_sources(
        [
            _source("mme", "Studio microphone", "MME"),
            _source("directsound", "Studio microphone", "Windows DirectSound"),
            _source("wasapi", "Studio microphone", "Windows WASAPI"),
            _source("mapper", "Microsoft Sound Mapper - Input", "MME"),
            _source(
                "loopback",
                "Speakers · System audio",
                "Windows WASAPI",
                is_loopback=True,
            ),
        ]
    )

    assert [source.id for source in sources] == ["wasapi", "loopback"]


def test_invalid_windows_default_output_does_not_break_source_discovery() -> None:
    class InvalidDefaultManager:
        def get_default_wasapi_loopback(self) -> object:
            raise ValueError("`info_dict` must represent an output device")

    assert _default_loopback_index(InvalidDefaultManager()) == -1


def test_realtime_overlap_removes_only_the_repeated_prefix() -> None:
    assert (
        _trim_duplicate_prefix(
            "the launch is approved for Monday",
            "We agreed that the launch is approved",
        )
        == "for Monday"
    )
    assert _trim_duplicate_prefix("A new point", "Previous discussion") == "A new point"
