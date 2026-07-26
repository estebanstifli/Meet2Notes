from __future__ import annotations

from typing import Any

from local_meeting_ai.application.transcription_config import (
    faster_whisper_config,
)
from local_meeting_ai.domain.entities import ModelProfile
from local_meeting_ai.domain.errors import ValidationError
from local_meeting_ai.domain.protocols import TranscriptionEngine
from local_meeting_ai.infrastructure.database.repositories import SettingsRepository


class TranscriptionProfileCatalog:
    def __init__(
        self,
        engine: TranscriptionEngine,
        preferences: SettingsRepository,
    ) -> None:
        self.engine = engine
        self.preferences = preferences

    def list(self) -> list[ModelProfile]:
        capability = self.engine.capability()
        installed = set(capability.get("installed_models", []))
        config = faster_whisper_config(self.preferences.get_all())
        cuda = bool(capability.get("cuda_available"))
        preset_device = "cuda" if cuda else "cpu"
        preset_compute_type = "float16" if cuda else "int8"
        configured_model = str(config["model"])
        configured = ModelProfile(
            id="default",
            display_name="Configured default",
            description="Uses the engine configuration saved in Settings",
            engine=self.engine.name,
            model=configured_model,
            device=str(config["device"]),
            compute_type=str(config["compute_type"]),
            beam_size=int(config["beam_size"]),
            vad_filter=bool(config["vad_filter"]),
            installed=configured_model in installed,
            recommended=True,
            **_advanced_profile_values(config),
        )
        return [
            configured,
            ModelProfile(
                id="fast",
                display_name="Fast",
                description="Tiny model · quickest drafts and low memory use",
                engine=self.engine.name,
                model="tiny",
                device=preset_device,
                compute_type=preset_compute_type,
                beam_size=3,
                vad_filter=True,
                installed="tiny" in installed,
                **_advanced_profile_values(config),
            ),
            ModelProfile(
                id="balanced",
                display_name="Balanced",
                description="Small model · recommended quality and speed",
                engine=self.engine.name,
                model="small",
                device=preset_device,
                compute_type=preset_compute_type,
                beam_size=5,
                vad_filter=True,
                installed="small" in installed,
                **_advanced_profile_values(config),
            ),
            ModelProfile(
                id="accurate",
                display_name="Accurate",
                description="Medium model · stronger accuracy, slower on CPU",
                engine=self.engine.name,
                model="medium",
                device=preset_device,
                compute_type=preset_compute_type,
                beam_size=5,
                vad_filter=True,
                installed="medium" in installed,
                **_advanced_profile_values(config),
            ),
            ModelProfile(
                id="very_accurate",
                display_name="Very accurate",
                description="Large v3 · best quality, substantial memory required",
                engine=self.engine.name,
                model="large-v3",
                device=preset_device,
                compute_type=preset_compute_type,
                beam_size=5,
                vad_filter=True,
                installed="large-v3" in installed,
                **_advanced_profile_values(config),
            ),
        ]

    def get(self, profile_id: str) -> ModelProfile:
        for profile in self.list():
            if profile.id == profile_id:
                return profile
        raise ValidationError(f"Unknown transcription profile: {profile_id}")


def _advanced_profile_values(config: dict[str, Any]) -> dict[str, Any]:
    return {
        "device_index": int(config["device_index"]),
        "cpu_threads": int(config["cpu_threads"]),
        "num_workers": int(config["num_workers"]),
        "vad_min_silence_ms": int(config["vad_min_silence_ms"]),
        "word_timestamps": bool(config["word_timestamps"]),
        "condition_on_previous_text": bool(
            config["condition_on_previous_text"]
        ),
        "keep_model_loaded": bool(config["keep_model_loaded"]),
    }
