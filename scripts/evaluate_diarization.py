"""Benchmark installed local diarization engines outside the web app.

The evaluator owns a separate process and one explicit orchestration thread.
It unloads the diarizer before and after every provider pass, checkpoints every
phase to JSON, and writes a readable speaker timeline for each completed pass.
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
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from local_meeting_ai.adapters.diarization.diarize_cpu import DiarizeCpuEngine
from local_meeting_ai.adapters.diarization.pyannote_community import (
    PyannoteCommunityDiarizationEngine,
)
from local_meeting_ai.adapters.diarization.sherpa_onnx import SherpaOnnxDiarizationEngine
from local_meeting_ai.application.ai_services import DIARIZATION_DEFAULTS
from local_meeting_ai.config import AppSettings
from local_meeting_ai.domain.entities import DiarizationSegment
from local_meeting_ai.domain.protocols import DiarizationEngine
from local_meeting_ai.infrastructure.database.connection import Database
from local_meeting_ai.infrastructure.database.repositories import SettingsRepository
from local_meeting_ai.infrastructure.ffmpeg import FFmpegClient
from local_meeting_ai.paths import AppPaths

logger = logging.getLogger("meet2notes.diarization_evaluator")

PROVIDERS = ("cpu", "cuda")
ENGINE_CHOICES = ("sherpa-onnx", "diarize", "pyannote-community-1")


class EvaluationStore:
    """Persist an in-progress run and the comparison ledger atomically."""

    def __init__(self, results_dir: Path, run: dict[str, Any]) -> None:
        self.results_dir = results_dir
        self.run = run
        self.run_path = results_dir / f"{run['run_id']}.json"
        self.ledger_path = results_dir / "diarization-evaluations.json"
        self._lock = threading.Lock()

    def checkpoint(self) -> None:
        with self._lock:
            _export_timeline_files(self.run, self.results_dir)
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


class DiarizationEvaluationOrchestrator:
    """Run cold-load CPU/CUDA passes in a dedicated orchestration thread."""

    def __init__(
        self,
        *,
        input_files: list[Path],
        models_dir: Path,
        data_dir: Path,
        results_dir: Path,
        ffmpeg_path: Path | None,
        configured_values: dict[str, Any],
        num_speakers: int | None,
        engines: list[str],
        pyannote_token: str | None,
    ) -> None:
        self.input_files = input_files
        self.models_dir = models_dir
        self.data_dir = data_dir
        self.ffmpeg_path = ffmpeg_path
        self.configured_values = configured_values
        self.num_speakers = num_speakers
        self.engines = engines
        self.pyannote_token = pyannote_token
        run_id = (
            f"diarization-evaluation-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
            f"-{uuid.uuid4().hex[:8]}"
        )
        self.run: dict[str, Any] = {
            "schema_version": 1,
            "run_id": run_id,
            "status": "running",
            "started_at": _timestamp(),
            "completed_at": None,
            "orchestrator": {
                "type": "dedicated_thread",
                "thread_name": "diarization-evaluation-orchestrator",
                "process_id": os.getpid(),
            },
            "environment": _environment_snapshot(),
            "engines_requested": engines,
            "models_directory": str(models_dir),
            "data_directory": str(data_dir),
            "providers_requested": list(PROVIDERS),
            "saved_settings_used": configured_values,
            "num_speakers_override": num_speakers,
            "inputs": [],
            "evaluations": [],
        }
        self.store = EvaluationStore(results_dir, self.run)
        self.error: BaseException | None = None
        self.thread = threading.Thread(
            target=self._thread_main,
            name="diarization-evaluation-orchestrator",
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
        try:
            normalized_inputs: list[tuple[Path, Path, dict[str, Any]]] = []
            for source in self.input_files:
                normalized, input_record = await self._normalize_input(ffmpeg, source)
                self.run["inputs"].append(input_record)
                normalized_inputs.append((source, normalized, input_record))
            for engine_id in self.engines:
                engine = self._build_engine(engine_id)
                try:
                    self.run.setdefault("engine_capability_before", {})[engine_id] = (
                        engine.capability()
                    )
                    self.store.checkpoint()
                    for source, normalized, input_record in normalized_inputs:
                        for provider in PROVIDERS:
                            await self._evaluate_pass(
                                engine=engine,
                                source=source,
                                normalized_audio=normalized,
                                provider=provider,
                                audio_duration_ms=input_record["normalized_probe"].get(
                                    "duration_ms"
                                ),
                            )
                finally:
                    await asyncio.to_thread(engine.unload)
                    await asyncio.to_thread(engine.shutdown)
                    self.run.setdefault("engine_capability_after", {})[engine_id] = (
                        engine.capability()
                    )
                    self.store.checkpoint()
            self.run["status"] = "completed"
        finally:
            self.run["completed_at"] = _timestamp()
            self.store.checkpoint()

    def _build_engine(self, engine_id: str) -> DiarizationEngine:
        if engine_id == "sherpa-onnx":
            return SherpaOnnxDiarizationEngine(self.models_dir)
        if engine_id == "diarize":
            return DiarizeCpuEngine(self.models_dir)
        if engine_id == "pyannote-community-1":
            return PyannoteCommunityDiarizationEngine(
                self.models_dir,
                access_token=self.pyannote_token,
            )
        raise ValueError(f"Unsupported evaluation engine: {engine_id}")

    async def _normalize_input(
        self,
        ffmpeg: FFmpegClient,
        source: Path,
    ) -> tuple[Path, dict[str, Any]]:
        if not source.is_file():
            raise FileNotFoundError(f"Diarization evaluation input was not found: {source}")
        normalized_dir = self.data_dir / "temp" / "diarization-evaluator"
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

    async def _evaluate_pass(
        self,
        *,
        engine: DiarizationEngine,
        source: Path,
        normalized_audio: Path,
        provider: str,
        audio_duration_ms: int | None,
    ) -> None:
        config = {
            **DIARIZATION_DEFAULTS,
            **self.configured_values,
            "engine": engine.name,
            "provider": provider,
            "keep_model_loaded": True,
            "preload_on_start": False,
        }
        if self.num_speakers is not None:
            config["num_speakers"] = self.num_speakers
        evaluation: dict[str, Any] = {
            "evaluation_id": uuid.uuid4().hex,
            "status": "running",
            "source_path": str(source),
            "normalized_audio_path": str(normalized_audio),
            "engine": engine.name,
            "provider_requested": provider,
            "effective_config": config,
            "started_at": _timestamp(),
            "finished_at": None,
            "phases": [],
            "progress_events": [],
            "result_available_at": None,
            "segment_count": 0,
        }
        self.run["evaluations"].append(evaluation)
        self.store.checkpoint()

        capability = engine.capability()
        evaluation["engine_capability_before"] = capability
        skip_reason = _skip_reason(capability, provider)
        if skip_reason:
            evaluation["status"] = "skipped"
            evaluation["reason"] = skip_reason
            evaluation["finished_at"] = _timestamp()
            self.store.checkpoint()
            return

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

        try:
            await self._run_phase(
                evaluation,
                "unload_before",
                lambda: asyncio.to_thread(engine.unload),
            )
            await self._run_phase(
                evaluation,
                "model_load",
                lambda: engine.prepare(config, allow_model_download=False),
            )
            segments = await self._run_phase(
                evaluation,
                "diarization",
                lambda: engine.diarize(normalized_audio, config, progress, lambda: False),
            )
            evaluation["result_available_at"] = _timestamp()
            evaluation["time_to_result_ms"] = _elapsed_ms(started)
            evaluation["result"] = _serialize_result(segments, audio_duration_ms)
            evaluation["segment_count"] = len(segments)
            evaluation["status"] = "completed"
        except Exception as error:
            evaluation["status"] = "failed"
            evaluation["error"] = _error_payload(error)
        finally:
            try:
                await self._run_phase(
                    evaluation,
                    "unload_after",
                    lambda: asyncio.to_thread(engine.unload),
                )
            except Exception as error:
                evaluation["unload_error"] = _error_payload(error)
                if evaluation["status"] == "completed":
                    evaluation["status"] = "failed"
            evaluation["engine_capability_after"] = engine.capability()
            evaluation["finished_at"] = _timestamp()
            evaluation["total_duration_ms"] = _elapsed_ms(started)
            self.store.checkpoint()
            logger.info(
                "%s | %s | %s | %s ms",
                evaluation["status"].upper(),
                engine.name,
                provider,
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


class _PreferencesSnapshot:
    """Read saved settings when present without creating a database."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir

    def get_all(self) -> dict[str, Any]:
        database_path = self.data_dir / "app.db"
        if not database_path.is_file():
            return {}
        try:
            return SettingsRepository(Database(database_path)).get_all()
        except Exception as error:
            logger.warning("Could not read saved diarization settings: %s", error)
            return {}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Cold-load local diarization benchmark. Runs every selected engine on "
            "CPU and CUDA when its runtime supports it."
        )
    )
    parser.add_argument(
        "--input",
        type=Path,
        nargs="+",
        default=[Path("debate_ceuta.wav")],
        help="WAV or MP3 file(s) to benchmark (default: debate_ceuta.wav).",
    )
    parser.add_argument("--data-dir", type=Path, help="Meet2Notes data directory override.")
    parser.add_argument("--models-dir", type=Path, help="AI models directory override.")
    parser.add_argument(
        "--results-dir",
        type=Path,
        help="Persistent JSON destination (default: <data-dir>/benchmarks/diarization).",
    )
    parser.add_argument("--ffmpeg-path", type=Path, help="Path to FFmpeg executable.")
    parser.add_argument(
        "--num-speakers",
        type=int,
        help="Optional known speaker count; omit for the saved setting (normally auto).",
    )
    parser.add_argument(
        "--engines",
        nargs="+",
        choices=("all", *ENGINE_CHOICES),
        default=["all"],
        help="Diarization engines to evaluate (default: all).",
    )
    parser.add_argument(
        "--export-run",
        type=Path,
        help="Export readable speaker timelines from an evaluation JSON, then exit.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    if arguments.export_run:
        return _export_existing_run(arguments.export_run)
    if arguments.num_speakers is not None and arguments.num_speakers < 1:
        print("--num-speakers must be at least 1 when it is supplied.", file=sys.stderr)
        return 2

    settings = AppSettings()
    if arguments.data_dir:
        settings = settings.model_copy(update={"data_dir": arguments.data_dir})
    if arguments.models_dir:
        settings = settings.model_copy(update={"models_dir": arguments.models_dir})
    paths = AppPaths.from_settings(settings)
    data_dir = paths.root
    saved = _PreferencesSnapshot(data_dir).get_all()
    models_dir = _resolve_models_directory(paths, arguments.models_dir, saved)
    results_dir = (
        arguments.results_dir or data_dir / "benchmarks" / "diarization"
    ).resolve()
    results_dir.mkdir(parents=True, exist_ok=True)
    _configure_logging(results_dir)
    configured = saved.get("diarization", {})
    configured_values = configured if isinstance(configured, dict) else {}
    inputs = [path.expanduser().resolve() for path in arguments.input]
    logger.info("Starting diarization evaluation for %d input file(s)", len(inputs))
    logger.info("Persistent results directory: %s", results_dir)
    orchestrator = DiarizationEvaluationOrchestrator(
        input_files=inputs,
        models_dir=models_dir,
        data_dir=data_dir,
        results_dir=results_dir,
        ffmpeg_path=arguments.ffmpeg_path or settings.ffmpeg_path,
        configured_values=configured_values,
        num_speakers=arguments.num_speakers,
        engines=(
            list(ENGINE_CHOICES)
            if "all" in arguments.engines
            else list(arguments.engines)
        ),
        pyannote_token=settings.pyannote_token,
    )
    orchestrator.start()
    try:
        orchestrator.join()
    except BaseException as error:
        logger.error("Diarization evaluation stopped: %s", error)
        return 1
    print(f"Diarization evaluation completed: {orchestrator.store.run_path}")
    print(f"Comparison ledger: {orchestrator.store.ledger_path}")
    return 0


def _resolve_models_directory(
    paths: AppPaths,
    override: Path | None,
    settings: dict[str, Any],
) -> Path:
    if override:
        return override.expanduser().resolve()
    configured = settings.get("models_directory")
    if isinstance(configured, str) and configured.strip():
        return Path(configured).expanduser().resolve()
    return paths.models.resolve()


def _skip_reason(capability: dict[str, Any], provider: str) -> str | None:
    if not bool(capability.get("available")):
        return "The engine runtime is not installed."
    if not bool(capability.get("installed")):
        return "The required diarization models or isolated runtime are not installed."
    if provider not in capability.get("supported_providers", []):
        return f"The engine does not advertise the {provider} provider."
    if provider == "cuda" and not _cuda_available():
        return "CUDA is not available to this evaluator process."
    return None


def _serialize_result(
    segments: list[DiarizationSegment],
    audio_duration_ms: int | None,
) -> dict[str, Any]:
    serialized = [
        {
            "index": index,
            "start_ms": segment.start_ms,
            "end_ms": segment.end_ms,
            "duration_ms": max(0, segment.end_ms - segment.start_ms),
            "speaker": segment.speaker,
        }
        for index, segment in enumerate(segments)
    ]
    speaker_durations: dict[str, int] = {}
    for segment in serialized:
        label = f"Speaker {segment['speaker'] + 1}"
        speaker_durations[label] = speaker_durations.get(label, 0) + int(
            segment["duration_ms"]
        )
    covered_ms = sum(int(segment["duration_ms"]) for segment in serialized)
    return {
        "audio_duration_ms": audio_duration_ms,
        "segment_count": len(serialized),
        "speaker_count": len(speaker_durations),
        "speaker_durations_ms": speaker_durations,
        "total_segment_duration_ms": covered_ms,
        "coverage_ratio": (
            round(covered_ms / audio_duration_ms, 6) if audio_duration_ms else None
        ),
        "segments": serialized,
    }


def _export_existing_run(run_path: Path) -> int:
    document = _read_json(run_path.expanduser().resolve(), {})
    if not document.get("run_id"):
        print(f"No valid diarization evaluation JSON found: {run_path}", file=sys.stderr)
        return 1
    exported = _export_timeline_files(document, run_path.parent)
    print(f"Exported {len(exported)} speaker timeline(s) to {run_path.parent / 'timelines'}")
    return 0


def _export_timeline_files(run: dict[str, Any], results_dir: Path) -> list[Path]:
    """Write a timestamped, human-readable speaker timeline for each pass."""

    exported: list[Path] = []
    run_id = str(run.get("run_id") or "diarization-evaluation")
    for evaluation in run.get("evaluations", []):
        if evaluation.get("status") != "completed":
            continue
        result = evaluation.get("result")
        if not isinstance(result, dict) or not isinstance(result.get("segments"), list):
            continue
        source_stem = Path(str(evaluation.get("source_path") or "audio")).stem
        filename = "__".join(
            _safe_filename_part(value)
            for value in (
                run_id,
                source_stem,
                evaluation.get("engine") or "sherpa-onnx",
                evaluation.get("provider_requested") or "provider",
            )
        ) + ".txt"
        destination = results_dir / "timelines" / filename
        lines = [
            "Meet2Notes diarization evaluation timeline",
            f"Run: {run_id}",
            f"Source: {evaluation.get('source_path')}",
            f"Engine: {evaluation.get('engine')}",
            f"Provider: {evaluation.get('provider_requested')}",
            f"Total evaluation time: {evaluation.get('total_duration_ms')} ms",
            f"Detected speakers: {result.get('speaker_count')}",
            f"Speaker durations (ms): {result.get('speaker_durations_ms')}",
            "",
        ]
        for segment in result["segments"]:
            if not isinstance(segment, dict):
                continue
            start = _format_timestamp(int(segment.get("start_ms") or 0))
            end = _format_timestamp(int(segment.get("end_ms") or 0))
            speaker = int(segment.get("speaker") or 0) + 1
            lines.append(f"[{start} -> {end}] Speaker {speaker}")
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


def _cuda_available() -> bool:
    try:
        import torch

        return bool(torch.cuda.is_available())
    except Exception:
        return False


def _configure_logging(results_dir: Path) -> None:
    log_path = results_dir / "diarization-evaluator.log"
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
