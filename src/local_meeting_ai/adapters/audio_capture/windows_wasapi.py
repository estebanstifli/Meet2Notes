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


class WindowsWasapiCaptureBackend:
    """Windows capture through PortAudio with native WASAPI loopback endpoints."""

    name = "wasapi"

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._buffer_lock = threading.Lock()
        self._manager: Any | None = None
        self._stream: Any | None = None
        self._wave: wave.Wave_write | None = None
        self._session_id: str | None = None
        self._source: AudioCaptureSource | None = None
        self._destination: Path | None = None
        self._state = "idle"
        self._started_at = 0.0
        self._active_started_at = 0.0
        self._active_seconds = 0.0
        self._level = 0.0
        self._error: str | None = None
        self._pending_pcm = bytearray()
        self._pending_start_frame = 0
        self._total_frames = 0
        self._buffer_sample_rate = 0
        self._buffer_channels = 0

    @staticmethod
    def _module() -> Any:
        try:
            return importlib.import_module("pyaudiowpatch")
        except ImportError as error:
            raise CapabilityUnavailableError(
                'Native Windows capture is not installed. Run: pip install -e ".[capture]"'
            ) from error

    def capability(self) -> dict[str, object]:
        try:
            module = self._module()
            version = getattr(module, "__version__", "installed")
            sources = self.list_sources()
        except CapabilityUnavailableError as error:
            return {
                "available": False,
                "backend": self.name,
                "platform": "Windows",
                "native_api": "WASAPI",
                "reason": str(error),
                "supports_microphones": False,
                "supports_system_audio": False,
            }
        return {
            "available": True,
            "backend": self.name,
            "platform": "Windows",
            "native_api": "WASAPI",
            "library": f"PyAudioWPatch {version}",
            "supports_microphones": any(source.kind != "system" for source in sources),
            "supports_system_audio": any(source.kind == "system" for source in sources),
            "source_count": len(sources),
            "permission_required": False,
        }

    def list_sources(self) -> list[AudioCaptureSource]:
        # PortAudio/PyAudioWPatch device discovery is not thread-safe on
        # Windows. The dashboard and sidebar can request capabilities at the
        # same time, so serialize all native manager lifecycles.
        with self._lock:
            return self._list_sources_unlocked()

    def _list_sources_unlocked(self) -> list[AudioCaptureSource]:
        module = self._module()
        manager = module.PyAudio()
        try:
            try:
                default_input_info = manager.get_default_input_device_info()
                default_input = int(default_input_info["index"])
                default_input_name = _clean_device_name(
                    str(default_input_info["name"]),
                    False,
                ).casefold()
            except OSError:
                default_input = -1
                default_input_name = ""
            default_loopback = _default_loopback_index(manager)

            candidates: list[AudioCaptureSource] = []
            for device in manager.get_device_info_generator():
                channels = int(device.get("maxInputChannels", 0))
                if channels <= 0:
                    continue
                index = int(device["index"])
                is_loopback = bool(device.get("isLoopbackDevice", False))
                host = manager.get_host_api_info_by_index(int(device["hostApi"]))
                host_name = str(host.get("name", "Windows audio"))
                source_kind = "system" if is_loopback else (
                    "interface" if channels > 2 else "microphone"
                )
                clean_name = _clean_device_name(str(device["name"]), is_loopback)
                candidates.append(
                    AudioCaptureSource(
                        id=f"pyaudio:{index}",
                        name=clean_name,
                        kind=source_kind,
                        backend=self.name,
                        host_api=host_name,
                        channels=channels,
                        sample_rate=int(float(device["defaultSampleRate"])),
                        is_default=(
                            index in {default_input, default_loopback}
                            or (
                                not is_loopback
                                and clean_name.casefold() == default_input_name
                            )
                        ),
                        is_loopback=is_loopback,
                    )
                )
            sources = _prefer_native_sources(candidates)
            return sorted(
                sources,
                key=lambda source: (
                    0 if source.is_default else 1,
                    0 if source.host_api.upper() == "WINDOWS WASAPI" else 1,
                    source.name.casefold(),
                ),
            )
        finally:
            manager.terminate()

    def probe_level(self, source_id: str) -> float:
        with self._lock:
            if (
                self._state == "recording"
                and self._source is not None
                and self._source.id == source_id
            ):
                return self._level
            source = next(
                (
                    candidate
                    for candidate in self._list_sources_unlocked()
                    if candidate.id == source_id
                ),
                None,
            )
            if source is None:
                raise ValidationError("The audio source is no longer available")
            module = self._module()
            manager = module.PyAudio()
            stream: Any | None = None
            sample_ready = threading.Event()
            sampled_level = 0.0

            def callback(
                input_data: bytes,
                frame_count: int,
                time_info: dict[str, float],
                status_flags: int,
            ) -> tuple[None, int]:
                del frame_count, time_info, status_flags
                nonlocal sampled_level
                sampled_level = _pcm_level(input_data)
                sample_ready.set()
                return None, module.paComplete

            try:
                channels = max(1, min(source.channels, 2))
                frames = min(4096, max(512, round(source.sample_rate * 0.08)))
                stream = manager.open(
                    format=module.paInt16,
                    channels=channels,
                    rate=source.sample_rate,
                    input=True,
                    input_device_index=int(source_id.split(":", maxsplit=1)[1]),
                    frames_per_buffer=frames,
                    stream_callback=callback,
                    start=True,
                )
                if not sample_ready.wait(timeout=0.5):
                    raise TimeoutError("the device did not provide an audio sample")
                return sampled_level
            except Exception as error:
                raise CapabilityUnavailableError(
                    f"Could not monitor {source.name}: {error}"
                ) from error
            finally:
                if stream is not None:
                    if not stream.is_stopped():
                        stream.stop_stream()
                    stream.close()
                manager.terminate()

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
            manager = module.PyAudio()
            index = int(source_id.split(":", maxsplit=1)[1])
            channels = max(1, min(source.channels, 2))
            destination.parent.mkdir(parents=True, exist_ok=True)
            # Kept open for the lifetime of the asynchronous WASAPI stream.
            writer = wave.open(str(destination), "wb")  # noqa: SIM115
            writer.setnchannels(channels)
            writer.setsampwidth(module.get_sample_size(module.paInt16))
            writer.setframerate(source.sample_rate)
            self._reset_frame_buffer(source.sample_rate, channels)

            def callback(
                input_data: bytes,
                frame_count: int,
                time_info: dict[str, float],
                status_flags: int,
            ) -> tuple[bytes, int]:
                del frame_count, time_info, status_flags
                writer = self._wave
                if writer is not None and self._state == "recording":
                    writer.writeframesraw(input_data)
                    self._buffer_frames(input_data, channels)
                    self._level = _pcm_level(input_data)
                return input_data, module.paContinue

            try:
                stream = manager.open(
                    format=module.paInt16,
                    channels=channels,
                    rate=source.sample_rate,
                    input=True,
                    input_device_index=index,
                    frames_per_buffer=1024,
                    stream_callback=callback,
                    start=True,
                )
            except Exception:
                writer.close()
                manager.terminate()
                destination.unlink(missing_ok=True)
                raise

            now = time.monotonic()
            self._manager = manager
            self._stream = stream
            self._wave = writer
            self._session_id = session_id
            self._source = source
            self._destination = destination
            self._state = "recording"
            self._started_at = now
            self._active_started_at = now
            self._active_seconds = 0.0
            self._level = 0.0
            self._error = None
            return self._status_unlocked()

    def status(self) -> CaptureStatus | None:
        with self._lock:
            if self._session_id is None:
                return None
            return self._status_unlocked()

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
            self._stream.stop_stream()
            self._state = "paused"
            self._level = 0.0
            return self._status_unlocked()

    def resume(self) -> CaptureStatus:
        with self._lock:
            if self._state != "paused" or self._stream is None:
                raise ValidationError("Live capture is not paused")
            self._stream.start_stream()
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
            assert self._manager is not None
            assert self._source is not None
            assert self._destination is not None
            if not self._stream.is_stopped():
                self._stream.stop_stream()
            self._stream.close()
            self._wave.close()
            self._manager.terminate()
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
            error=self._error,
        )

    def _clear_unlocked(self) -> None:
        self._manager = None
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


