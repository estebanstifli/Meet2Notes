# Live AI Assistant

The Live AI Assistant is an optional native meeting copilot. It observes
committed provisional transcript segments, evaluates user-defined monitoring
rules, and displays concise insights while capture is active. It is disabled by
default and is independent from post-meeting AI Notes.

## Runtime boundary

Capture never calls a model or performs network I/O. After overlap
deduplication and persistence, it publishes the new segment batch with
`put_nowait` into a bounded queue. If the assistant falls behind, the oldest
pending batch is dropped and the most recent meeting speech is retained.

When enabled, a dedicated async dispatcher coalesces queued batches and invokes
its own `LlamaCppSummaryEngine` instance. That instance owns a separate
single-thread executor and model residency from AI Notes. Disabling the feature
cancels the active session, drains pending work, and unloads an idle assistant
model. Application shutdown cancels the dispatcher and closes its executor.

This boundary prevents assistant latency, provider timeouts, and failures from
blocking recording, Live ASR, final ASR, diarization, API requests, or AI Notes.
It cannot isolate physical hardware: loading another local GGUF consumes extra
RAM or VRAM and simultaneous inference can reduce throughput on the same CPU or
GPU.

## Context and response policy

Each evaluation contains:

- the latest committed Live segments;
- a time-bounded recent transcript window;
- compact memory returned by earlier evaluations;
- a short list of earlier assistant responses; and
- the user's monitoring instructions.

The full transcript is not resent on every evaluation. Optional trigger phrases
are literal pre-filters: each entry should be an exact word or short phrase such
as `Alexa` or `Madrid`. Conditional behavior belongs in Instructions. The model
is not called until a new segment contains one of the configured literal
phrases. Cooldown, calls-per-minute, context-length, output-token, and timeout
limits provide additional cost and latency control.

New installations start with a 16,384-token context window and a 1,024-token
output ceiling. Output tokens are a maximum rather than a target; 512–1,024 is
normally appropriate for responsive live contributions. LiteLLM calls first
use its provider/model capability metadata and safely omit a sampling parameter
when a selected model accepts only its default value.

Transcript content is explicitly marked as untrusted data in the system prompt.
The model must return one JSON object describing whether to respond, the insight
text and kind, confidence, related Live segment IDs, and updated compact memory.
Invalid or failed responses are recorded as session errors without changing the
transcript or meeting notes.

During a meeting, responses appear in a floating widget rather than consuming
transcript layout space. The widget can be dragged by its header, resized from
its lower-right handle, resized with arrow keys while that handle is focused,
and minimized. Its size, position, and minimized state are stored as local UI
preferences in browser storage; transcript or assistant content is not stored
there.

## Models and credentials

Settings -> Live Assistant selects a model independently from Settings -> AI
engine. Supported paths are:

- a managed local llama.cpp model already installed through AI Engine; or
- any local or remote provider supported by LiteLLM, including Ollama, LM
  Studio, and OpenAI-compatible services.

The assistant's LiteLLM API key uses its own operating-system credential-vault
account. It is never stored in SQLite, browser storage, diagnostics, or API
responses, and it never falls back to the AI Notes key. Enabling a remote
provider requires an explicit UI confirmation because recent transcript text is
sent to that endpoint.

## Persistence and local API

`live_assistant_sessions` stores lifecycle state, a settings snapshot, compact
memory, sequence progress, and the last error. `live_assistant_insights` stores
the immutable response text, timing/model provenance, and related provisional
segment IDs. The floating widget presents them chronologically and automatically
keeps the latest contribution in view.

The loopback UI uses these endpoints:

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/live-assistant` | Catalog, current settings, capability, credential status |
| `PUT` | `/api/live-assistant/settings` | Validate and save independent settings |
| `PUT` / `DELETE` | `/api/live-assistant/api-key` | Store or remove the scoped credential |
| `GET` | `/api/live-assistant/meetings/{meeting_id}` | Runtime state and recent insights |

These endpoints are application-internal loopback APIs, not public inbound
webhooks. Existing outbound webhooks and remote-agent suggestions remain a
separate integration mechanism.
