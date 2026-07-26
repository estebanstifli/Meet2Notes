from __future__ import annotations

from fastapi.testclient import TestClient

from local_meeting_ai.domain.enums import JobType


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

    updated = client.put(
        "/api/settings",
        json={
            "ui_language": "es",
            "retention_days": 90,
            "confirm_permanent_delete": False,
        },
    )
    assert updated.status_code == 200
    assert updated.json()["ui_language"] == "es"
    assert updated.json()["retention_days"] == 90
    assert "api_key" not in updated.json()

    loaded = client.get("/api/settings")
    assert loaded.json() == updated.json()


def test_faster_whisper_runtime_settings_are_validated_and_persisted(
    client: TestClient,
) -> None:
    updated = client.put(
        "/api/settings",
        json={
            "transcription_engine": "faster-whisper",
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
    assert client.get("/api/settings").json()["faster_whisper"] == config

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


def test_transcription_capability_reports_dedicated_worker(client: TestClient) -> None:
    response = client.get("/api/capabilities")

    assert response.status_code == 200
    transcription = response.json()["transcription"]
    assert transcription["worker"]["dedicated"] is True
    assert transcription["worker"]["thread_prefix"] == "faster-whisper"
    assert transcription["supported_devices"][0]["id"] == "auto"
    assert "vulkan" in transcription["unsupported_backends"]


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
