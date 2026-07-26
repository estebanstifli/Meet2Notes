from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from local_meeting_ai.domain.entities import Job, Summary
from local_meeting_ai.domain.enums import JobType
from local_meeting_ai.domain.errors import (
    CapabilityUnavailableError,
    NotFoundError,
    ValidationError,
)
from local_meeting_ai.domain.protocols import (
    DiarizationEngine,
    ProgressReporter,
    SummaryEngine,
)
from local_meeting_ai.infrastructure.database.repositories import (
    JobRepository,
    RecordingRepository,
    SettingsRepository,
    SummaryRepository,
    TranscriptionRepository,
)
from local_meeting_ai.infrastructure.jobs import JobContext, LocalJobQueue

DIARIZATION_DEFAULTS: dict[str, Any] = {
    "engine": "sherpa-onnx",
    "segmentation_model": "pyannote-3.0",
    "embedding_model": "3d-speaker",
    "quantized_segmentation": True,
    "provider": "cpu",
    "num_threads": 2,
    "num_speakers": -1,
    "cluster_threshold": 0.5,
    "min_duration_on": 0.3,
    "min_duration_off": 0.5,
    "minimum_overlap_ratio": 0.15,
    "debug": False,
    "keep_model_loaded": True,
}

SUMMARY_DEFAULTS: dict[str, Any] = {
    "provider": "local",
    "local_runtime": "managed-llama-cpp",
    "model": "LiquidAI/LFM2.5-1.2B-Instruct-GGUF",
    "model_file": "LFM2.5-1.2B-Instruct-Q4_K_M.gguf",
    "context_length": 16384,
    "batch_size": 512,
    "micro_batch_size": 128,
    "threads": 0,
    "batch_threads": 0,
    "max_output_tokens": 1024,
    "temperature": 0.2,
    "top_p": 0.9,
    "top_k": 40,
    "min_p": 0.05,
    "repeat_penalty": 1.1,
    "seed": -1,
    "gpu_layers": -1,
    "main_gpu": 0,
    "split_mode": "layer",
    "use_mmap": True,
    "use_mlock": False,
    "offload_kqv": True,
    "flash_attention": True,
    "numa": False,
    "keep_model_loaded": True,
    "system_prompt": (
        "You are a precise meeting analyst. Summarize only information "
        "present in the transcript. Write in the transcript language."
    ),
}


def configured_values(
    preferences: SettingsRepository,
    key: str,
    defaults: dict[str, Any],
) -> dict[str, Any]:
    configured = preferences.get_all().get(key, {})
    return {**defaults, **(configured if isinstance(configured, dict) else {})}


class DiarizationService:
    def __init__(
        self,
        *,
        engine: DiarizationEngine,
        recordings: RecordingRepository,
        transcriptions: TranscriptionRepository,
        jobs: JobRepository,
        preferences: SettingsRepository,
        queue: LocalJobQueue,
    ) -> None:
        self.engine = engine
        self.recordings = recordings
        self.transcriptions = transcriptions
        self.jobs = jobs
        self.preferences = preferences
        self.queue = queue

    def capability(self) -> dict[str, Any]:
        return self.engine.capability()

    async def preload_default(self) -> None:
        config = configured_values(
            self.preferences,
            "diarization",
            DIARIZATION_DEFAULTS,
        )
        if not config["keep_model_loaded"] or not self.engine.capability()["installed"]:
            return
        await self.engine.prepare(config, allow_model_download=False)

    async def start(self, transcription_id: int) -> Job:
        transcription = self.transcriptions.get(transcription_id)
        if not transcription:
            raise NotFoundError("Transcription not found")
        if transcription.status != "completed":
            raise ValidationError("Complete the transcription before diarization")
        normalized = self.recordings.latest_for_role(
            transcription.meeting_id,
            "normalized",
        )
        if not normalized:
            raise ValidationError(
                "The normalized transcription audio is not available"
            )
        capability = self.engine.capability()
        if not capability["available"] or not capability["installed"]:
            raise CapabilityUnavailableError(
                "Install sherpa-onnx and its diarization models in Settings first"
            )
        job = self.jobs.create(
            meeting_id=transcription.meeting_id,
            job_type=JobType.DIARIZE,
            payload={
                "transcription_id": transcription.id,
                "recording_id": normalized.id,
            },
            message="Waiting to identify speakers",
        )
        await self.queue.submit(job.uuid)
        return job

    async def process(self, job: Job, context: JobContext) -> dict[str, Any]:
        transcription_id = job.payload.get("transcription_id")
        recording_id = job.payload.get("recording_id")
        if not isinstance(transcription_id, int) or not isinstance(recording_id, int):
            raise ValidationError("Diarization job payload is incomplete")
        transcription = self.transcriptions.get(transcription_id)
        recording = self.recordings.get(recording_id)
        if not transcription or not recording:
            raise NotFoundError("The diarization source no longer exists")
        config = configured_values(
            self.preferences,
            "diarization",
            DIARIZATION_DEFAULTS,
        )

        def is_cancelled() -> bool:
            current = self.jobs.get(job.uuid)
            return current is None or current.cancel_requested

        def progress(value: float, message: str) -> None:
            self.jobs.update_progress(job.uuid, value * 0.9, message)

        turns = await self.engine.diarize(
            Path(recording.local_path),
            config,
            cast(ProgressReporter, progress),
            is_cancelled,
        )
        await context.update(0.94, "Assigning speakers to transcript segments")
        assigned = self.transcriptions.assign_diarization(
            meeting_id=transcription.meeting_id,
            transcription_id=transcription.id,
            diarization=turns,
            minimum_overlap_ratio=float(config["minimum_overlap_ratio"]),
        )
        return {
            "transcription_id": transcription.id,
            "speaker_count": len({turn.speaker for turn in turns}),
            "turn_count": len(turns),
            "assigned_segments": assigned,
        }


class SummaryService:
    def __init__(
        self,
        *,
        engine: SummaryEngine,
        summaries: SummaryRepository,
        transcriptions: TranscriptionRepository,
        jobs: JobRepository,
        preferences: SettingsRepository,
        queue: LocalJobQueue,
    ) -> None:
        self.engine = engine
        self.summaries = summaries
        self.transcriptions = transcriptions
        self.jobs = jobs
        self.preferences = preferences
        self.queue = queue

    def capability(self) -> dict[str, Any]:
        return self.engine.capability()

    async def preload_default(self) -> None:
        config = configured_values(
            self.preferences,
            "summary_engine",
            SUMMARY_DEFAULTS,
        )
        if (
            config["provider"] != "local"
            or not config["keep_model_loaded"]
            or not self.engine.capability()["installed"]
        ):
            return
        await self.engine.prepare(config, allow_model_download=False)

    async def start(self, transcription_id: int) -> tuple[Summary, Job]:
        transcription = self.transcriptions.get(transcription_id)
        if not transcription:
            raise NotFoundError("Transcription not found")
        if transcription.status != "completed":
            raise ValidationError("Complete the transcription before summarizing")
        config = configured_values(
            self.preferences,
            "summary_engine",
            SUMMARY_DEFAULTS,
        )
        if config["provider"] == "disabled":
            raise CapabilityUnavailableError(
                "Enable the LFM2.5 summary provider in Settings first"
            )
        if config["provider"] == "local":
            capability = self.engine.capability()
            if not capability["available"] or not capability["installed"]:
                raise CapabilityUnavailableError(
                    "Install llama.cpp and LFM2.5 Q4_K_M in Settings first"
                )
        summary = self.summaries.create(
            meeting_id=transcription.meeting_id,
            transcription_id=transcription.id,
            provider=str(config["provider"]),
            model=str(config["model"]),
        )
        job = self.jobs.create(
            meeting_id=transcription.meeting_id,
            job_type=JobType.SUMMARIZE,
            payload={
                "summary_id": summary.id,
                "transcription_id": transcription.id,
            },
            message="Waiting to generate the meeting summary",
        )
        await self.queue.submit(job.uuid)
        return summary, job

    async def process(self, job: Job, context: JobContext) -> dict[str, Any]:
        summary_id = job.payload.get("summary_id")
        transcription_id = job.payload.get("transcription_id")
        if not isinstance(summary_id, int) or not isinstance(transcription_id, int):
            raise ValidationError("Summary job payload is incomplete")
        segments = self.transcriptions.segments(transcription_id)
        transcription = self.transcriptions.get(transcription_id)
        if not transcription:
            raise NotFoundError("The transcription no longer exists")
        if not segments:
            raise ValidationError("The transcription has no text to summarize")
        transcript = "\n".join(
            f"[{segment.start_ms / 1000:.1f}s] {segment.text}"
            for segment in segments
        )
        config = configured_values(
            self.preferences,
            "summary_engine",
            SUMMARY_DEFAULTS,
        )
        config["response_language"] = transcription.language

        def is_cancelled() -> bool:
            current = self.jobs.get(job.uuid)
            return current is None or current.cancel_requested

        def progress(value: float, message: str) -> None:
            self.jobs.update_progress(job.uuid, value * 0.95, message)

        self.summaries.mark_running(summary_id)
        try:
            result = await self.engine.summarize(
                transcript,
                config,
                cast(ProgressReporter, progress),
                is_cancelled,
            )
            completed = self.summaries.complete(
                summary_id,
                result.content_markdown,
                {
                    "prompt_tokens": result.prompt_tokens,
                    "completion_tokens": result.completion_tokens,
                },
            )
            if not completed:
                raise NotFoundError("The summary no longer exists")
            return {
                "summary_id": summary_id,
                "prompt_tokens": result.prompt_tokens,
                "completion_tokens": result.completion_tokens,
            }
        except Exception:
            self.summaries.fail(summary_id)
            raise
