# Architecture

Meet2Notes uses a layered, dependency-inward design:

1. `domain` defines entities, states, errors, and integration contracts.
2. `application` coordinates use cases without knowing FastAPI or filesystem UI details.
3. `infrastructure` implements SQLite, local storage, FFmpeg, and background jobs.
4. `api` validates HTTP input and maps domain failures to explicit responses.
5. `web` is a progressively enhanced local interface using HTML, CSS, and vanilla JavaScript.

The application is assembled in `bootstrap.py`. Infrastructure is injected into
services and handlers. Faster Whisper is the first optional engine adapter; the
domain and application services only depend on `TranscriptionEngine`,
`AudioNormalizer`, and `AudioCaptureBackend` protocols.

## Native audio capture

`AudioCaptureBackend` isolates the application and UI from operating-system audio
details. The factory selects one implementation at runtime:

- Windows uses PyAudioWPatch over PortAudio/WASAPI and exposes real WASAPI
  loopback endpoints for desktop sound.
- Linux uses PortAudio inputs, including PipeWire or PulseAudio monitor sources
  when the host exposes them.
- macOS uses CoreAudio inputs through PortAudio. System sound is shown only when
  the host exposes a suitable virtual or tap-backed input; the capability response
  explains that limitation rather than silently recording the wrong source.

Every source carries a stable session identifier, kind, backend, host API,
channel count, sample rate, default flag, and loopback flag. Capture writes PCM
WAV directly into the meeting's private storage. The service owns the lifecycle
and enforces a single active session with explicit start, pause, resume, and stop.

While recording, each native callback also appends PCM frames to a bounded drain
buffer. `LiveCaptureService` consumes those frames in short windows with a small
overlap, writes an ephemeral WAV inside the meeting's private temporary directory,
and invokes the already-loaded Faster Whisper model. Timestamped provisional
segments are persisted immediately. Word overlap at window boundaries is removed
before the UI receives the next segment.

The browser polls the lightweight capture-session endpoint for level, status, and
segment count. It fetches the transcript only when that count changes. Stopping
registers the complete WAV and schedules a full-file pass against the same
transcription record. Live segments remain visible during this pass and are
atomically replaced by the final result when it completes.

## Process model

Short SQLite operations run in request handlers. Media inspection is scheduled in
the local `asyncio` job queue and executed through asynchronous subprocesses. Jobs
are persisted before they are queued. On startup, queued work is recovered, while
previously running work is marked failed because replaying unknown operations is
unsafe.

Transcription jobs first reuse or generate a mono 16 kHz PCM WAV source. The
engine reports provisional segments while processing; these become final in one
transaction when the result completes.

Faster Whisper owns a dedicated `ThreadPoolExecutor`, separate from the API event
loop and Python's shared default executor. Startup preloads the configured local
model when it is already installed and “keep model in memory” is enabled. The
adapter caches the configured model instance after inference; settings and
capability requests read an atomic resident-model snapshot and never wait behind a
large model load.

The configured `num_workers` value is passed to CTranslate2 and also bounds
concurrent calls into the shared model instance. One worker is the low-latency,
memory-friendly default; additional workers improve throughput for simultaneous
jobs at a RAM/VRAM cost. FFmpeg runs as an asynchronous subprocess, so neither
normalization nor inference blocks the API loop.

### Independent local AI workers

The three native AI runtimes never execute on the FastAPI event loop:

- `faster-whisper`: dedicated executor for speech recognition.
- `sherpa-diarization`: one dedicated executor and one resident ONNX pipeline.
- `llama-summary`: one dedicated executor and one resident llama.cpp model.

Each engine exposes preparation, unload, capability and shutdown operations.
Models can remain resident independently, and Settings shows the actual worker
state. Startup model loads are deliberately sequenced to avoid simultaneous
RAM/VRAM allocation spikes; inference remains isolated after startup.

Diarization creates meeting-local speakers and assigns them to transcript
segments using temporal overlap. Summary generation streams tokens from
llama.cpp so cooperative cancellation can interrupt generation.

The engine contract includes preparation, transcription, unload, shutdown, and
capability discovery. Preferences keep the selected provider in a separate
`transcription_engine` field and Faster Whisper options in a provider-specific
object, leaving a clean seam for future local adapters or remote API providers.

Model download consent is stored in the job request. Faster Whisper runs with
`local_files_only` unless the user explicitly allowed a download. The
`meet2notes-models` setup command provides the same explicit, local-only model
management flow for unattended installation.

## Database

Schema changes live in numbered SQL files and are recorded in `schema_migrations`.
Every connection enables foreign keys, WAL, a five-second busy timeout, and normal
synchronous mode. Deleting a meeting cascades through its database records.
