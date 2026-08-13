"""Benchmark Meet2Notes ASR engines without using the web application's workers.

Each benchmark is run by an orchestration thread in this separate Python
process.  The runner unloads every local ASR model before and after each pass,
so timings measure a cold model load followed by one inference pass.  Results
are checkpointed into permanent JSON files for later comparisons.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import platform
import sys
import threading
import time
import traceback
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from local_meeting_ai.adapters.transcription.faster_whisper import FasterWhisperEngine
from local_meeting_ai.adapters.transcription.nvidia_asr import (
    build_nemotron_engine,
    build_parakeet_engine,
)
from local_meeting_ai.adapters.transcription.router import TranscriptionEngineRouter
from local_meeting_ai.adapters.transcription.vibevoice import VibeVoiceBitNetEngine
from local_meeting_ai.application.transcription_profiles import TranscriptionProfileCatalog
from local_meeting_ai.config import AppSettings
from local_meeting_ai.domain.entities import ModelProfile, SegmentDraft, TranscriptionEngineRequest
from local_meeting_ai.infrastructure.database.connection import Database
from local_meeting_ai.infrastructure.database.repositories import SettingsRepository
from local_meeting_ai.infrastructure.ffmpeg import FFmpegClient
from local_meeting_ai.paths import AppPaths

logger = logging.getLogger("meet2notes.asr_evaluator")

DEFAULT_PROFILE_IDS = (
    "default",
    "vibevoice-asr-bitnet",
    "nvidia-parakeet-tdt-0.6b-v3",
    "nvidia-nemotron-3.5-streaming-0.6b",
)
PASSES = (
    ("cpu", "auto"),
    ("cpu", "spanish"),
    ("cuda", "auto"),
    ("cuda", "spanish"),
)


class EvaluationStore:
    """Persist an in-progress run and the long-lived comparison ledger safely."""

    def __init__(self, results_dir: Path, run: dict[str, Any]) -> None:
        self.results_dir = results_dir
        self.run = run
        self.run_path = results_dir / f"{run['run_id']}.json"
        self.ledger_path = results_dir / "asr-evaluations.json"
        self._lock = threading.Lock()

    def checkpoint(self) -> None:
        with self._lock:
            _export_transcript_files(self.run, self.results_dir)
            _write_json_atomically(self.run_path, self.run)
            ledger = _read_json(self.ledger_path, {"schema_version": 1, "runs": []})
            runs = ledger.setdefault("runs", [])
            if not isinstance(runs, list):
                runs = []
                ledger["runs"] = runs
            for index, previous in enumerate(runs):
                if previous.get("run_id") == self.run["run_id"]:
                    runs[index] = self.run
                    break
            else:
                runs.append(self.run)
            _write_json_atomically(self.ledger_path, ledger)


class AsrEvaluationOrchestrator:
    """Run ASR benchmarks from one explicit orchestration thread."""

    def __init__(
        self,
        *,
        input_files: list[Path],
        profiles: list[str],
        models_dir: Path,
        data_dir: Path,
        results_dir: Path,
        ffmpeg_path: Path | None,
    ) -> None:
        self.input_files = input_files
        self.profiles = profiles
        self.models_dir = models_dir
        self.data_dir = data_dir
        self.ffmpeg_path = ffmpeg_path
        started_at = _timestamp()
        run_id = (
            f"asr-evaluation-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
            f"-{uuid.uuid4().hex[:8]}"
        )
        self.run: dict[str, Any] = {
            "schema_version": 1,
            "run_id": run_id,
            "status": "running",
            "started_at": started_at,
            "completed_at": None,
            "orchestrator": {
                "type": "dedicated_thread",
                "thread_name": "asr-evaluation-orchestrator",
                "process_id": os.getpid(),
            },
            "environment": _environment_snapshot(),
            "models_directory": str(models_dir),
            "data_directory": str(data_dir),
            "requested_profiles": profiles,
            "passes": [
                {"device": device, "language_mode": language}
                for device, language in PASSES
            ],
            "inputs": [],
            "evaluations": [],
        }
        self.store = EvaluationStore(results_dir, self.run)
        self.error: BaseException | None = None
        self.thread = threading.Thread(
            target=self._thread_main,
            name="asr-evaluation-orchestrator",
            daemon=False,
        )

    def start(self) -> None:
        self.store.checkpoint()
        self.thread.start()

    def join(self) -> None:
        self.thread.join()
        if self.error is not None:
            raise self.error

    def _thread_main(self) -> None:
        try:
            asyncio.run(self._run())
        except BaseException as error:
            self.error = error
            self.run["status"] = "failed"
            self.run["fatal_error"] = _error_payload(error)
            self.run["completed_at"] = _timestamp()
            self.store.checkpoint()

    async def _run(self) -> None:
        ffmpeg = FFmpegClient(self.ffmpeg_path)
        router = _build_router(self.models_dir)
        try:
            available_profiles = {
                profile.id: profile
                for profile in TranscriptionProfileCatalog(
                    router,
                    _PreferencesSnapshot(self.data_dir),
                ).list()
            }
            self.run["engine_capabilities_before"] = router.capability().get("engines", {})
            self.store.checkpoint()

            for source in self.input_files:
                normalized, input_record = await self._normalize_input(ffmpeg, source)
                self.run["inputs"].append(input_record)
                self.store.checkpoint()
                for profile_id in self.profiles:
                    profile = available_profiles.get(profile_id)
                    if profile is None:
                        self._record_unknown_profile(source, profile_id)
                        continue
                    for device, language_mode in PASSES:
                        await self._evaluate_pass(
                            router=router,
                            source=source,
                            normalized_audio=normalized,
                            profile=profile,
                            device=device,
                            language_mode=language_mode,
                        )
            self.run["status"] = "completed"
        finally:
            await asyncio.to_thread(router.unload)
            await asyncio.to_thread(router.shutdown)
            self.run["engine_capabilities_after"] = router.capability().get("engines", {})
            self.run["completed_at"] = _timestamp()
            self.store.checkpoint()

    async def _normalize_input(
        self,
        ffmpeg: FFmpegClient,
        source: Path,
    ) -> tuple[Path, dict[str, Any]]:
        if not source.is_file():
            raise FileNotFoundError(f"ASR evaluation input was not found: {source}")
        normalized_dir = self.data_dir / "temp" / "asr-evaluator"
        normalized = normalized_dir / f"{self.run['run_id']}-{source.stem}-16khz-mono.wav"
        phase = _phase("normalization")
        source_probe = await ffmpeg.probe_as_dict(source)
        try:
            normalized_probe = await ffmpeg.normalize_for_transcription(
                source,
                normalized,
                sample_rate=16000,
                channels=1,
            )
        except Exception as error:
            _finish_phase(phase, error)
            raise
        _finish_phase(phase)
        return normalized, {
            "source_path": str(source),
            "source_size_bytes": source.stat().st_size,
            "source_probe": source_probe,
            "normalized_path": str(normalized),
            "normalized_probe": asdict(normalized_probe),
            "preparation": phase,
        }

    def _record_unknown_profile(self, source: Path, profile_id: str) -> None:
        self.run["evaluations"].append(
            {
                "status": "skipped",
                "source_path": str(source),
                "profile_id": profile_id,
                "reason": "The requested profile is not available in this installation.",
                "started_at": _timestamp(),
                "finished_at": _timestamp(),
                "phases": [],
            }
        )
        self.store.checkpoint()

    async def _evaluate_pass(
        self,
        *,
        router: TranscriptionEngineRouter,
        source: Path,
        normalized_audio: Path,
        profile: ModelProfile,
        device: str,
        language_mode: str,
    ) -> None:
        evaluation: dict[str, Any] = {
            "evaluation_id": uuid.uuid4().hex,
            "status": "running",
            "source_path": str(source),
            "normalized_audio_path": str(normalized_audio),
            "profile": _serialize_profile(profile),
            "engine": profile.engine,
            "device_requested": device,
            "language_mode": language_mode,
            "language_sent_to_engine": None if language_mode == "auto" else "es",
            "language_control": _language_control(profile.engine),
            "started_at": _timestamp(),
            "finished_at": None,
            "phases": [],
            "progress_events": [],
            "first_segment_at": None,
            "segment_count": 0,
        }
        self.run["evaluations"].append(evaluation)
        self.store.checkpoint()

        capability = router.capability_for(profile.engine)
        skip_reason = _skip_reason(profile, capability, device)
        if skip_reason:
            evaluation["status"] = "skipped"
            evaluation["reason"] = skip_reason
            evaluation["finished_at"] = _timestamp()
            self.store.checkpoint()
            return

        configured_profile = _configured_profile(profile, device)
        request = _request_for(
            audio_path=normalized_audio,
            profile=configured_profile,
            language=None if language_mode == "auto" else "es",
        )
        evaluation["effective_request"] = {
            "engine": request.engine,
            "model": request.model,
            "device": request.device,
            "compute_type": request.compute_type,
            "language": request.language,
            "task": request.task,
            "beam_size": request.beam_size,
            "vad_filter": request.vad_filter,
        }
        timeline_lock = threading.Lock()
        started = time.perf_counter()

        def progress(value: float, message: str) -> None:
            with timeline_lock:
                evaluation["progress_events"].append(
                    {
                        "at": _timestamp(),
                        "elapsed_ms": _elapsed_ms(started),
                        "progress": round(float(value), 4),
                        "message": str(message),
                    }
                )

        def segment_ready(_segment: SegmentDraft) -> None:
            with timeline_lock:
                if evaluation["first_segment_at"] is None:
                    evaluation["first_segment_at"] = _timestamp()
                    evaluation["time_to_first_segment_ms"] = _elapsed_ms(started)

        try:
            await self._run_phase(
                evaluation,
                "unload_before",
                lambda: asyncio.to_thread(router.unload),
            )
            await self._run_phase(
                evaluation,
                "model_load",
                lambda: router.prepare(
                    configured_profile,
                    allow_model_download=False,
                ),
            )
            result = await self._run_phase(
                evaluation,
                "inference",
                lambda: router.transcribe(
                    request,
                    progress,
                    lambda: False,
                    segment_ready,
                ),
            )
            evaluation["result"] = _serialize_result(result)
            evaluation["segment_count"] = len(result.segments)
            evaluation["status"] = "completed"
        except Exception as error:
            evaluation["status"] = "failed"
            evaluation["error"] = _error_payload(error)
        finally:
            try:
                await self._run_phase(
                    evaluation,
                    "unload_after",
                    lambda: asyncio.to_thread(router.unload),
                )
            except Exception as error:
                evaluation["unload_error"] = _error_payload(error)
                if evaluation["status"] == "completed":
                    evaluation["status"] = "failed"
            evaluation["finished_at"] = _timestamp()
            evaluation["total_duration_ms"] = _elapsed_ms(started)
            self.store.checkpoint()
            logger.info(
                "%s | %s | %s | %s | %s ms",
                evaluation["status"].upper(),
                profile.display_name,
                device,
                language_mode,
                evaluation["total_duration_ms"],
            )

    async def _run_phase(
        self,
        evaluation: dict[str, Any],
        name: str,
        operation: Callable[[], Awaitable[Any]],
    ) -> Any:
        phase = _phase(name)
        evaluation["phases"].append(phase)
        self.store.checkpoint()
        try:
            result = await operation()
        except Exception as error:
            _finish_phase(phase, error)
            self.store.checkpoint()
            raise
        _finish_phase(phase)
        self.store.checkpoint()
        return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Cold-load ASR benchmark. Each selected engine runs CPU/auto-language, "
            "CPU/Spanish, CUDA/auto-language and CUDA/Spanish passes."
        )
    )
    parser.add_argument(
        "--input",
        type=Path,
        nargs="+",
        default=[Path("debate_ceuta.wav")],
        help="WAV or MP3 file(s) to benchmark (default: debate_ceuta.wav).",
    )
    parser.add_argument(
        "--profile",
        nargs="+",
        default=list(DEFAULT_PROFILE_IDS),
        help=(
            "Profile IDs to test. Defaults to one Faster Whisper profile plus each "
            "available native ASR engine."
        ),
    )
    parser.add_argument("--data-dir", type=Path, help="Meet2Notes data directory override.")
    parser.add_argument("--models-dir", type=Path, help="AI models directory override.")
    parser.add_argument(
        "--results-dir",
        type=Path,
        help="Persistent JSON destination (default: <data-dir>/benchmarks/asr).",
    )
    parser.add_argument("--ffmpeg-path", type=Path, help="Path to FFmpeg executable.")
    parser.add_argument(
        "--export-run",
        type=Path,
        help="Export readable .txt transcripts from an existing evaluation JSON, then exit.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    if arguments.export_run:
        return _export_existing_run(arguments.export_run)
    settings = AppSettings()
    if arguments.data_dir:
        settings = settings.model_copy(update={"data_dir": arguments.data_dir})
    if arguments.models_dir:
        settings = settings.model_copy(update={"models_dir": arguments.models_dir})
    paths = AppPaths.from_settings(settings)
    data_dir = paths.root
    models_dir = _resolve_models_directory(paths, arguments.models_dir)
    results_dir = (arguments.results_dir or data_dir / "benchmarks" / "asr").resolve()
    results_dir.mkdir(parents=True, exist_ok=True)
    _configure_logging(results_dir)
    inputs = [path.expanduser().resolve() for path in arguments.input]
    logger.info("Starting ASR evaluation for %d input file(s)", len(inputs))
    logger.info("Persistent results directory: %s", results_dir)
    orchestrator = AsrEvaluationOrchestrator(
        input_files=inputs,
        profiles=list(arguments.profile),
        models_dir=models_dir,
        data_dir=data_dir,
        results_dir=results_dir,
        ffmpeg_path=arguments.ffmpeg_path or settings.ffmpeg_path,
    )
    orchestrator.start()
    try:
        orchestrator.join()
    except BaseException as error:
        logger.error("ASR evaluation stopped: %s", error)
        return 1
    print(f"ASR evaluation completed: {orchestrator.store.run_path}")
    print(f"Comparison ledger: {orchestrator.store.ledger_path}")
    return 0


def _build_router(models_dir: Path) -> TranscriptionEngineRouter:
    faster_whisper = FasterWhisperEngine(models_dir)
    return TranscriptionEngineRouter(
        {
            faster_whisper.name: faster_whisper,
            "vibevoice-asr-bitnet": VibeVoiceBitNetEngine(models_dir),
            "nvidia-parakeet": build_parakeet_engine(models_dir),
            "nvidia-nemotron": build_nemotron_engine(models_dir),
        }
    )


class _PreferencesSnapshot:
    """Read settings when present without creating a database during a benchmark."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir

    def get_all(self) -> dict[str, Any]:
        database_path = self.data_dir / "app.db"
        if not database_path.is_file():
            return {}
        try:
            database = Database(database_path)
            return SettingsRepository(database).get_all()
        except Exception as error:
            logger.warning("Could not read saved ASR settings: %s", error)
            return {}


