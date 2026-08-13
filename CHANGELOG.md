# Changelog

## Unreleased

- Defined the independent community-plugin workflow, added a public JSON
  listing and issue template, and coordinated the user, contributor, developer,
  and roadmap documentation around that single flow.

## 0.5.0 - 2026-08-13

- Replaced implicit final post-processing callbacks with an observable pipeline
  for final ASR, diarization, saved-voice matching, filters, and AI analysis.
- Added Plugin API v1 with typed artifacts, actions, filters, deterministic
  priorities, timeouts, failure policies, and Python entry-point discovery.
- Added a lazy shared provider registry for plugin transcription, diarization,
  summary, and embedding engines, model-only extensions, scoped directories,
  declarative settings, and hot registry refresh.
- Added composite transcription results with speaker turns so end-to-end models
  can bypass the separate diarization stage safely.
- Added local plugin management in Settings and a privacy-preserving execution
  ledger containing timings, statuses, and content digests.
- Added a built-in analysis-cleanup filter, a community plugin example, and
  contribution and roadmap documentation.
- Kept the existing Live transcription path and settings unchanged.

## 0.4.0 - 2026-07-26

- Renamed the application and all public entry points to Meet2Notes.
- Added one-command Windows, macOS, and Linux installation with platform-aware
  llama.cpp acceleration and safe CPU fallback.
- Added `meet2notes-models` for automatic download and verification of Faster
  Whisper, sherpa-onnx, and LFM2.5 models.
- Preserved existing LocalMeet2Resume data directories to avoid breaking stored
  recording paths during the rename.
- Rebuilt the README as a product-quality installation, privacy, architecture,
  and platform guide.
- Added a resident sherpa-onnx speaker-diarization worker with model management,
  configurable clustering, and transcript speaker assignment.
- Added a resident llama.cpp summary worker and the official LFM2.5 1.2B
  Q4_K_M managed preset.
- Added independent install, load, unload, status, memory, runtime, and
  generation controls to Settings.
- Added cancellable diarization and summary jobs plus summary persistence.

## 0.3.0 - 2026-07-25

- Rebuilt the transcription workspace as a single minimal editor.
- Added editable, persistent transcription names with automatic numbering.
- Added native audio-source discovery and live capture controls.
- Added Windows WASAPI loopback through PyAudioWPatch.
- Added portable PortAudio adapters for CoreAudio and PipeWire/Pulse/ALSA inputs.
- Added pause, resume, stop, level metering, and automatic final-quality refinement.
- Added true real-time transcription with overlapping low-latency audio windows.
- Added provisional live segments and an automatic full-quality refinement after Stop.
- Added an extensible transcription-engine settings card with runtime-aware device
  and compute-type choices, 100 Whisper languages, decoding controls, and live
  chunk/overlap tuning.
- Isolated Faster Whisper in a dedicated executor and kept the configured model
  resident in memory between transcriptions by default.

## 0.2.0 - 2026-07-25

- Added FFmpeg audio normalization for transcription.
- Added an optional, lazy Faster Whisper adapter with CPU `int8` and CUDA `float16`.
- Added fast, balanced, accurate, and very accurate model profiles.
- Added explicit confirmation before any Whisper model download.
- Added persistent transcription versions and progressive timestamped segments.
- Added segment editing, activation, find and replace, and retranscription.
- Added secure local media playback and a responsive transcript editor.

## 0.1.0 - 2026-07-24

- Added the FastAPI application and local responsive web interface.
- Added versioned SQLite migrations and repositories.
- Added safe media storage, FFmpeg capability detection, and media probing.
- Added a persistent asynchronous job queue with progress and cancellation.
- Added meeting, recording, settings, job, capability, and event APIs.
- Added automated tests and cross-platform continuous integration.
