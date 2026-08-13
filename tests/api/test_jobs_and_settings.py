from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from local_meeting_ai.api.app import create_app
from local_meeting_ai.bootstrap import _retire_removed_final_transcription_preferences
from local_meeting_ai.config import AppSettings
from local_meeting_ai.domain.enums import JobType
from local_meeting_ai.paths import default_models_directory


def test_queued_job_can_be_cancelled(client: TestClient) -> None:
    container = client.app.state.container
    job = container.jobs.create(
        meeting_id=None,
        job_type=JobType.TRANSCRIBE,
        payload={},
    )

    cancelled = client.post(f"/api/jobs/{job.uuid}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["cancel_requested"] is True


def test_preferences_are_persisted_without_secrets(client: TestClient) -> None:
    defaults = client.get("/api/settings")
    assert defaults.status_code == 200
    assert defaults.json()["confirm_permanent_delete"] is True
    assert defaults.json()["ui_theme"] == "system"
    assert defaults.json()["models_directory"] == str(client.app.state.container.paths.models)
    assert defaults.json()["models_directory_restart_required"] is False

    updated = client.put(
        "/api/settings",
        json={
            "ui_language": "es",
            "ui_theme": "dark",
            "retention_days": 90,
            "confirm_permanent_delete": False,
        },
    )
    assert updated.status_code == 200
    assert updated.json()["ui_language"] == "es"
    assert updated.json()["ui_theme"] == "dark"
    assert updated.json()["retention_days"] == 90
    assert "api_key" not in updated.json()

    loaded = client.get("/api/settings")
    assert loaded.json() == updated.json()

    invalid = client.put("/api/settings", json={"ui_theme": "midnight"})
    assert invalid.status_code == 422


def test_local_http_port_is_validated_and_persisted(client: TestClient) -> None:
    updated = client.put("/api/settings", json={"http_port": 8899})

    assert updated.status_code == 200
    assert updated.json()["http_port"] == 8899
    assert client.get("/api/settings").json()["http_port"] == 8899
    assert client.put("/api/settings", json={"http_port": 80}).status_code == 422


def test_custom_models_directory_is_applied_after_restart(data_dir: Path) -> None:
    selected_models = data_dir.parent / "large-drive" / "models"
    initial_settings = AppSettings(
        data_dir=data_dir,
        testing=True,
        open_browser=False,
        log_level="WARNING",
    )
    with TestClient(create_app(initial_settings)) as first_client:
        updated = first_client.put(
            "/api/settings",
            json={"models_directory": str(selected_models)},
        )
        assert updated.status_code == 200
        assert updated.json()["models_directory"] == str(selected_models.resolve())
        assert updated.json()["active_models_directory"] == str(default_models_directory())
        assert updated.json()["models_directory_restart_required"] is True

    restarted_settings = AppSettings(
        data_dir=data_dir,
        testing=True,
        open_browser=False,
        log_level="WARNING",
    )
    with TestClient(create_app(restarted_settings)) as restarted_client:
        preferences = restarted_client.get("/api/settings").json()
        assert restarted_client.app.state.container.paths.models == selected_models.resolve()
        assert preferences["active_models_directory"] == str(selected_models.resolve())
        assert preferences["models_directory_restart_required"] is False


def test_model_directory_move_transfers_existing_files(client: TestClient, tmp_path: Path) -> None:
    container = client.app.state.container
    source = container.paths.models
    source.mkdir(parents=True, exist_ok=True)
    marker = source / "downloaded-model.bin"
    marker.write_bytes(b"local-model")
    destination = tmp_path / "other-drive" / "models"

    response = client.post(
        "/api/settings/models-directory/move",
        json={"models_directory": str(destination)},
    )

    assert response.status_code == 200
    assert (destination / marker.name).read_bytes() == b"local-model"
    assert container.preferences.get_all()["models_directory"] == str(destination.resolve())


def test_model_directory_move_requires_confirmed_overwrite(
    client: TestClient,
    tmp_path: Path,
) -> None:
    source = client.app.state.container.paths.models
    source.mkdir(parents=True, exist_ok=True)
    marker = source / "model-cache.bin"
    marker.write_bytes(b"current-model")
    destination = tmp_path / "existing-models"
    destination.mkdir()
    (destination / marker.name).write_bytes(b"old-model")

    inspection = client.post(
        "/api/settings/models-directory/inspect",
        json={"models_directory": str(destination)},
    )
    assert inspection.status_code == 200
    assert inspection.json()["requires_overwrite_confirmation"] is True
    assert inspection.json()["existing_entry_count"] == 1

    rejected = client.post(
        "/api/settings/models-directory/move",
        json={"models_directory": str(destination)},
    )
    assert rejected.status_code == 422
    assert marker.read_bytes() == b"current-model"
    assert (destination / marker.name).read_bytes() == b"old-model"

    moved = client.post(
        "/api/settings/models-directory/move",
        json={"models_directory": str(destination), "overwrite_existing": True},
    )
    assert moved.status_code == 200
    assert (destination / marker.name).read_bytes() == b"current-model"
    assert (
        client.app.state.container.preferences.get_all()["models_directory"]
        == str(destination.resolve())
    )


def test_faster_whisper_runtime_settings_are_validated_and_persisted(
    client: TestClient,
) -> None:
    updated = client.put(
        "/api/settings",
        json={
            "transcription_engine": "faster-whisper",
            "live_transcription_engine": "nvidia-nemotron",
            "live_transcription_profile": "nvidia-nemotron-3.5-streaming-0.6b",
            "final_transcription_engine": "nvidia-parakeet",
            "final_transcription_profile": "nvidia-parakeet-tdt-0.6b-v3",
            "faster_whisper": {
                "model": "small",
                "device": "cpu",
                "device_index": 0,
                "compute_type": "int8",
                "language": "es",
                "task": "transcribe",
                "beam_size": 3,
                "vad_filter": True,
                "vad_min_silence_ms": 650,
                "word_timestamps": True,
                "condition_on_previous_text": True,
                "cpu_threads": 4,
                "num_workers": 1,
                "keep_model_loaded": False,
                "preload_on_start": False,
                "realtime_chunk_seconds": 2.5,
                "realtime_overlap_seconds": 0.75,
            },
        },
    )

    assert updated.status_code == 200
    config = updated.json()["faster_whisper"]
    assert config["compute_type"] == "int8"
    assert config["language"] == "es"
    assert config["realtime_chunk_seconds"] == 2.5
    assert config["preload_on_start"] is False
    persisted = client.get("/api/settings").json()
    assert persisted["faster_whisper"] == config
    assert persisted["live_transcription_engine"] == "nvidia-nemotron"
    assert (
        persisted["live_transcription_profile"]
        == "nvidia-nemotron-3.5-streaming-0.6b"
    )
    assert persisted["final_transcription_engine"] == "nvidia-parakeet"
    assert persisted["final_transcription_profile"] == "nvidia-parakeet-tdt-0.6b-v3"

    invalid = client.put(
        "/api/settings",
        json={
            "faster_whisper": {
                **config,
                "realtime_chunk_seconds": 2.0,
                "realtime_overlap_seconds": 2.0,
            }
        },
    )
    assert invalid.status_code == 422


def test_removed_final_transcription_selection_is_migrated_to_safe_default(
    client: TestClient,
) -> None:
    preferences = client.app.state.container.preferences
    preferences.update(
        {
            "final_transcription_engine": "moss-transcribe-diarize",
            "final_transcription_profile": "moss-transcribe-diarize-0.9b",
        }
    )

    _retire_removed_final_transcription_preferences(preferences)

    migrated = preferences.get_all()
    assert migrated["final_transcription_engine"] == "faster-whisper"
    assert migrated["final_transcription_profile"] == "default"


def test_transcription_capability_reports_dedicated_worker(client: TestClient) -> None:
    response = client.get("/api/capabilities")

    assert response.status_code == 200
    transcription = response.json()["transcription"]
    assert transcription["worker"]["dedicated"] is True
    assert transcription["worker"]["thread_prefix"] == "faster-whisper"
    assert transcription["supported_devices"][0]["id"] == "auto"
    assert "vulkan" in transcription["unsupported_backends"]

    profiles = client.get("/api/models/transcription").json()
    assert all(item["id"] != "vibevoice-asr-7b" for item in profiles)
    assert all(item["id"] != "moss-transcribe-diarize-0.9b" for item in profiles)
    bitnet = next(item for item in profiles if item["id"] == "vibevoice-asr-bitnet")
    parakeet = next(
        item for item in profiles if item["id"] == "nvidia-parakeet-tdt-0.6b-v3"
    )
    nemotron = next(
        item
        for item in profiles
        if item["id"] == "nvidia-nemotron-3.5-streaming-0.6b"
    )
    assert bitnet["download_size"] == "1.58 GB"
    assert parakeet["supports_live"] is False
    assert parakeet["supports_final"] is True
    assert parakeet["download_size"] == "~2.6 GB"
    assert nemotron["supports_live"] is True
    assert nemotron["supports_final"] is True
    assert "Spanish" in nemotron["compatibility_note"]
    whisper_sizes = {
        item["model"]: item["download_size"]
        for item in profiles
        if item["engine"] == "faster-whisper" and item["id"] != "default"
    }
    assert whisper_sizes == {
        "tiny": "78.2 MB",
        "base": "148 MB",
        "small": "486 MB",
        "medium": "1.53 GB",
        "large-v3": "3.09 GB",
        "distil-large-v3": "1.52 GB",
        "turbo": "1.62 GB",
    }


def test_inactive_transcription_model_can_be_uninstalled(client: TestClient) -> None:
    models = client.app.state.container.paths.models
    tiny_cache = models / "models--Systran--faster-whisper-tiny"
    tiny_cache.mkdir(parents=True)
    (tiny_cache / "model.bin").write_bytes(b"tiny")

    response = client.post("/api/engines/transcription/uninstall?profile_id=fast")

    assert response.status_code == 200
    assert not tiny_cache.exists()


def test_active_transcription_model_cannot_be_uninstalled(client: TestClient) -> None:
    response = client.post("/api/engines/transcription/uninstall?profile_id=balanced")

    assert response.status_code == 422
    assert "Select another live transcription model" in response.json()["detail"]


def test_diarization_saved_speaker_recognition_can_be_configured(client: TestClient) -> None:
    defaults = client.get("/api/settings").json()["diarization"]
    assert defaults["preload_on_start"] is True
    assert defaults["recognize_saved_speakers"] is True

    updated = client.put(
        "/api/settings",
        json={
            "diarization": {
                "recognize_saved_speakers": False,
                "preload_on_start": False,
            }
        },
    )

    assert updated.status_code == 200
    config = updated.json()["diarization"]
    assert config["recognize_saved_speakers"] is False
    assert config["preload_on_start"] is False


def test_summary_engine_settings_are_extensible_and_do_not_store_keys(
    client: TestClient,
) -> None:
    updated = client.put(
        "/api/settings",
        json={
            "summary_engine": {
                "provider": "openai-compatible",
                "local_runtime": "external-openai",
                "model": "my-local-model",
                "base_url": "http://127.0.0.1:8080/v1",
                "api_key_env": "MEET2NOTES_AI_API_KEY",
                "context_length": 8192,
                "batch_size": 256,
                "micro_batch_size": 64,
                "max_output_tokens": 1024,
                "temperature": 0.2,
                "top_p": 0.85,
                "gpu_layers": -1,
                "flash_attention": False,
                "keep_model_loaded": True,
            }
        },
    )
    assert updated.status_code == 200
    summary = updated.json()["summary_engine"]
    assert summary["provider"] == "openai-compatible"
    assert summary["batch_size"] == 256
    assert summary["flash_attention"] is False
    assert summary["api_key_env"] == "MEET2NOTES_AI_API_KEY"
    assert "api_key" not in summary

    invalid = client.put(
        "/api/settings",
        json={
            "summary_engine": {
                **summary,
                "base_url": "not-a-url",
            }
        },
    )
    assert invalid.status_code == 422


def test_summary_model_catalog_and_litellm_preferences(
    client: TestClient,
    tmp_path: Path,
) -> None:
    catalog = client.get("/api/models/summary")
    assert catalog.status_code == 200
    models = catalog.json()
    assert [item["id"] for item in models] == [
        "lfm2.5-1.2b-q4",
        "qwen3-0.6b",
        "qwen3-1.7b",
        "custom-gguf",
        "litellm-custom",
    ]
    assert models[0]["download_size"] == "731 MB"
    assert models[1]["download_size"] == "639 MB"
    assert models[2]["download_size"] == "1.83 GB"
    assert models[3]["external_file"] is True
    assert models[4]["managed"] is False

    custom_model = tmp_path / "downloaded-model.gguf"
    custom_model.write_bytes(b"GGUF test placeholder")
    custom = client.put(
        "/api/settings",
        json={
            "summary_engine": {
                "provider": "local",
                "profile_id": "custom-gguf",
                "model": "custom-gguf",
                "model_file": "external.gguf",
                "model_path": str(custom_model),
                "preload_on_start": True,
            }
        },
    )
    assert custom.status_code == 200
    assert custom.json()["summary_engine"]["profile_id"] == "custom-gguf"
    custom_catalog = client.get("/api/models/summary").json()
    custom_profile = next(item for item in custom_catalog if item["id"] == "custom-gguf")
    assert custom_profile["installed"] is True
    assert custom_profile["configured_path"] == str(custom_model)

    updated = client.put(
        "/api/settings",
        json={
            "summary_engine": {
                "provider": "litellm",
                "profile_id": "litellm-custom",
                "model": "ollama/qwen3:8b",
                "model_file": "not-managed.gguf",
                "base_url": "http://127.0.0.1:11434",
                "preload_on_start": True,
            }
        },
    )
    assert updated.status_code == 200
    config = updated.json()["summary_engine"]
    assert config["provider"] == "litellm"
    assert config["profile_id"] == "litellm-custom"
    assert config["preload_on_start"] is True
    assert "api_key" not in config

    credential = client.get("/api/settings/summary-api-key")
    assert credential.status_code == 200
    assert set(credential.json()) == {"available", "configured"}


def test_note_formats_support_defaults_and_custom_crud(client: TestClient) -> None:
    listed = client.get("/api/summary-templates")
    assert listed.status_code == 200
    formats = listed.json()
    assert len(formats) == 9
    assert next(item for item in formats if item["is_default"])["name"] == "General Meeting"
    assert {item["name"] for item in formats} >= {
        "Daily Stand-up",
        "Project Sync",
        "Sales Call",
        "Technical Meeting",
        "Formal Minutes",
    }

    payload = {
        "name": "Clinical session",
        "description": "A private clinical note format",
        "system_prompt": "Use only facts stated in the transcript.",
        "user_prompt_template": "Create a structured clinical session note.",
        "sections": [
            {
                "title": "Observations",
                "instruction": "Summarize observations that were explicitly discussed.",
                "format": "paragraph",
                "item_format": None,
            },
            {
                "title": "Follow-up",
                "instruction": "List explicit follow-up actions.",
                "format": "list",
                "item_format": "| Action | Owner | Date |",
            },
        ],
    }
    created = client.post("/api/summary-templates", json=payload)
    assert created.status_code == 201
    custom = created.json()
    assert custom["is_builtin"] is False

    selected = client.post(f"/api/summary-templates/{custom['id']}/default")
    assert selected.status_code == 200
    assert next(item for item in selected.json() if item["is_default"])["id"] == custom["id"]
    assert client.get("/api/settings").json()["default_summary_template_id"] == custom["id"]

    payload["name"] = "Clinical consultation"
    updated = client.put(f"/api/summary-templates/{custom['id']}", json=payload)
    assert updated.status_code == 200
    assert updated.json()["name"] == "Clinical consultation"

    builtin_id = next(item["id"] for item in formats if item["is_builtin"])
    assert client.delete(f"/api/summary-templates/{builtin_id}").status_code == 422
    assert client.delete(f"/api/summary-templates/{custom['id']}").status_code == 204
    assert next(
        item for item in client.get("/api/summary-templates").json() if item["is_default"]
    )["name"] == "General Meeting"


def test_diarization_settings_are_validated_and_persisted(
    client: TestClient,
) -> None:
    response = client.put(
        "/api/settings",
        json={
            "diarization": {
                "engine": "sherpa-onnx",
                "segmentation_model": "pyannote-3.0",
                "embedding_model": "3d-speaker",
                "quantized_segmentation": True,
                "provider": "cpu",
                "num_threads": 3,
                "num_speakers": -1,
                "cluster_threshold": 0.55,
                "min_duration_on": 0.25,
                "min_duration_off": 0.45,
                "minimum_overlap_ratio": 0.2,
                "debug": False,
                "keep_model_loaded": False,
            }
        },
    )
    assert response.status_code == 200
    config = response.json()["diarization"]
    assert config["num_threads"] == 3
    assert config["cluster_threshold"] == 0.55
    assert client.get("/api/settings").json()["diarization"] == config