def _resolve_models_directory(paths: AppPaths, override: Path | None) -> Path:
    if override:
        return override.expanduser().resolve()
    if paths.database.is_file():
        configured = _PreferencesSnapshot(paths.root).get_all().get("models_directory")
        if isinstance(configured, str) and configured.strip():
            return Path(configured).expanduser().resolve()
    return paths.models.resolve()


def _configured_profile(profile: ModelProfile, device: str) -> ModelProfile:
    compute_type = profile.compute_type
    if profile.engine == "faster-whisper":
        compute_type = "int8" if device == "cpu" else "float16"
    return replace(
        profile,
        device=device,
        compute_type=compute_type,
        keep_model_loaded=True,
        num_workers=1,
    )


def _request_for(
    *,
    audio_path: Path,
    profile: ModelProfile,
    language: str | None,
) -> TranscriptionEngineRequest:
    return TranscriptionEngineRequest(
        audio_path=audio_path,
        model=profile.model,
        device=profile.device,
        compute_type=profile.compute_type,
        language=language,
        task="transcribe",
        beam_size=profile.beam_size,
        vad_filter=profile.vad_filter,
        allow_model_download=False,
        engine=profile.engine,
        device_index=profile.device_index,
        cpu_threads=profile.cpu_threads,
        num_workers=profile.num_workers,
        vad_min_silence_ms=profile.vad_min_silence_ms,
        word_timestamps=profile.word_timestamps,
        condition_on_previous_text=profile.condition_on_previous_text,
        keep_model_loaded=True,
    )


