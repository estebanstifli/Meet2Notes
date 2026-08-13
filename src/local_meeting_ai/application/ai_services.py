from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, cast

from local_meeting_ai.domain.entities import Job, Summary, SummaryTemplate
from local_meeting_ai.domain.enums import JobType
from local_meeting_ai.domain.errors import (
    CapabilityUnavailableError,
    NotFoundError,
    ValidationError,
)
from local_meeting_ai.domain.protocols import (
    DiarizationEngine,
    ProgressReporter,
    SpeakerProfileMatcher,
    SummaryEngine,
)
from local_meeting_ai.infrastructure.database.repositories import (
    JobRepository,
    RecordingRepository,
    SettingsRepository,
    SpeakerProfileRepository,
    SummaryRepository,
    SummaryTemplateRepository,
    TranscriptionRepository,
)
from local_meeting_ai.infrastructure.jobs import JobContext, LocalJobQueue

from .speaker_text import speaker_turn_text

logger = logging.getLogger(__name__)

DIARIZATION_DEFAULTS: dict[str, Any] = {
    "engine": "sherpa-onnx",
    "segmentation_model": "pyannote-3.0",
    "embedding_model": "3d-speaker",
    "quantized_segmentation": True,
    "provider": "cpu",
    "num_threads": 2,
    "num_speakers": -1,
    "cluster_threshold": 0.7,
    "min_duration_on": 0.3,
    "min_duration_off": 0.5,
    "minimum_overlap_ratio": 0.15,
    "profile_match_threshold": 0.72,
    "recognize_saved_speakers": True,
    "pyannote_exclusive": True,
    "debug": False,
    "keep_model_loaded": True,
    "preload_on_start": True,
}

