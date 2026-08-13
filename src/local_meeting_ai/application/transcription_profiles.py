from __future__ import annotations

from typing import Any

from local_meeting_ai.application.transcription_config import (
    FASTER_WHISPER_MODEL_DOWNLOAD_SIZES,
    faster_whisper_config,
)
from local_meeting_ai.domain.entities import ModelProfile
from local_meeting_ai.domain.errors import ValidationError
from local_meeting_ai.domain.protocols import TranscriptionEngine
from local_meeting_ai.infrastructure.database.repositories import SettingsRepository
from local_meeting_ai.plugins.providers import ProviderRegistry


class TranscriptionProfileCatalog:
    def __init__(
        self,
        engine: TranscriptionEngine,
        preferences: SettingsRepository,
        provider_registry: ProviderRegistry | None = None,
    ) -> None:
        self.engine = engine
        self.preferences = preferences
        self.provider_registry = provider_registry

    def list(self) -> list[ModelProfile]:
        capability = self.engine.capability()
        engine_capabilities = capability.get("engines")
        if not isinstance(engine_capabilities, dict):
            engine_capabilities = {self.engine.name: capability}
        whisper_capability = engine_capabilities.get("faster-whisper", capability)
        whisper_engine = (
            "faster-whisper" if "faster-whisper" in engine_capabilities else self.engine.name
        )
        installed = set(whisper_capability.get("installed_models", []))
        config = faster_whisper_config(self.preferences.get_all())
        cuda = bool(whisper_capability.get("cuda_available"))
        preset_device = "cuda" if cuda else "cpu"
        preset_compute_type = "float16" if cuda else "int8"
        configured_model = str(config["model"])
        configured = ModelProfile(
            id="default",
            display_name="Faster Whisper · Configured default",
            description="Uses the engine configuration saved in Settings",
            engine=whisper_engine,
            model=configured_model,
            device=str(config["device"]),
            compute_type=str(config["compute_type"]),
            beam_size=int(config["beam_size"]),
            vad_filter=bool(config["vad_filter"]),
            installed=configured_model in installed,
            recommended=True,
            download_size=FASTER_WHISPER_MODEL_DOWNLOAD_SIZES.get(configured_model),
            **_advanced_profile_values(config),
        )
        profiles = [
            configured,
            ModelProfile(
                id="fast",
                display_name="Faster Whisper · Fast",
                description="Tiny model · quickest drafts and low memory use",
                engine=whisper_engine,
                model="tiny",
                device=preset_device,
                compute_type=preset_compute_type,
                beam_size=3,
                vad_filter=True,
                installed="tiny" in installed,
                download_size=FASTER_WHISPER_MODEL_DOWNLOAD_SIZES["tiny"],
                **_advanced_profile_values(config),
            ),
            ModelProfile(
                id="whisper-base",
                display_name="Faster Whisper · Base",
                description="Base model · modest resource use with better quality than Tiny",
                engine=whisper_engine,
                model="base",
                device=preset_device,
                compute_type=preset_compute_type,
                beam_size=4,
                vad_filter=True,
                installed="base" in installed,
                download_size=FASTER_WHISPER_MODEL_DOWNLOAD_SIZES["base"],
                **_advanced_profile_values(config),
            ),
            ModelProfile(
                id="balanced",
                display_name="Faster Whisper · Balanced",
                description="Small model · recommended quality and speed",
                engine=whisper_engine,
                model="small",
                device=preset_device,
                compute_type=preset_compute_type,
                beam_size=5,
                vad_filter=True,
                installed="small" in installed,
                download_size=FASTER_WHISPER_MODEL_DOWNLOAD_SIZES["small"],
                **_advanced_profile_values(config),
            ),
            ModelProfile(
                id="accurate",
                display_name="Faster Whisper · Accurate",
                description="Medium model · stronger accuracy, slower on CPU",
                engine=whisper_engine,
                model="medium",
                device=preset_device,
                compute_type=preset_compute_type,
                beam_size=5,
                vad_filter=True,
                installed="medium" in installed,
                download_size=FASTER_WHISPER_MODEL_DOWNLOAD_SIZES["medium"],
                **_advanced_profile_values(config),
            ),
            ModelProfile(
                id="very_accurate",
                display_name="Faster Whisper · Very accurate",
                description="Large v3 · best quality, substantial memory required",
                engine=whisper_engine,
                model="large-v3",
                device=preset_device,
                compute_type=preset_compute_type,
                beam_size=5,
                vad_filter=True,
                installed="large-v3" in installed,
                download_size=FASTER_WHISPER_MODEL_DOWNLOAD_SIZES["large-v3"],
                **_advanced_profile_values(config),
            ),
            ModelProfile(
                id="whisper-distil-large-v3",
                display_name="Faster Whisper · Distil Large v3",
                description="Distilled Large v3 · faster high-quality English transcription",
                engine=whisper_engine,
                model="distil-large-v3",
                device=preset_device,
                compute_type=preset_compute_type,
                beam_size=5,
                vad_filter=True,
                installed="distil-large-v3" in installed,
                supports_live=False,
                download_size=FASTER_WHISPER_MODEL_DOWNLOAD_SIZES["distil-large-v3"],
                compatibility_note="English-only distilled checkpoint.",
                **_advanced_profile_values(config),
            ),
            ModelProfile(
                id="whisper-turbo",
                display_name="Faster Whisper · Large v3 Turbo",
                description="Large v3 Turbo · fast multilingual quality pass",
                engine=whisper_engine,
                model="turbo",
                device=preset_device,
                compute_type=preset_compute_type,
                beam_size=5,
                vad_filter=True,
                installed="turbo" in installed,
                supports_live=False,
                download_size=FASTER_WHISPER_MODEL_DOWNLOAD_SIZES["turbo"],
                **_advanced_profile_values(config),
            ),
        ]
        bitnet = engine_capabilities.get("vibevoice-asr-bitnet", {})
        profiles.append(
            ModelProfile(
                id="vibevoice-asr-bitnet",
                display_name="VibeVoice ASR BitNet",
                description=("Microsoft · compact official CPU model using VibeASR.cpp"),
                engine="vibevoice-asr-bitnet",
                model="microsoft/VibeVoice-ASR-BitNet",
                device="cpu",
                compute_type="i2_s+i8_s",
                beam_size=1,
                vad_filter=False,
                cpu_threads=4,
                keep_model_loaded=True,
                installed=bool(bitnet.get("installed")),
                supports_live=False,
                supports_final=True,
                runtime_available=bool(bitnet.get("runtime_available")),
                download_size="1.58 GB",
                compatibility_note=(
                    "CPU-only. Spanish is not yet in Microsoft's validated "
                    "language list; Windows runtime currently requires MinGW."
                ),
            )
        )
        parakeet = engine_capabilities.get("nvidia-parakeet", {})
        profiles.append(
            ModelProfile(
                id="nvidia-parakeet-tdt-0.6b-v3",
                display_name="NVIDIA Parakeet TDT 0.6B v3",
                description=(
                    "NVIDIA · fast multilingual final transcription with punctuation"
                ),
                engine="nvidia-parakeet",
                model="nvidia/parakeet-tdt-0.6b-v3",
                device="auto",
                compute_type="auto",
                beam_size=1,
                vad_filter=False,
                keep_model_loaded=True,
                installed=bool(parakeet.get("installed")),
                supports_live=False,
                supports_final=True,
                runtime_available=bool(parakeet.get("runtime_available")),
                download_size="~2.6 GB",
                compatibility_note=(
                    "25 European languages including Spanish. Recommended NVIDIA "
                    "option for the final quality pass; fits an 8 GB GPU. NVIDIA's "
                    "official support matrix focuses on Linux."
                ),
            )
        )
        nemotron = engine_capabilities.get("nvidia-nemotron", {})
        profiles.append(
            ModelProfile(
                id="nvidia-nemotron-3.5-streaming-0.6b",
                display_name="NVIDIA Nemotron 3.5 ASR Streaming 0.6B",
                description=(
                    "NVIDIA · cache-aware multilingual streaming with punctuation"
                ),
                engine="nvidia-nemotron",
                model="nvidia/nemotron-3.5-asr-streaming-0.6b",
                device="auto",
                compute_type="auto",
                beam_size=1,
                vad_filter=False,
                keep_model_loaded=True,
                installed=bool(nemotron.get("installed")),
                supports_live=True,
                supports_final=True,
                runtime_available=bool(nemotron.get("runtime_available")),
                download_size="~2.6 GB",
                compatibility_note=(
                    "40 language-locales with Spanish and automatic language detection. "
                    "Best NVIDIA option for live transcription on an 8 GB GPU. NVIDIA's "
                    "official support matrix focuses on Linux."
                ),
            )
        )
        if self.provider_registry is not None:
            existing_ids = {profile.id for profile in profiles}
            registrations = self.provider_registry.models("transcription")
            for registration in registrations:
                model = registration.model
                if model.id in existing_ids:
                    continue
                capability = self.provider_registry.resolve(
                    "transcription", registration.provider_id
                ).capability()
                installed_models = set(capability.get("installed_models", []))
                provider_model_count = sum(
                    item.provider_id == registration.provider_id
                    for item in registrations
                )
                is_installed = (
                    model.model in installed_models
                    or (provider_model_count == 1 and bool(capability.get("installed")))
                    or not model.managed
                )
                defaults = dict(model.defaults)
                profiles.append(
                    ModelProfile(
                        id=model.id,
                        display_name=model.display_name,
                        description=model.description,
                        engine=registration.provider_id,
                        model=model.model,
                        device=model.device,
                        compute_type=model.compute_type,
                        beam_size=int(defaults.pop("beam_size", 1)),
                        vad_filter=bool(defaults.pop("vad_filter", False)),
                        device_index=int(defaults.pop("device_index", 0)),
                        cpu_threads=int(defaults.pop("cpu_threads", 0)),
                        num_workers=int(defaults.pop("num_workers", 1)),
                        vad_min_silence_ms=int(
                            defaults.pop("vad_min_silence_ms", 500)
                        ),
                        word_timestamps=bool(defaults.pop("word_timestamps", True)),
                        condition_on_previous_text=bool(
                            defaults.pop("condition_on_previous_text", True)
                        ),
                        keep_model_loaded=bool(
                            defaults.pop("keep_model_loaded", True)
                        ),
                        recommended=model.recommended,
                        installed=is_installed,
                        supports_live=model.supports_live,
                        supports_final=model.supports_final,
                        runtime_available=bool(
                            capability.get(
                                "runtime_available", capability.get("available")
                            )
                        ),
                        download_size=model.download_size,
                        compatibility_note=model.compatibility_note,
                        provider_options=defaults,
                    )
                )
                existing_ids.add(model.id)
        return profiles

    def get(self, profile_id: str) -> ModelProfile:
        for profile in self.list():
            if profile.id == profile_id:
                return profile
        raise ValidationError(f"Unknown transcription profile: {profile_id}")

    def resolve(self, profile_id: str, *, purpose: str) -> ModelProfile:
        requested = profile_id
        if profile_id == "default":
            preferences = self.preferences.get_all()
            configured = preferences.get(f"{purpose}_transcription_profile")
            if isinstance(configured, str) and configured.strip():
                requested = configured.strip()
        profile = self.get(requested)
        supported = profile.supports_live if purpose == "live" else profile.supports_final
        if not supported:
            raise ValidationError(
                f"{profile.display_name} cannot be used for {purpose} transcription"
            )
        return profile


def _advanced_profile_values(config: dict[str, Any]) -> dict[str, Any]:
    return {
        "device_index": int(config["device_index"]),
        "cpu_threads": int(config["cpu_threads"]),
        "num_workers": int(config["num_workers"]),
        "vad_min_silence_ms": int(config["vad_min_silence_ms"]),
        "word_timestamps": bool(config["word_timestamps"]),
        "condition_on_previous_text": bool(config["condition_on_previous_text"]),
        "keep_model_loaded": True,
    }