def _skip_reason(profile: ModelProfile, capability: dict[str, Any], device: str) -> str | None:
    if not profile.runtime_available:
        return "The optional runtime for this engine is not installed."
    if not profile.installed:
        return "The model files are not installed locally; the evaluator never downloads models."
    if not bool(capability.get("available", True)):
        return "The engine reported that its local runtime is unavailable."
    if device == "cuda" and profile.device == "cpu":
        return "This profile is CPU-only and cannot run a CUDA pass."
    if device == "cuda" and not bool(capability.get("cuda_available")):
        return "CUDA is unavailable for this engine in the evaluator process."
    return None


def _language_control(engine: str) -> str:
    if engine in {"faster-whisper", "nvidia-nemotron"}:
        return "sent_to_engine"
    return "engine_does_not_expose_an_explicit_language_parameter"


def _serialize_profile(profile: ModelProfile) -> dict[str, Any]:
    return asdict(profile)


def _serialize_result(result: Any) -> dict[str, Any]:
    segments = [_serialize_segment(segment) for segment in result.segments]
    return {
        "language": result.language,
        "language_probability": result.language_probability,
        "audio_duration_ms": result.duration_ms,
        "segment_count": len(segments),
        "transcript": "\n".join(segment["text"] for segment in segments).strip(),
        "segments": segments,
    }


