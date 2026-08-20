from __future__ import annotations

import re
from pathlib import Path

from local_meeting_ai.domain.entities import Meeting, Speaker, SpeakerProfile, SpeakerTurn
from local_meeting_ai.domain.errors import NotFoundError, ValidationError
from local_meeting_ai.domain.protocols import AudioNormalizer, AudioRangeExporter
from local_meeting_ai.infrastructure.database.repositories import (
    MeetingRepository,
    RecordingRepository,
    SpeakerProfileRepository,
    TranscriptionRepository,
)
from local_meeting_ai.infrastructure.storage import MeetingStorage

from .speaker_text import speaker_turn_text


class SpeakerService:
    def __init__(
        self,
        *,
        meetings: MeetingRepository,
        recordings: RecordingRepository,
        transcriptions: TranscriptionRepository,
        storage: MeetingStorage,
        exporter: AudioRangeExporter,
        normalizer: AudioNormalizer,
        profiles: SpeakerProfileRepository,
    ) -> None:
        self.meetings = meetings
        self.recordings = recordings
        self.transcriptions = transcriptions
        self.storage = storage
        self.exporter = exporter
        self.normalizer = normalizer
        self.profiles = profiles

    def profiles_list(self, search: str | None = None) -> list[SpeakerProfile]:
        return self.profiles.list(search=search)

    def profile_meetings(self, profile_ids: list[int]) -> list[Meeting]:
        return self.profiles.meetings_for_profiles(profile_ids)

    def rename_profile(self, profile_id: int, name: str) -> SpeakerProfile:
        clean = " ".join(name.split())
        if not clean:
            raise ValidationError("A speaker name is required")
        try:
            profile = self.profiles.update(profile_id, name=clean)
        except ValueError as error:
            raise ValidationError(str(error)) from error
        if not profile:
            raise NotFoundError("Saved voice not found")
        return profile

    def delete_profile(self, profile_id: int) -> None:
        profile = self.profiles.get(profile_id)
        if not profile:
            raise NotFoundError("Saved voice not found")
        if profile.sample_path:
            Path(profile.sample_path).unlink(missing_ok=True)
        self.profiles.delete(profile_id)

    async def create_profile_from_speaker(
        self, transcription_id: int, speaker_id: int
    ) -> SpeakerProfile:
        transcription = self.transcriptions.get(transcription_id)
        speaker = self.transcriptions.get_speaker(speaker_id)
        if not transcription or not speaker or transcription.meeting_id != speaker.meeting_id:
            raise NotFoundError("Speaker or transcription not found")
        existing = self.profiles.get_by_name(speaker.display_name)
        if existing:
            raise ValidationError(f'"{existing.name}" is already saved as a voice')
        try:
            profile = self.profiles.create(name=speaker.display_name, sample_path=None)
        except ValueError as error:
            raise ValidationError(str(error)) from error
        try:
            path, _, _ = await self.export_audio(transcription_id, speaker_id, "wav")
            destination = self.storage.speaker_profile_path(profile.id)
            destination.write_bytes(path.read_bytes())
            profile = self.profiles.update(profile.id, sample_path=str(destination)) or profile
            self.profiles.link_speaker(speaker_id, profile.id)
            return profile
        except Exception:
            self.profiles.delete(profile.id)
            raise

    async def create_profile_from_upload(self, name: str, upload: object) -> SpeakerProfile:
        clean = " ".join(name.split())
        if not clean:
            raise ValidationError("A speaker name is required")
        if self.profiles.get_by_name(clean):
            raise ValidationError(f'"{clean}" is already saved as a voice')
        try:
            profile = self.profiles.create(name=clean, sample_path=None)
        except ValueError as error:
            raise ValidationError(str(error)) from error
        try:
            stored = await self.storage.save_speaker_profile_sample(profile.id, upload)  # type: ignore[arg-type]
            source = Path(stored.path)
            destination = self.storage.speaker_profile_path(profile.id)
            try:
                await self.normalizer.normalize_for_transcription(
                    source,
                    destination,
                    sample_rate=16000,
                    channels=1,
                )
            finally:
                source.unlink(missing_ok=True)
            return self.profiles.update(profile.id, sample_path=str(destination)) or profile
        except Exception:
            self.storage.speaker_profile_path(profile.id).unlink(missing_ok=True)
            self.profiles.delete(profile.id)
            raise

    def list_for_transcription(
        self,
        transcription_id: int,
    ) -> tuple[list[Speaker], list[SpeakerTurn]]:
        if not self.transcriptions.get(transcription_id):
            raise NotFoundError("Transcription not found")
        return (
            self.transcriptions.speakers_for_transcription(transcription_id),
            self.transcriptions.speaker_turns(transcription_id),
        )

    def rename(self, speaker_id: int, display_name: str) -> Speaker:
        clean_name = " ".join(display_name.split())
        if not clean_name:
            raise ValidationError("A speaker name is required")
        speaker = self.transcriptions.rename_speaker(speaker_id, clean_name)
        if not speaker:
            raise NotFoundError("Speaker not found")
        return speaker

    async def export_audio(
        self,
        transcription_id: int,
        speaker_id: int,
        output_format: str,
    ) -> tuple[Path, str, str]:
        transcription = self.transcriptions.get(transcription_id)
        speaker = self.transcriptions.get_speaker(speaker_id)
        if not transcription or not speaker:
            raise NotFoundError("Speaker or transcription not found")
        if transcription.meeting_id != speaker.meeting_id:
            raise ValidationError("Speaker does not belong to this transcription")
        turns = self.transcriptions.speaker_turns(transcription_id, speaker_id)
        if not turns:
            raise ValidationError("This speaker has no diarized audio turns")
        recording = self.recordings.latest_for_role(transcription.meeting_id, "normalized")
        meeting = self.meetings.get(transcription.meeting_id)
        if not recording or not meeting:
            raise NotFoundError("The normalized meeting audio is unavailable")
        destination = self.storage.speaker_export_path(
            meeting.uuid,
            speaker.id,
            output_format,
        )
        await self.exporter.export_audio_ranges(
            Path(recording.local_path),
            destination,
            [(turn.start_ms, turn.end_ms) for turn in turns],
            output_format=output_format,
        )
        safe_name = re.sub(r"[^a-zA-Z0-9_-]+", "-", speaker.display_name).strip("-")
        filename = f"{safe_name or f'speaker-{speaker.id}'}.{output_format}"
        media_type = _audio_media_type(output_format)
        return destination, filename, media_type

    async def export_meeting_audio(
        self,
        transcription_id: int,
        output_format: str,
    ) -> tuple[Path, str, str]:
        transcription = self.transcriptions.get(transcription_id)
        if not transcription:
            raise NotFoundError("Transcription not found")
        recording = self.recordings.latest_for_role(transcription.meeting_id, "normalized")
        meeting = self.meetings.get(transcription.meeting_id)
        if not recording or not meeting or not recording.duration_ms:
            raise NotFoundError("The normalized meeting audio is unavailable")
        destination = self.storage.meeting_export_path(meeting.uuid, output_format)
        await self.exporter.export_audio_ranges(
            Path(recording.local_path),
            destination,
            [(0, recording.duration_ms)],
            output_format=output_format,
        )
        safe_name = re.sub(r"[^a-zA-Z0-9_-]+", "-", transcription.title).strip("-")
        filename = f"{safe_name or 'meeting'}.{output_format}"
        media_type = _audio_media_type(output_format)
        return destination, filename, media_type

    def export_text(
        self,
        transcription_id: int,
        speaker_id: int,
    ) -> tuple[str, str]:
        transcription = self.transcriptions.get(transcription_id)
        speaker = self.transcriptions.get_speaker(speaker_id)
        if not transcription or not speaker:
            raise NotFoundError("Speaker or transcription not found")
        if transcription.meeting_id != speaker.meeting_id:
            raise ValidationError("Speaker does not belong to this transcription")
        fragments = speaker_turn_text(
            self.transcriptions.segments(transcription_id),
            self.transcriptions.speaker_turns(transcription_id, speaker_id),
        )
        if not fragments:
            raise ValidationError("This speaker has no assigned transcript text")
        content = f"{speaker.display_name}\n\n" + "\n".join(
            text for _, text in fragments
        )
        safe_name = re.sub(r"[^a-zA-Z0-9_-]+", "-", speaker.display_name).strip("-")
        return content + "\n", f"{safe_name or f'speaker-{speaker.id}'}.txt"


def _audio_media_type(output_format: str) -> str:
    if output_format == "wav":
        return "audio/wav"
    if output_format == "flac":
        return "audio/flac"
    return "audio/mpeg"