SUMMARY_DEFAULTS: dict[str, Any] = {
    "provider": "local",
    "profile_id": "lfm2.5-1.2b-q4",
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
    "preload_on_start": True,
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
        speaker_profiles: SpeakerProfileRepository,
        profile_matcher: SpeakerProfileMatcher | None = None,
    ) -> None:
        self.engine = engine
        self.recordings = recordings
        self.transcriptions = transcriptions
        self.jobs = jobs
        self.preferences = preferences
        self.queue = queue
        self.speaker_profiles = speaker_profiles
        self.profile_matcher = profile_matcher

    def capability(self) -> dict[str, Any]:
        capability = self.engine.capability()
        config = configured_values(
            self.preferences,
            "diarization",
            DIARIZATION_DEFAULTS,
        )
        selected_engine = str(config["engine"])
        engines = capability.get("engines")
        if isinstance(engines, dict):
            selected = dict(engines.get(selected_engine) or {})
            if selected:
                selected["selected_engine"] = selected_engine
                selected["primary_engine"] = capability.get("primary_engine")
                selected["engines"] = engines
                capability = selected
        if self.profile_matcher is not None:
            capability["speaker_profile_matcher"] = self.profile_matcher.capability()
        return capability

    async def prepare(
        self,
        config: dict[str, Any],
        *,
        allow_model_download: bool,
    ) -> None:
        await self.engine.prepare(config, allow_model_download=allow_model_download)
        if (
            self.profile_matcher is not None
            and bool(config.get("recognize_saved_speakers", True))
            and (
                allow_model_download
                or bool(self.profile_matcher.capability().get("installed"))
            )
        ):
            await self.profile_matcher.prepare(
                config,
                allow_model_download=allow_model_download,
            )

    async def uninstall(self, engine_id: str) -> None:
        """Release model handles before removing one selectable diarizer."""
        if engine_id == "sherpa-onnx" and self.profile_matcher is not None:
            self.profile_matcher.unload()
        await self.engine.uninstall(engine_id)

    def unload(self) -> None:
        self.engine.unload()
        if self.profile_matcher is not None:
            self.profile_matcher.unload()

    def shutdown(self) -> None:
        self.engine.shutdown()
        if self.profile_matcher is not None:
            self.profile_matcher.shutdown()

    async def preload_default(self) -> None:
        config = configured_values(
            self.preferences,
            "diarization",
            DIARIZATION_DEFAULTS,
        )
        if not config["preload_on_start"] or not self.capability().get("installed"):
            return
        logger.info("Preloading %s diarization model", config["engine"])
        await self.prepare(config, allow_model_download=False)

    async def start(
        self,
        transcription_id: int,
        *,
        speaker_count: int | None = None,
        postprocess_options: dict[str, Any] | None = None,
        postprocess: bool = False,
    ) -> Job:
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
        capability = self.capability()
        if not capability.get("available") or not capability.get("installed"):
            raise CapabilityUnavailableError(
                "Install the selected diarization engine and its models in Settings first"
            )
        job = self.jobs.create(
            meeting_id=transcription.meeting_id,
            job_type=JobType.DIARIZE,
            payload={
                "transcription_id": transcription.id,
                "recording_id": normalized.id,
                "postprocess": postprocess,
                "postprocess_options": postprocess_options or {},
                "speaker_count": speaker_count,
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
        if "speaker_count" in job.payload:
            requested_speaker_count = job.payload.get("speaker_count")
            config["num_speakers"] = (
                requested_speaker_count
                if isinstance(requested_speaker_count, int) and requested_speaker_count > 0
                else -1
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
        recognized: dict[int, Any] = {}
        profiles = [item for item in self.speaker_profiles.list() if item.sample_path]
        if (
            config["recognize_saved_speakers"]
            and self.profile_matcher is not None
            and profiles
        ):
            await context.update(0.91, "Matching saved voice profiles")
            recognized = await self.profile_matcher.match(
                Path(recording.local_path),
                turns,
                profiles,
                config,
            )
        await context.update(0.94, "Assigning speakers to transcript segments")
        assigned = self.transcriptions.assign_diarization(
            meeting_id=transcription.meeting_id,
            transcription_id=transcription.id,
            diarization=turns,
            minimum_overlap_ratio=float(config["minimum_overlap_ratio"]),
            recognized_profiles=recognized,
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
        templates: SummaryTemplateRepository,
        transcriptions: TranscriptionRepository,
        jobs: JobRepository,
        preferences: SettingsRepository,
        queue: LocalJobQueue,
    ) -> None:
        self.engine = engine
        self.summaries = summaries
        self.templates = templates
        self.transcriptions = transcriptions
        self.jobs = jobs
        self.preferences = preferences
        self.queue = queue

    def capability(self) -> dict[str, Any]:
        capability = self.engine.capability()
        config = configured_values(
            self.preferences,
            "summary_engine",
            SUMMARY_DEFAULTS,
        )
        selected = next(
            (
                item
                for item in capability.get("models", [])
                if item.get("id") == config.get("profile_id")
            ),
            None,
        )
        if isinstance(selected, dict):
            selected_installed = selected.get("installed", False)
            if config.get("profile_id") == "custom-gguf":
                custom_path = Path(str(config.get("model_path") or "")).expanduser()
                selected_installed = (
                    custom_path.suffix.lower() == ".gguf" and custom_path.is_file()
                )
            capability.update(
                {
                    "display_name": selected.get("display_name", capability["display_name"]),
                    "installed": selected_installed,
                    "available": selected.get("runtime_available", capability["available"]),
                    "selected_profile": config.get("profile_id"),
                    "provider": config.get("provider"),
                }
            )
        return capability

    async def preload_default(self) -> None:
        config = configured_values(
            self.preferences,
            "summary_engine",
            SUMMARY_DEFAULTS,
        )
        if (
            config["provider"] != "local"
            or not config["preload_on_start"]
            or not self._local_model_ready(config)
        ):
            return
        await self.engine.prepare(config, allow_model_download=False)

    async def start(
        self,
        transcription_id: int,
        *,
        postprocess: bool = False,
    ) -> tuple[Summary, Job]:
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
                "Select an AI model in Settings first"
            )
        if config["provider"] == "local":
            capability = self.engine.capability()
            if not capability["available"] or not self._local_model_ready(config):
                raise CapabilityUnavailableError(
                    "Install the selected local AI model in Settings first"
                )
        template = self._default_template()
        summary = self.summaries.create(
            meeting_id=transcription.meeting_id,
            transcription_id=transcription.id,
            provider=str(config["provider"]),
            model=str(config["model"]),
            template_id=template.id,
        )
        job = self.jobs.create(
            meeting_id=transcription.meeting_id,
            job_type=JobType.SUMMARIZE,
            payload={
                "summary_id": summary.id,
                "transcription_id": transcription.id,
                "postprocess": postprocess,
                "summary_template": self._template_config(template),
            },
            message="Waiting to generate the meeting summary",
        )
        await self.queue.submit(job.uuid)
        return summary, job

    async def start_speaker(
        self,
        transcription_id: int,
        speaker_id: int,
    ) -> tuple[Any, Job]:
        transcription = self.transcriptions.get(transcription_id)
        speaker = self.transcriptions.get_speaker(speaker_id)
        if not transcription or not speaker:
            raise NotFoundError("Speaker or transcription not found")
        if transcription.status != "completed":
            raise ValidationError("Complete the transcription before summarizing")
        if transcription.meeting_id != speaker.meeting_id or not self.transcriptions.speaker_turns(
            transcription_id, speaker_id
        ):
            raise ValidationError("Speaker does not belong to this transcription")
        fragments = speaker_turn_text(
            self.transcriptions.segments(transcription_id),
            self.transcriptions.speaker_turns(transcription_id, speaker_id),
        )
        if not fragments:
            raise ValidationError("This speaker has no transcript text to summarize")
        config = configured_values(
            self.preferences,
            "summary_engine",
            SUMMARY_DEFAULTS,
        )
        if config["provider"] == "disabled":
            raise CapabilityUnavailableError(
                "Select an AI model in Settings first"
            )
        if config["provider"] == "local":
            capability = self.engine.capability()
            if not capability["available"] or not self._local_model_ready(config):
                raise CapabilityUnavailableError(
                    "Install the selected local AI model in Settings first"
                )
        updated = self.transcriptions.set_speaker_summary_status(
            speaker_id,
            "queued",
            provider=str(config["provider"]),
            model=str(config["model"]),
        )
        if not updated:
            raise NotFoundError("Speaker not found")
        job = self.jobs.create(
            meeting_id=transcription.meeting_id,
            job_type=JobType.SUMMARIZE,
            payload={
                "transcription_id": transcription.id,
                "speaker_id": speaker.id,
                "summary_scope": "speaker",
            },
            message=f"Waiting to summarize {speaker.display_name}",
        )
        await self.queue.submit(job.uuid)
        return updated, job

    async def process(self, job: Job, context: JobContext) -> dict[str, Any]:
        speaker_id = job.payload.get("speaker_id")
        if isinstance(speaker_id, int):
            return await self._process_speaker(job, context, speaker_id)
        summary_id = job.payload.get("summary_id")
        transcription_id = job.payload.get("transcription_id")
        if not isinstance(summary_id, int) or not isinstance(transcription_id, int):
            raise ValidationError("Summary job payload is incomplete")
        summary = self.summaries.get(summary_id)
        if not summary:
            raise NotFoundError("The summary no longer exists")
        segments = self.transcriptions.segments(transcription_id)
        transcription = self.transcriptions.get(transcription_id)
        if not transcription:
            raise NotFoundError("The transcription no longer exists")
        if not segments:
            raise ValidationError("The transcription has no text to summarize")
        speaker_ids = sorted(
            {segment.speaker_id for segment in segments if segment.speaker_id is not None}
        )
        configured_names = {
            speaker.id: speaker.display_name
            for speaker in self.transcriptions.speakers_for_transcription(
                transcription_id
            )
        }
        speaker_labels = {
            speaker_id: configured_names.get(speaker_id, f"Speaker {index + 1}")
            for index, speaker_id in enumerate(speaker_ids)
        }
        def summary_line(segment: Any) -> str:
            label = (
                speaker_labels.get(segment.speaker_id, "Unidentified speaker")
                if segment.speaker_id is not None
                else "Unidentified speaker"
            )
            return f"[{segment.start_ms / 1000:.1f}s] {label}: {segment.text}"

        transcript = "\n".join(summary_line(segment) for segment in segments)
        config = configured_values(
            self.preferences,
            "summary_engine",
            SUMMARY_DEFAULTS,
        )
        config["response_language"] = transcription.language
        template_snapshot = job.payload.get("summary_template")
        if isinstance(template_snapshot, dict):
            config["summary_template"] = template_snapshot
        else:
            template = (
                self.templates.get(summary.template_id)
                if summary.template_id is not None
                else self._default_template()
            )
            if template:
                config["summary_template"] = self._template_config(template)

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

    def _local_model_ready(self, config: dict[str, Any]) -> bool:
        selected = str(config.get("profile_id", "lfm2.5-1.2b-q4"))
        if selected == "custom-gguf":
            path = Path(str(config.get("model_path") or "")).expanduser()
            return path.suffix.lower() == ".gguf" and path.is_file()
        capability = self.engine.capability()
        models = capability.get("models", [])
        if not models:
            return bool(capability.get("installed"))
        return any(
            item.get("id") == selected and item.get("installed")
            for item in models
            if isinstance(item, dict)
        )

    def _default_template(self) -> SummaryTemplate:
        configured = self.preferences.get_all().get("default_summary_template_id")
        return self.templates.default(configured)

    @staticmethod
    def _template_config(template: SummaryTemplate) -> dict[str, Any]:
        return {
            "name": template.name,
            "system_prompt": template.system_prompt,
            "user_prompt_template": template.user_prompt_template,
            "sections": template.sections,
        }

    async def _process_speaker(
        self,
        job: Job,
        context: JobContext,
        speaker_id: int,
    ) -> dict[str, Any]:
        transcription_id = job.payload.get("transcription_id")
        if not isinstance(transcription_id, int):
            raise ValidationError("Speaker summary job payload is incomplete")
        transcription = self.transcriptions.get(transcription_id)
        speaker = self.transcriptions.get_speaker(speaker_id)
        if not transcription or not speaker:
            raise NotFoundError("Speaker or transcription not found")
        fragments = speaker_turn_text(
            self.transcriptions.segments(transcription_id),
            self.transcriptions.speaker_turns(transcription_id, speaker_id),
        )
        if not fragments:
            raise ValidationError("This speaker has no assigned transcript text")
        transcript = "\n".join(
            f"[{start_ms / 1000:.1f}s] {text}"
            for start_ms, text in fragments
        )
        config = configured_values(
            self.preferences,
            "summary_engine",
            SUMMARY_DEFAULTS,
        )
        config.update(
            {
                "response_language": transcription.language,
                "summary_scope": "speaker",
                "speaker_name": speaker.display_name,
            }
        )

        def is_cancelled() -> bool:
            current = self.jobs.get(job.uuid)
            return current is None or current.cancel_requested

        def progress(value: float, message: str) -> None:
            self.jobs.update_progress(job.uuid, value * 0.95, message)

        self.transcriptions.set_speaker_summary_status(speaker_id, "running")
        try:
            result = await self.engine.summarize(
                transcript,
                config,
                cast(ProgressReporter, progress),
                is_cancelled,
            )
            completed = self.transcriptions.complete_speaker_summary(
                speaker_id,
                result.content_markdown,
            )
            if not completed:
                raise NotFoundError("Speaker no longer exists")
            return {
                "speaker_id": speaker_id,
                "prompt_tokens": result.prompt_tokens,
                "completion_tokens": result.completion_tokens,
            }
        except Exception:
            self.transcriptions.set_speaker_summary_status(speaker_id, "failed")
            raise
