from __future__ import annotations

import importlib
import math
import threading
import time
import wave
from array import array
from pathlib import Path
from typing import Any

from local_meeting_ai.domain.entities import (
    AudioCaptureSource,
    AudioFrameBatch,
    CapturedAudio,
    CaptureStatus,
)
from local_meeting_ai.domain.errors import CapabilityUnavailableError, ValidationError

_SYSTEM_SOURCE_HINTS = ("monitor", "blackhole", "soundflower", "loopback")


class PortAudioCaptureBackend:
    """CoreAudio/ALSA/Pulse/PipeWire inputs exposed through PortAudio."""

    name = "portaudio"

    def __init__(self, platform_name: str) -> None:
        self.platform_name = platform_name
        self._lock = threading.RLock()
        self._buffer_lock = threading.Lock()
        self._stream: Any | None = None
        self._wave: wave.Wave_write | None = None
        self._session_id: str | None = None
        self._source: AudioCaptureSource | None = None
        self._destination: Path | None = None
        self._state = "idle"
        self._active_started_at = 0.0
        self._active_seconds = 0.0
        self._level = 0.0
        self._pending_pcm = bytearray()
        self._pending_start_frame = 0
        self._total_frames = 0
        self._buffer_sample_rate = 0
        self._buffer_channels = 0

    @staticmethod
    def _module() -> Any:
        try:
            return importlib.import_module("sounddevice")
        except ImportError as error:
            raise CapabilityUnavailableError(
                'Native capture is not installed. Run: pip install -e ".[capture]"'
            ) from error

    def capability(self) -> dict[str, object]:
        try:
            sources = self.list_sources()
        except CapabilityUnavailableError as error:
            return {
                "available": False,
                "backend": self.name,
                "platform": self.platform_name,
                "reason": str(error),
                "supports_microphones": False,
                "supports_system_audio": False,
            }
        native_api = "CoreAudio" if self.platform_name == "Darwin" else "PipeWire/Pulse/ALSA"
        return {
            "available": True,
            "backend": self.name,
            "platform": self.platform_name,
            "native_api": native_api,
            "supports_microphones": any(source.kind != "system" for source in sources),
            "supports_system_audio": any(source.kind == "system" for source in sources),
            "source_count": len(sources),
            "permission_required": self.platform_name == "Darwin",
            "system_audio_note": (
                "macOS system audio requires a Core Audio Tap, ScreenCaptureKit, "
                "or a virtual input device."
                if self.platform_name == "Darwin"
                else "PipeWire/Pulse monitor sources are detected automatically."
            ),
        }

    def list_sources(self) -> list[AudioCaptureSource]:
        module = self._module()
        devices = module.query_devices()
        host_apis = module.query_hostapis()
        default_device = module.default.device
        default_input = int(default_device[0] if isinstance(default_device, (list, tuple)) else -1)
        sources: list[AudioCaptureSource] = []
        for index, device in enumerate(devices):
            channels = int(device["max_input_channels"])
            if channels <= 0:
                continue
            name = str(device["name"])
            lowered = name.casefold()
            is_system = any(hint in lowered for hint in _SYSTEM_SOURCE_HINTS)
            kind = "system" if is_system else ("interface" if channels > 2 else "microphone")
            sources.append(
                AudioCaptureSource(
                    id=f"sounddevice:{index}",
                    name=name,
                    kind=kind,
                    backend=self.name,
                    host_api=str(host_apis[int(device["hostapi"])]["name"]),
                    channels=channels,
                    sample_rate=int(float(device["default_samplerate"])),
                    is_default=index == default_input,
                    is_loopback=is_system,
                )
            )
        return sorted(
            sources,
            key=lambda source: (
                0 if source.is_default else 1,
                0 if source.kind == "system" else 1,
                source.name.casefold(),
            ),
        )

    def probe_level(self, source_id: str) -> float:
        with self._lock:
            if (
                self._state == "recording"
                and self._source is not None
                and self._source.id == source_id
            ):
                return self._level
            source = next(
                (candidate for candidate in self.list_sources() if candidate.id == source_id),
                None,
            )
            if source is None:
                raise ValidationError("The audio source is no longer available")
            module = self._module()
            channels = max(1, min(source.channels, 2))
            frames = min(4096, max(512, round(source.sample_rate * 0.08)))
            stream: Any | None = None
            started = False
            try:
                stream = module.RawInputStream(
                    samplerate=source.sample_rate,
                    blocksize=frames,
                    device=int(source_id.split(":", maxsplit=1)[1]),
                    channels=channels,
                    dtype="int16",
                )
                stream.start()
                started = True
                data, _overflowed = stream.read(frames)
                return _pcm_level(bytes(data))
            except Exception as error:
                raise CapabilityUnavailableError(
                    f"Could not monitor {source.name}: {error}"
                ) from error
            finally:
                if stream is not None:
                    if started:
                        stream.stop()
                    stream.close()

    def start(
        self,
        *,
        session_id: str,
        source_id: str,
        destination: Path,
    ) -> CaptureStatus:
        with self._lock:
            if self._state in {"recording", "paused"}:
                raise ValidationError("Another live capture is already active")
            source = next(
                (candidate for candidate in self.list_sources() if candidate.id == source_id),
                None,
            )
            if not source:
                raise ValidationError("The selected audio source is no longer available")
            module = self._module()
            index = int(source_id.split(":", maxsplit=1)[1])
            channels = max(1, min(source.channels, 2))
            destination.parent.mkdir(parents=True, exist_ok=True)
            # Kept open for the lifetime of the asynchronous input stream.
            writer = wave.open(str(destination), "wb")  # noqa: SIM115
            writer.setnchannels(channels)
            writer.setsampwidth(2)
            writer.setframerate(source.sample_rate)
            self._reset_frame_buffer(source.sample_rate, channels)

            def callback(
                input_data: Any,
                frames: int,
                time_info: Any,
                status: Any,
            ) -> None:
                del frames, time_info, status
                raw = bytes(input_data)
                writer = self._wave
                if writer is not None and self._state == "recording":
                    writer.writeframesraw(raw)
                    self._buffer_frames(raw, channels)
                    self._level = _pcm_level(raw)

            try:
                stream = module.RawInputStream(
                    samplerate=source.sample_rate,
                    blocksize=1024,
                    device=index,
                    channels=channels,
                    dtype="int16",
                    callback=callback,
                )
                stream.start()
            except Exception:
                writer.close()
                destination.unlink(missing_ok=True)
                raise

            now = time.monotonic()
            self._stream = stream
            self._wave = writer
            self._session_id = session_id
            self._source = source
            self._destination = destination
            self._state = "recording"
            self._active_started_at = now
            self._active_seconds = 0.0
            self._level = 0.0
            return self._status_unlocked()

    def status(self) -> CaptureStatus | None:
        with self._lock:
            return self._status_unlocked() if self._session_id else None

    def drain_frames(self) -> AudioFrameBatch | None:
        with self._buffer_lock:
            if not self._pending_pcm or not self._buffer_sample_rate:
                return None
            data = bytes(self._pending_pcm)
            start_frame = self._pending_start_frame
            frame_width = 2 * self._buffer_channels
            frame_count = len(data) // frame_width
            self._pending_pcm.clear()
            self._pending_start_frame = start_frame + frame_count
            return AudioFrameBatch(
                pcm_s16le=data,
                sample_rate=self._buffer_sample_rate,
                channels=self._buffer_channels,
                start_frame=start_frame,
                end_frame=start_frame + frame_count,
            )

    def pause(self) -> CaptureStatus:
        with self._lock:
            if self._state != "recording" or self._stream is None:
                raise ValidationError("Live capture is not recording")
            self._active_seconds += time.monotonic() - self._active_started_at
            self._stream.stop()
            self._state = "paused"
            self._level = 0.0
            return self._status_unlocked()

    def resume(self) -> CaptureStatus:
        with self._lock:
            if self._state != "paused" or self._stream is None:
                raise ValidationError("Live capture is not paused")
            self._stream.start()
            self._active_started_at = time.monotonic()
            self._state = "recording"
            return self._status_unlocked()

    def stop(self) -> CapturedAudio:
        with self._lock:
            if self._state not in {"recording", "paused"}:
                raise ValidationError("No live capture is active")
            if self._state == "recording":
                self._active_seconds += time.monotonic() - self._active_started_at
            assert self._stream is not None
            assert self._wave is not None
            assert self._source is not None
            assert self._destination is not None
            self._stream.stop()
            self._stream.close()
            self._wave.close()
            result = CapturedAudio(
                path=self._destination,
                source=self._source,
                duration_ms=max(0, round(self._active_seconds * 1000)),
                sample_rate=self._source.sample_rate,
                channels=max(1, min(self._source.channels, 2)),
            )
            self._clear_unlocked()
            return result

    def shutdown(self) -> None:
        with self._lock:
            if self._state in {"recording", "paused"}:
                try:
                    self.stop()
                except Exception:
                    self._clear_unlocked()

    def _status_unlocked(self) -> CaptureStatus:
        assert self._session_id is not None
        assert self._source is not None
        assert self._destination is not None
        seconds = self._active_seconds
        if self._state == "recording":
            seconds += time.monotonic() - self._active_started_at
        return CaptureStatus(
            session_id=self._session_id,
            state=self._state,
            source=self._source,
            destination=self._destination,
            elapsed_ms=max(0, round(seconds * 1000)),
            level=self._level,
        )

    def _clear_unlocked(self) -> None:
        self._stream = None
        self._wave = None
        self._session_id = None
        self._source = None
        self._destination = None
        self._state = "idle"
        self._level = 0.0

    def _reset_frame_buffer(self, sample_rate: int, channels: int) -> None:
        with self._buffer_lock:
            self._pending_pcm.clear()
            self._pending_start_frame = 0
            self._total_frames = 0
            self._buffer_sample_rate = sample_rate
            self._buffer_channels = channels

    def _buffer_frames(self, data: bytes, channels: int) -> None:
        frame_width = 2 * channels
        with self._buffer_lock:
            if not self._pending_pcm:
                self._pending_start_frame = self._total_frames
            self._pending_pcm.extend(data)
            self._total_frames += len(data) // frame_width
            maximum_bytes = self._buffer_sample_rate * frame_width * 60
            overflow = len(self._pending_pcm) - maximum_bytes
            if overflow > 0:
                aligned = overflow - (overflow % frame_width)
                del self._pending_pcm[:aligned]
                self._pending_start_frame += aligned // frame_width


def _pcm_level(data: bytes) -> float:
    samples = array("h")
    samples.frombytes(data)
    if not samples:
        return 0.0
    return min(1.0, math.sqrt(max(abs(sample) for sample in samples) / 32768))