def _clean_device_name(name: str, loopback: bool) -> str:
    clean = name.replace("[Loopback]", "").replace("(loopback)", "").strip()
    return f"{clean} · System audio" if loopback else clean


def _default_loopback_index(manager: Any) -> int:
    """Return the default loopback when Windows exposes a valid output device."""
    try:
        device = manager.get_default_wasapi_loopback()
        return int(device["index"])
    except (AttributeError, KeyError, OSError, TypeError, ValueError):
        # PyAudioWPatch raises ValueError when Windows' default WASAPI device
        # unexpectedly points to an input. Source enumeration is still valid;
        # only the default badge is unavailable.
        return -1


def _prefer_native_sources(
    candidates: list[AudioCaptureSource],
) -> list[AudioCaptureSource]:
    """Collapse PortAudio compatibility aliases and prefer native WASAPI endpoints."""
    selected: dict[tuple[str, str], AudioCaptureSource] = {}
    for source in candidates:
        key = (source.kind, source.name.casefold())
        current = selected.get(key)
        if current is None or _host_rank(source.host_api) < _host_rank(current.host_api):
            selected[key] = source
    native_inputs_exist = any(
        not source.is_loopback and _host_rank(source.host_api) == 0
        for source in selected.values()
    )
    compatibility_names = {
        "microsoft sound mapper - input",
        "primary sound capture driver",
    }
    return [
        source
        for source in selected.values()
        if not (
            native_inputs_exist
            and not source.is_loopback
            and source.name.casefold() in compatibility_names
        )
    ]


def _host_rank(host_api: str) -> int:
    normalized = host_api.casefold()
    if "wasapi" in normalized:
        return 0
    if "directsound" in normalized:
        return 1
    return 2


def _pcm_level(data: bytes) -> float:
    if not data:
        return 0.0
    samples = array("h")
    samples.frombytes(data)
    if not samples:
        return 0.0
    peak = max(abs(sample) for sample in samples)
    return min(1.0, math.sqrt(peak / 32768))