def _serialize_segment(segment: SegmentDraft) -> dict[str, Any]:
    metadata = segment.metadata or {}
    return {
        "index": segment.index,
        "start_ms": segment.start_ms,
        "end_ms": segment.end_ms,
        "text": segment.text,
        "confidence": segment.confidence,
        "speaker": metadata.get("speaker"),
        "timestamped_words": len(metadata.get("words") or []),
    }


def _export_existing_run(run_path: Path) -> int:
    document = _read_json(run_path.expanduser().resolve(), {})
    if not document.get("run_id"):
        print(f"No valid ASR evaluation JSON found: {run_path}", file=sys.stderr)
        return 1
    exported = _export_transcript_files(document, run_path.parent)
    print(f"Exported {len(exported)} transcript file(s) to {run_path.parent / 'transcripts'}")
    return 0


def _export_transcript_files(run: dict[str, Any], results_dir: Path) -> list[Path]:
    """Write completed benchmark transcripts as easy-to-read timestamped text."""

    exported: list[Path] = []
    run_id = str(run.get("run_id") or "asr-evaluation")
    for evaluation in run.get("evaluations", []):
        if evaluation.get("status") != "completed":
            continue
        result = evaluation.get("result")
        if not isinstance(result, dict) or not isinstance(result.get("segments"), list):
            continue
        profile = evaluation.get("profile") or {}
        source_stem = Path(str(evaluation.get("source_path") or "audio")).stem
        filename = "__".join(
            _safe_filename_part(value)
            for value in (
                run_id,
                source_stem,
                profile.get("id") or evaluation.get("engine") or "engine",
                evaluation.get("device_requested") or "device",
                evaluation.get("language_mode") or "language",
            )
        ) + ".txt"
        destination = results_dir / "transcripts" / filename
        segments = result["segments"]
        lines = [
            "Meet2Notes ASR evaluation transcript",
            f"Run: {run_id}",
            f"Source: {evaluation.get('source_path')}",
            f"Engine: {evaluation.get('engine')}",
            f"Profile: {profile.get('display_name') or profile.get('id')}",
            f"Device: {evaluation.get('device_requested')}",
            f"Language mode: {evaluation.get('language_mode')}",
            f"Total evaluation time: {evaluation.get('total_duration_ms')} ms",
            "",
        ]
        for segment in segments:
            if not isinstance(segment, dict):
                continue
            start = _format_timestamp(int(segment.get("start_ms") or 0))
            end = _format_timestamp(int(segment.get("end_ms") or 0))
            speaker = segment.get("speaker")
            prefix = f"[{start} → {end}]"
            if speaker:
                prefix += f" [{speaker}]"
            lines.extend((f"{prefix} {str(segment.get('text') or '').strip()}", ""))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        exported.append(destination)
    return exported


