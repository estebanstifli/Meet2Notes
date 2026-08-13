from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from local_meeting_ai.domain.entities import MediaProbe
from local_meeting_ai.domain.errors import (
    CapabilityUnavailableError,
    JobCancelledError,
    ValidationError,
)
from local_meeting_ai.domain.protocols import CancellationCheck


class FFmpegClient:
    def __init__(self, custom_path: Path | None = None) -> None:
        self.ffmpeg_path = self._resolve(custom_path, "ffmpeg")
        self.ffprobe_path = self._resolve_probe(custom_path)
        self._capability_cache: dict[str, Any] | None = None

    @staticmethod
    def _resolve(custom_path: Path | None, executable: str) -> str | None:
        if custom_path:
            resolved = custom_path.expanduser().resolve()
            return str(resolved) if resolved.is_file() else None
        return shutil.which(executable)

    def _resolve_probe(self, custom_path: Path | None) -> str | None:
        if custom_path:
            custom = custom_path.expanduser().resolve()
            suffix = custom.suffix
            candidate = custom.with_name(f"ffprobe{suffix}")
            return str(candidate) if candidate.is_file() else None
        return shutil.which("ffprobe")

    def capabilities(self) -> dict[str, Any]:
        if self._capability_cache is not None:
            return self._capability_cache
        version: str | None = None
        if self.ffmpeg_path:
            try:
                result = subprocess.run(
                    [self.ffmpeg_path, "-version"],
                    capture_output=True,
                    check=False,
                    text=True,
                    timeout=3,
                )
                version = result.stdout.splitlines()[0] if result.stdout else None
            except (OSError, subprocess.SubprocessError):
                version = None
        self._capability_cache = {
            "available": bool(self.ffmpeg_path and self.ffprobe_path),
            "ffmpeg_path": self.ffmpeg_path,
            "ffprobe_path": self.ffprobe_path,
            "version": version,
        }
        return self._capability_cache

    async def probe_media(self, path: Path) -> MediaProbe:
        if not self.ffprobe_path:
            raise CapabilityUnavailableError(
                "FFprobe was not found. Install FFmpeg or configure M2N_FFMPEG_PATH."
            )
        process = await asyncio.create_subprocess_exec(
            self.ffprobe_path,
            "-v",
            "error",
            "-show_entries",
            "format=duration,format_name,size:stream=codec_type,codec_name,"
            "sample_rate,channels,width,height",
            "-of",
            "json",
            str(path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=30)
        except TimeoutError as error:
            process.kill()
            await process.communicate()
            raise ValidationError("Media inspection timed out") from error

        if process.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace").strip()
            raise ValidationError(f"FFmpeg could not read this media file: {detail[:500]}")

        document = json.loads(stdout.decode("utf-8"))
        streams = document.get("streams", [])
        audio_streams = [stream for stream in streams if stream.get("codec_type") == "audio"]
        video_streams = [stream for stream in streams if stream.get("codec_type") == "video"]
        if not audio_streams and not video_streams:
            raise ValidationError("The file does not contain a readable audio or video stream")

        format_info = document.get("format", {})
        audio = audio_streams[0] if audio_streams else {}
        duration = _optional_float(format_info.get("duration"))
        size = _optional_int(format_info.get("size"))
        probe = MediaProbe(
            format_name=str(format_info.get("format_name", "unknown")),
            duration_ms=round(duration * 1000) if duration is not None else None,
            size_bytes=size,
            sample_rate=_optional_int(audio.get("sample_rate")),
            channels=_optional_int(audio.get("channels")),
            has_audio=bool(audio_streams),
            has_video=bool(video_streams),
            metadata={
                "format": format_info.get("format_name"),
                "streams": streams,
            },
        )
        return probe

    async def probe_as_dict(self, path: Path) -> dict[str, Any]:
        return asdict(await self.probe_media(path))

    async def normalize_for_transcription(
        self,
        source: Path,
        destination: Path,
        *,
        sample_rate: int = 16000,
        channels: int = 1,
        is_cancelled: CancellationCheck | None = None,
    ) -> MediaProbe:
        if not self.ffmpeg_path:
            raise CapabilityUnavailableError(
                "FFmpeg was not found. Install FFmpeg or configure M2N_FFMPEG_PATH."
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.unlink(missing_ok=True)
        process = await asyncio.create_subprocess_exec(
            self.ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-y",
            "-i",
            str(source),
            "-map",
            "0:a:0",
            "-vn",
            "-ac",
            str(channels),
            "-ar",
            str(sample_rate),
            "-c:a",
            "pcm_s16le",
            str(destination),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        communicate = asyncio.create_task(process.communicate())
        started = time.monotonic()
        while not communicate.done():
            if is_cancelled and is_cancelled():
                process.kill()
                await communicate
                destination.unlink(missing_ok=True)
                raise JobCancelledError("Audio normalization was cancelled")
            if time.monotonic() - started > 4 * 60 * 60:
                process.kill()
                await communicate
                destination.unlink(missing_ok=True)
                raise ValidationError("Audio normalization exceeded the four-hour limit")
            await asyncio.sleep(0.2)

        _, stderr = await communicate
        if process.returncode != 0:
            destination.unlink(missing_ok=True)
            detail = stderr.decode("utf-8", errors="replace").strip()
            raise ValidationError(f"FFmpeg could not normalize this media: {detail[:500]}")
        return await self.probe_media(destination)

    async def export_audio_ranges(
        self,
        source: Path,
        destination: Path,
        ranges: list[tuple[int, int]],
        *,
        output_format: str,
    ) -> None:
        if not self.ffmpeg_path:
            raise CapabilityUnavailableError(
                "FFmpeg is required to export speaker audio"
            )
        if output_format not in {"wav", "mp3"}:
            raise ValidationError("Speaker audio can only be exported as WAV or MP3")
        merged = _merge_audio_ranges(ranges)
        if not merged:
            raise ValidationError("This speaker has no audio turns to export")

        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.unlink(missing_ok=True)
        filter_script = destination.with_suffix(f".{output_format}.filter.txt")
        filters = []
        labels = []
        for index, (start_ms, end_ms) in enumerate(merged):
            label = f"a{index}"
            labels.append(f"[{label}]")
            filters.append(
                f"[0:a]atrim=start={start_ms / 1000:.3f}:end={end_ms / 1000:.3f},"
                f"asetpts=PTS-STARTPTS[{label}]"
            )
        filters.append(
            f"{''.join(labels)}concat=n={len(labels)}:v=0:a=1[out]"
        )
        filter_script.write_text(";\n".join(filters), encoding="utf-8")
        codec = ["-c:a", "pcm_s16le"] if output_format == "wav" else [
            "-c:a",
            "libmp3lame",
            "-q:a",
            "2",
        ]
        process = await asyncio.create_subprocess_exec(
            self.ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-y",
            "-i",
            str(source),
            "-filter_complex_script",
            str(filter_script),
            "-map",
            "[out]",
            *codec,
            str(destination),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(process.communicate(), timeout=4 * 60 * 60)
        except TimeoutError as error:
            process.kill()
            await process.communicate()
            destination.unlink(missing_ok=True)
            raise ValidationError("Speaker audio export timed out") from error
        finally:
            filter_script.unlink(missing_ok=True)
        if process.returncode != 0:
            destination.unlink(missing_ok=True)
            detail = stderr.decode("utf-8", errors="replace").strip()
            raise ValidationError(f"FFmpeg could not export speaker audio: {detail[:500]}")


def _optional_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _merge_audio_ranges(
    ranges: list[tuple[int, int]],
    *,
    join_gap_ms: int = 180,
) -> list[tuple[int, int]]:
    ordered = sorted(
        (max(0, int(start)), int(end))
        for start, end in ranges
        if int(end) > int(start)
    )
    merged: list[tuple[int, int]] = []
    for start, end in ordered:
        if merged and start <= merged[-1][1] + join_gap_ms:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged
