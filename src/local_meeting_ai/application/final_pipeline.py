from __future__ import annotations

import logging
from typing import Any

from local_meeting_ai.domain.entities import Job
from local_meeting_ai.domain.enums import JobStatus, JobType
from local_meeting_ai.infrastructure.database.repositories import JobRepository
from local_meeting_ai.plugins.contracts import HookContext
from local_meeting_ai.plugins.manager import PluginManager

from .ai_services import DiarizationService, SummaryService

logger = logging.getLogger(__name__)


class FinalProcessingPipeline:
    """Explicit orchestration for work performed after final ASR.

    Live capture deliberately does not use this coordinator. This class replaces
    the former implicit queue callback while retaining the same user-visible
    ordering and graceful fallback when an optional stage is unavailable.
    """

    STAGES = (
        {
            "id": "final_transcription",
            "name": "Final transcription",
            "output": "timestamped_transcript",
        },
        {
            "id": "diarization",
            "name": "Speaker diarization",
            "output": "speaker_turns",
            "optional": True,
        },
        {
            "id": "saved_voice_matching",
            "name": "Saved-voice matching",
            "output": "identified_speakers",
            "optional": True,
        },
        {
            "id": "analysis_filters",
            "name": "Filters and enrichers",
            "output": "derived_meeting_document",
            "optional": True,
        },
        {
            "id": "analysis",
            "name": "AI analysis",
            "output": "meeting_notes",
            "optional": True,
        },
    )

    HOOKS = (
        {"name": "final_transcription.completed", "kind": "action"},
        {"name": "final_transcription.failed", "kind": "action"},
        {"name": "final_transcription.cancelled", "kind": "action"},
        {"name": "diarization.completed", "kind": "action"},
        {"name": "diarization.failed", "kind": "action"},
        {"name": "diarization.cancelled", "kind": "action"},
        {"name": "analysis.before", "kind": "filter"},
        {"name": "analysis.after", "kind": "filter"},
        {"name": "analysis.completed", "kind": "action"},
        {"name": "analysis.failed", "kind": "action"},
        {"name": "analysis.cancelled", "kind": "action"},
        {"name": "pipeline.finished", "kind": "action"},
    )

    def __init__(
        self,
        *,
        jobs: JobRepository,
        diarization: DiarizationService,
        summaries: SummaryService,
        plugins: PluginManager,
    ) -> None:
        self.jobs = jobs
        self.diarization = diarization
        self.summaries = summaries
        self.plugins = plugins

    def description(self) -> dict[str, Any]:
        return {
            "live_transcription_unchanged": True,
            "stages": list(self.STAGES),
            "hooks": list(self.HOOKS),
            "execution_targets": ["this-computer"],
            "remote_processing": "planned",
        }

    async def job_finished(self, job: Job, status: JobStatus) -> None:
        transcription_id = job.payload.get("transcription_id")
        if not isinstance(transcription_id, int):
            return
        options = _pipeline_options(job)
        context = HookContext(
            hook=_terminal_hook(job.job_type, status),
            pipeline_id=_pipeline_id(job, options),
            job_uuid=job.uuid,
            meeting_id=job.meeting_id,
            transcription_id=transcription_id,
            stage=_stage_id(job.job_type),
        )
        hook = context.hook
        if hook:
            await self.plugins.hooks.emit_action(
                hook,
                _job_event_payload(job, status),
                context,
            )

        if not bool(job.payload.get("postprocess")):
            return
        if job.job_type == JobType.TRANSCRIBE:
            await self._after_transcription(job, status, transcription_id, options)
        elif job.job_type == JobType.DIARIZE:
            await self._after_diarization(job, status, transcription_id, options)
        elif (
            job.job_type == JobType.SUMMARIZE
            and status
            in {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}
        ):
            await self._finish(job, status, transcription_id, options)

    async def _after_transcription(
        self,
        job: Job,
        status: JobStatus,
        transcription_id: int,
        options: dict[str, Any],
    ) -> None:
        if status != JobStatus.COMPLETED:
            if status in {JobStatus.FAILED, JobStatus.CANCELLED}:
                await self._finish(job, status, transcription_id, options)
            return
        run_diarization = bool(options.get("diarization", True))
        run_summary = bool(options.get("summary", True))
        completed_job = self.jobs.get(job.uuid)
        integrated_speakers = bool(
            completed_job
            and completed_job.result
            and completed_job.result.get("speaker_count", 0)
        )
        if integrated_speakers:
            logger.info("Separate diarization skipped; transcription supplied speaker turns")
            if run_summary:
                await self._start_summary(job, transcription_id, options)
            else:
                await self._finish(job, status, transcription_id, options)
            return
        if not run_diarization:
            if run_summary:
                await self._start_summary(job, transcription_id, options)
            else:
                await self._finish(job, status, transcription_id, options)
            return
        speaker_count = options.get("speaker_count")
        if not isinstance(speaker_count, int) or speaker_count < 1:
            speaker_count = None
        try:
            await self.diarization.start(
                transcription_id,
                speaker_count=speaker_count,
                postprocess_options=options,
                postprocess=True,
            )
            logger.info("Speaker identification queued by final pipeline")
        except Exception as error:
            logger.warning(
                "Speaker identification skipped; final pipeline will continue: %s",
                error,
            )
            failed_job = self.jobs.create(
                meeting_id=job.meeting_id,
                job_type=JobType.DIARIZE,
                payload={
                    "transcription_id": transcription_id,
                    "postprocess": True,
                    "postprocess_options": options,
                },
                message="Speaker identification could not start",
            )
            self.jobs.fail(failed_job.uuid, str(error) or type(error).__name__)
            if run_summary:
                await self._start_summary(job, transcription_id, options)
            else:
                await self._finish(job, JobStatus.FAILED, transcription_id, options)

    async def _after_diarization(
        self,
        job: Job,
        status: JobStatus,
        transcription_id: int,
        options: dict[str, Any],
    ) -> None:
        if status == JobStatus.CANCELLED:
            await self._finish(job, status, transcription_id, options)
            return
        if status not in {JobStatus.COMPLETED, JobStatus.FAILED}:
            return
        if status == JobStatus.FAILED:
            logger.warning("AI analysis will continue after diarization failure")
        if bool(options.get("summary", True)):
            await self._start_summary(job, transcription_id, options)
        else:
            await self._finish(job, status, transcription_id, options)

    async def _start_summary(
        self,
        preceding_job: Job,
        transcription_id: int,
        options: dict[str, Any],
    ) -> None:
        try:
            await self.summaries.start(
                transcription_id,
                postprocess=True,
                postprocess_options=options,
            )
            logger.info("Meeting AI analysis queued by final pipeline")
        except Exception as error:
            logger.warning("Meeting AI analysis skipped: %s", error)
            failed_job = self.jobs.create(
                meeting_id=preceding_job.meeting_id,
                job_type=JobType.SUMMARIZE,
                payload={
                    "transcription_id": transcription_id,
                    "postprocess": True,
                    "postprocess_options": options,
                },
                message="Meeting AI analysis could not start",
            )
            self.jobs.fail(failed_job.uuid, str(error) or type(error).__name__)
            await self._finish(
                failed_job,
                JobStatus.FAILED,
                transcription_id,
                options,
            )

    async def _finish(
        self,
        job: Job,
        status: JobStatus,
        transcription_id: int,
        options: dict[str, Any],
    ) -> None:
        context = HookContext(
            hook="pipeline.finished",
            pipeline_id=_pipeline_id(job, options),
            job_uuid=job.uuid,
            meeting_id=job.meeting_id,
            transcription_id=transcription_id,
            stage="completed",
        )
        await self.plugins.hooks.emit_action(
            "pipeline.finished",
            {
                "pipeline_id": context.pipeline_id,
                "status": status.value,
                "meeting_id": job.meeting_id,
                "transcription_id": transcription_id,
                "options": options,
            },
            context,
        )
        logger.info(
            "Final processing pipeline %s finished with status %s",
            context.pipeline_id or "untracked",
            status.value,
        )


def _pipeline_options(job: Job) -> dict[str, Any]:
    raw = job.payload.get("postprocess_options")
    options = dict(raw) if isinstance(raw, dict) else {}
    options.setdefault("pipeline_id", job.payload.get("pipeline_id") or job.uuid)
    return options


def _pipeline_id(job: Job, options: dict[str, Any]) -> str | None:
    raw = options.get("pipeline_id") or job.payload.get("pipeline_id")
    return str(raw) if raw else job.uuid


def _stage_id(job_type: JobType) -> str:
    return {
        JobType.TRANSCRIBE: "final_transcription",
        JobType.DIARIZE: "diarization",
        JobType.SUMMARIZE: "analysis",
    }.get(job_type, job_type.value)


def _terminal_hook(job_type: JobType, status: JobStatus) -> str:
    stage = _stage_id(job_type)
    if stage not in {"final_transcription", "diarization", "analysis"}:
        return ""
    terminal = {
        JobStatus.COMPLETED: "completed",
        JobStatus.FAILED: "failed",
        JobStatus.CANCELLED: "cancelled",
    }.get(status)
    if terminal is None:
        return ""
    return f"{stage}.{terminal}"


def _job_event_payload(job: Job, status: JobStatus) -> dict[str, Any]:
    return {
        "job_uuid": job.uuid,
        "job_type": job.job_type.value,
        "status": status.value,
        "meeting_id": job.meeting_id,
        "transcription_id": job.payload.get("transcription_id"),
    }