def _safe_filename_part(value: object) -> str:
    raw = str(value)
    clean = "".join(
        character if character.isalnum() or character in "-_" else "-"
        for character in raw
    )
    return clean.strip("-_")[:80] or "value"


def _format_timestamp(milliseconds: int) -> str:
    hours, remainder = divmod(max(0, milliseconds), 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, milliseconds = divmod(remainder, 1_000)
    return f"{hours:02}:{minutes:02}:{seconds:02}.{milliseconds:03}"


def _environment_snapshot() -> dict[str, Any]:
    snapshot: dict[str, Any] = {
        "platform": platform.platform(),
        "python": sys.version,
        "processor": platform.processor() or platform.machine(),
        "logical_cpu_count": os.cpu_count(),
    }
    try:
        import torch

        snapshot["torch"] = {
            "version": str(torch.__version__),
            "cuda_build": torch.version.cuda,
            "cuda_available": bool(torch.cuda.is_available()),
            "cuda_device_count": int(torch.cuda.device_count()),
            "cuda_devices": [
                torch.cuda.get_device_name(index)
                for index in range(torch.cuda.device_count())
            ]
            if torch.cuda.is_available()
            else [],
        }
    except Exception as error:
        snapshot["torch_error"] = f"{type(error).__name__}: {error}"
    return snapshot


def _configure_logging(results_dir: Path) -> None:
    log_path = results_dir / "asr-evaluator.log"
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(console)
    root.addHandler(file_handler)


def _phase(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "started_at": _timestamp(),
        "finished_at": None,
        "duration_ms": None,
        "status": "running",
        "_started_monotonic": time.perf_counter(),
    }


def _finish_phase(phase: dict[str, Any], error: Exception | None = None) -> None:
    started = float(phase.pop("_started_monotonic"))
    phase["finished_at"] = _timestamp()
    phase["duration_ms"] = _elapsed_ms(started)
    if error is None:
        phase["status"] = "completed"
    else:
        phase["status"] = "failed"
        phase["error"] = _error_payload(error)


def _elapsed_ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)


def _timestamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def _error_payload(error: BaseException) -> dict[str, str]:
    return {
        "type": type(error).__name__,
        "message": str(error),
        "traceback": "".join(traceback.format_exception(error)).strip(),
    }


def _read_json(path: Path, fallback: dict[str, Any]) -> dict[str, Any]:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError):
        return fallback
    return loaded if isinstance(loaded, dict) else fallback


def _write_json_atomically(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


if __name__ == "__main__":
    raise SystemExit(main())
