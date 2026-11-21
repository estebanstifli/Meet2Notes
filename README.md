<div align="center">
  <img src="src/local_meeting_ai/web/static/icons/mark.svg" alt="Meet2Notes logo" width="88">
  <h1>Meet2Notes</h1>
  <p><strong>Private AI meeting notes, local transcription, and speaker diarization.</strong></p>
  <p>Record, transcribe, identify speakers, and create structured meeting summaries on your own computer.</p>

  <p>
    <a href="https://meet2notes.eu"><strong>Website</strong></a> ·
    <a href="#installation">Install</a> ·
    <a href="#performance-demo">Performance demo</a> ·
    <a href="docs/README.md">Documentation</a> ·
    <a href="https://github.com/estebanstifli/Meet2Notes/issues">Support</a>
  </p>

  <p>
    <img alt="Python 3.11+" src="https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white">
    <img alt="Platforms" src="https://img.shields.io/badge/Windows%20%7C%20macOS%20%7C%20Linux-supported-176BFF">
    <img alt="Local first" src="https://img.shields.io/badge/AI-local--first-16A085">
    <img alt="License MIT" src="https://img.shields.io/badge/license-MIT-111827">
    <img alt="Version 0.9.0" src="https://img.shields.io/badge/version-0.9.0-176BFF">
  </p>
</div>

Meet2Notes is an open-source, self-hosted AI meeting assistant for Windows,
macOS, and Linux. It captures microphone and system audio, imports recordings,
creates live or high-quality final transcripts, separates and recognizes
speakers, and turns conversations into searchable, structured meeting notes.
It is designed for private local AI workflows: recordings, transcripts, model
files, and application data remain under the user's control.

For video calls, select **Microphone + System audio** together to record both
sides of the conversation. See the [audio capture setup guide](docs/audio-capture.md)
for Windows loopback, Linux monitors, and macOS virtual inputs.

Meet2Notes is a private, local-first alternative to commercial AI meeting
assistants such as **Granola**, **Fireflies.ai**, **Fathom**, and **Otter.ai**.
It is also an open-source alternative to **Meetily** for people and teams that
want self-hosted meeting transcription, speaker diarization, and AI notes
without surrendering control of their recordings.

The official product website is [meet2notes.eu](https://meet2notes.eu).

The processing pipeline is intentionally modular. Transcription, diarization,
saved-voice matching, and analysis are independent stages with their own model
selection, settings, lifecycle, and worker. A meeting is not tied to Faster
Whisper, Sherpa-ONNX, or a particular language model.

> Back up important recordings and obtain every consent required before recording a conversation.

<a id="performance-demo"></a>

## Performance demo: a two-hour debate on an 8 GB GPU

Ask follow-up questions about a long recording with **Bonsai 27B 1-bit running
locally**. This two-minute demo uses the transcript of *The ITV Leaders' Debate*
and shows the actual Prompt interface answering three questions at original speed.

<p align="center">
  <a href="https://www.youtube.com/watch?v=pDUrVM5XTZw">
    <img src="docs/assets/meet2notes-bonsai-performance.png" alt="Watch the performance demo: two hours, three questions, one 8 GB GPU, with Bonsai 27B 1-bit" width="960">
  </a>
</p>
<p align="center"><a href="https://www.youtube.com/watch?v=pDUrVM5XTZw"><strong>Watch the performance demo on YouTube</strong></a> · 2 min 6 sec · English narration</p>

Measured on an **NVIDIA RTX 3070 with 8 GB VRAM and 32 GB system RAM**, using
native Bonsai Q1_0 with a 65,536-token context window. The source recording is
**2 h 18 min** long; the first request processed **42,343 input tokens**.

| Follow-up question | First text | Complete answer | Generation speed |
|---|---:|---:|---:|
| Ed Miliband's main proposals | 1.64 s | 3.95 s | 31.2 tokens/s |
| Cameron vs. Sturgeon on austerity | 1.62 s | 4.37 s | 31.3 tokens/s |
| Nick Clegg's NHS funding proposal | 1.83 s | 2.61 s | 31.0 tokens/s |

The initial cold request took **75.68 s**, including loading, context processing
and its answer. Each follow-up reused **more than 42,000 cached tokens** and
processed only 266–335 new input tokens. The initial wait is shortened and labeled
in the video; the three follow-up answers run at real speed, with reading pauses
after completion. All inference in this demo runs locally.

These are three short answers from one run, not a guarantee for every recording
or GPU. First-text and total times are measured at the application server;
generation speed is reported by the native runtime and excludes prompt processing.
See the [measurement details](docs/performance-demo.md).

## Meet2Notes 0.9: faster speaker recognition, flexible AI context

Version **0.9.0** adds the following changes since 0.8.0:

- **Nemotron 3 diarization is the fresh-install default**, on CPU or CUDA, with
  a private runtime, up to eight speakers, bounded processing windows and
  progress reporting. Existing installations retain their selected engine.
- **Faster saved-voice matching:** persist embeddings for saved voices and
  meeting speakers, invalidate them when inputs change, and compare cached
  vectors without reloading the embedding model. Distributed speech samples
  skip the first three seconds of long interventions and detected overlaps.
  This reduces exposure to applause and interruptions; it does not detect all noise.
- **Bonsai 8B 1-bit (1.16 GB)** joins the managed native models. Compatible
  NVIDIA GPUs with 3–7 GB VRAM use it on fresh installations, starting at 8K
  context with a 65,536-token model ceiling. The 8–15 GB and 16+ GB tiers retain
  Bonsai 27B 1-bit and Ternary; CPU/unsupported systems retain LFM. Driver,
  architecture and system RAM checks still apply.
- **OpenAI, Claude and Gemini presets** in both AI Engine and Live Assistant,
  alongside Custom model IDs and endpoints. LiteLLM 1.103.1 or newer is required.
- **Automatic context across AI engines:** selected full attachments go to
  remote APIs without a fixed 8K application ceiling. Local windows grow within
  model limits and memory constraints; output length remains independently
  configurable. Manual context limits remain available.
- **Reusable notes/chat prefixes:** retain Bonsai cache reuse, support resident
  llama.cpp reuse and Ollama context discovery, and send supported OpenAI/Claude
  cache hints. Gemini uses implicit caching. Cache hits depend on provider rules,
  prefix length and residency; live moving windows have different reuse limits.

Real cache checks succeeded with Bonsai, OpenAI, Claude and Gemini. These are
individual measurements, not guaranteed response times. The latest ITV matching
check recognized 8/8 speakers in 7.87 seconds, or 0.098 seconds with cached
embeddings; its saved voices came from the same recording.

See [context and cache validation](docs/automatic-context-cache.md),
[voice matching validation](docs/voice-sampling-validation.md),
[Bonsai 8B measurements](docs/bonsai-8b-validation.md),
[remote presets](docs/litellm-presets.md), and the
[0.9.0 changelog](CHANGELOG.md#090---2026-10-01).

**Updating:** stop the app, run the usual Windows or Pinokio updater, or update
and rerun your Unix installer, then restart. Existing meetings, settings and
model selections are preserved. Apply a new recommended profile explicitly in
Settings if desired. See [safe updates](docs/updating.md).

## Meet2Notes 0.8: long conversations, local answers

Version **0.8.0** brings native Bonsai, reusable long-context processing, streamed
assistant answers and hardware-aware setup. These are the changes since 0.7.0.

### Native Bonsai and long meeting context

- Managed **Bonsai 27B 1-bit and Ternary** GGUF models with a private, pinned,
  hash-verified Prism llama.cpp runtime. No Ollama installation is required.
- Automatic context sizing for native Bonsai in **AI notes, speaker notes and
  assistant questions**, up to **262,144 total tokens** when memory permits.
  That budget includes instructions, history and output, not just the transcript.
  Complete attachments are never silently truncated. A checked system-RAM KV
  fallback can handle GPU allocation failures, at a possible speed cost.
- A stable transcript prefix and compressed KV cache let AI notes prepare the
  context for later questions about the same attached transcript. Follow-ups reuse
  it while the model stays loaded; changing the meeting, unloading the model or
  growing its context window can require processing it again.
- Fixed empty follow-up answers, premature answers that omitted names, and context
  overflow in normal and hierarchical summaries. See [Bonsai setup and cache
  behavior](docs/automatic-llm-setup.md).

### Clearer, more useful assistants

- **Markdown answers** in Meeting Assistant and Live Assistant: lists, headings,
  tables and code, with safe links and escaped raw HTML.
- **Optional streaming** for local llama.cpp, Ollama and LiteLLM providers, with
  separate controls for Meeting Assistant and Live Assistant. Model loading and
  context preparation remain visible before the first answer; unsupported
  streaming falls back to a complete response when the provider rejects it.
- Selecting a meeting automatically attaches its active completed transcript in
  the widget and the **Prompt** page. Remove the context chip to opt out; switching
  meetings clears the previous context and conversation.
- Follow-up questions use proper conversation turns and a chat-specific prompt.
  Source markers become readable meeting links; unverified references are labeled
  instead of linking to unrelated evidence.

### Reuse your Ollama models

- **Settings → AI engine → Ollama** discovers an existing service on Windows,
  Linux or macOS and lists verified text-generation models, excluding embedding-only
  models. Choose an LLM and save without manually writing its provider identifier.
- Connection diagnostics, editable local/remote addresses, cloud-model labels and
  context-limit details make the selected model explicit. The configured context
  is passed to Ollama. Discovery does not start Ollama or download/load models.
  See the [Ollama guide](docs/ollama.md).

### Installation adapted to your hardware

- Fresh installations select **Bonsai 1-bit for compatible 8–15 GB NVIDIA GPUs**,
  **Ternary for 16+ GB**, or **LFM2.5 1.2B Q4** for CPU and unsupported systems.
  Automatic Bonsai selection also checks OS, architecture, driver and system RAM;
  VRAM alone is not sufficient. Windows, Unix, Pinokio and the model CLI share
  the policy. Model choices and custom storage are preserved on updates.
- LLM installation downloads and verifies files without loading a model or
  generating a test answer. Fresh automatic LLM profiles load on demand.
- Coordinated **live/final transcription profiles** select lighter Whisper INT8
  models for CPU or Small/Turbo for validated NVIDIA CUDA, with Sherpa-ONNX CPU
  diarization. Existing users can apply the recommendation explicitly in Settings.
  Pinokio also selects the appropriate CPU/CUDA PyTorch build on installation.
- Optional **release of idle models between stages** reduces competing memory use
  while retaining the summary model across follow-up questions. Whisper can retry
  a CUDA/memory failure on CPU before any transcript segments have been emitted.
  See [automatic audio setup](docs/automatic-audio-setup.md).

### Linux GPU setup and long-recording fixes

- Detect CUDA 12 / cuDNN 9 libraries in the private environment and supported
  system locations. Installers can repair missing libraries; Settings offers
  diagnostics and an explicit repair action. Auto mode falls back to CPU/int8
  when CUDA is unavailable. No system driver changes or `sudo` are performed.
  See [Linux CUDA setup](docs/linux-cuda.md).
- Long `diarize` CPU jobs report elapsed activity every 15 seconds, show
  indeterminate progress and can be cancelled while the worker is busy.
- Long transcripts render in batches with progress; the speaker panel loads when
  opened. This reduces browser stalls when viewing large meetings.
- Completed **AI notes appear automatically**, including when summary generation
  and search indexing finish together, instead of staying at “AI analysis is queued”.
- Transcript and speaker playback use the normalized WAV when available, fixing
  inaccurate seeking in long variable-bitrate MP3 files.

See [the changelog](CHANGELOG.md#080---2026-09-30) for the detailed change list and
upgrade instructions. The six-second overview below and the earlier presentation
remain available.

## Meet2Notes 0.7: find the moment, organize the work

Version **0.7.0** makes your local meeting library easier to use:

- Search titles, descriptions, or spoken words in completed active transcripts.
  Results show speaker, timestamp, and a direct link to the matching segment.
  Keyword search works without an embedding model or AI provider.
- Create persistent meeting tags, apply several to a meeting, rename them across
  the library, and combine a tag filter with text search. Results are paginated.
- Use Meeting Assistant quick actions to draft follow-up emails, review decisions,
  extract next steps, or prepare a status brief. Review the question before
  sending; save, edit, and delete your own reusable actions locally.
- Copy assistant answers for review and reuse. Changing the meeting scope clears
  conversation history so previous answers cannot silently affect another meeting.

See the [library and quick actions guide](docs/meeting-library.md).

<p align="center">
  <a href="https://youtu.be/Z2wRrs9Q9pU">
    <img src="docs/assets/meet2notes-demo-fast.gif" alt="Meet2Notes six-second demo: live transcription, speakers, AI notes, exports, and a real assistant answer" width="960">
  </a>
</p>
<p align="center">Live capture → Speakers → AI notes → Utilities → Meeting Assistant → Search &amp; tags</p>

### Earlier releases

Version **0.6.2** added the source and setup documentation for the optional
[Anna integration](integrations/anna/README.md). On Windows x86_64, Anna can
search your existing library and display transcripts and AI notes while Meet2Notes
and Anna Local Agent run on the same computer. Enable MCP access explicitly to
use it. Retrieved text passes through Anna and may reach its AI provider in chat.
The Anna app v0.1.3 is pending Marketplace review and uses independent versioning.
Meet2Notes works normally without an Anna account, agent, or installation.


Version 0.6.1 can record **Microphone + System audio** simultaneously, capturing
your voice and the other participants in one synchronized local recording. Each
input has its own device selector and live meter; the mixed audio feeds both live
transcription and the final WAV while preserving headroom when people speak at
the same time. Windows uses native WASAPI loopback, while the setup guide explains
the virtual or monitor inputs used on macOS and Linux.

The 0.6 series also turns Meet2Notes into a private knowledge source for the desktop
AI tools people already use. Its local **Model Context Protocol (MCP)**
server connects **Claude Desktop, ChatGPT Desktop, Codex**, and compatible MCP
clients to completed meetings without copying the Meet2Notes database or
introducing another cloud service.

Instead of manually finding and pasting old transcripts, users can ask their AI
client to locate a meeting, inspect its metadata, read the relevant transcript
or AI notes, and retrieve grounded evidence across the meeting library. This
makes previous conversations useful in the next proposal, status report,
decision review, customer follow-up, or project handover while the source data
remains under the user's control.

| What the user can do | Why it matters |
|---|---|
| Ask which meeting discussed a person, project, decision, date, or identifier | Turns a growing archive into useful organizational memory |
| Read a transcript or AI notes from Claude Desktop, ChatGPT Desktop, or Codex | Removes the repeated search-and-copy workflow |
| Search exact terms with local FTS5 or concepts with hybrid RAG | Covers both precise facts and meaning-based discovery |
| Follow meeting IDs, timestamps, speakers, and source excerpts | Keeps answers traceable to the original conversation |
| Disable MCP access at any time from Settings | Gives the user an explicit local privacy control |

Technically, every desktop client starts a lightweight `stdio` MCP process from
the existing Meet2Notes virtual environment. That process talks only to the
running application's bounded read API; it does not open `app.db`, load a second
copy of the AI models, rebuild the RAG index, or expose recording, editing,
deletion, settings, audio, or filesystem tools. Loopback is enforced by default,
and multiple clients can safely use the same running Meet2Notes instance.

Configuration snippets and their destination paths are generated in
**Settings -> General -> MCP desktop clients**, ready to copy for Claude Desktop
or for Codex/ChatGPT Desktop. See the [Local MCP server guide](docs/mcp.md) for
setup, tools, lifecycle, and security details.

## Current features

- Live recording from microphones, audio interfaces, Windows WASAPI loopback,
  and the inputs exposed by macOS or Linux.
- WAV, MP3, M4A, FLAC, OGG, AAC, MP4, MKV, WebM, and MOV import through FFmpeg.
- Separate selectable engines for live and final transcription.
- Optional final quality pass, speaker diarization, saved-voice recognition,
  and AI analysis; each step can be enabled or disabled before processing.
- Automatic speaker count or an explicit known count without using magic
  values such as `-1` in the user interface.
- Timestamped transcription segments and diarized speaker turns stored in a
  local SQLite workspace.
- Historical RAG over one meeting or the full library, with BGE-M3 embeddings,
  persisted SQLite vectors, optional sqlite-vec acceleration, hybrid ranking,
  temporal queries, and timestamped source provenance.
- A read-only local MCP server that lets Claude Desktop, ChatGPT Desktop, Codex,
  and compatible clients query meetings, transcripts, AI notes, exact keyword
  matches, and existing hybrid RAG evidence with source provenance.
- A separate Prompt window that can use a complete selected transcript or embed
  each question and retrieve grounded context from every meeting.
- A native Live AI Assistant that watches provisional transcript segments,
  follows user-defined monitoring rules, and publishes concise insights through
  its own bounded queue and independent local or LiteLLM worker.
- Durable outbound webhooks for Live segments and processing milestones, with
  per-endpoint content controls, HMAC signatures, retries, delivery history, and
  optional remote-agent suggestions that never block local transcription.
- A Speakers workspace for renaming speakers, saving voice samples, matching
  identities across meetings, generating per-speaker summaries, and exporting
  a speaker's text or audio.
- A meeting library, live job progress, cancellation, diagnostic logs, light
  and dark themes, and a safe application shutdown button. Permanent meeting
  deletion asks for confirmation and removes that meeting's audio, transcript,
  notes, Live Assistant data, RAG index entries, jobs, and private files.
- A Local AI Status panel showing engine state, model residency, system RAM,
  GPU name, VRAM when available, and the Meet2Notes GPU process.
- Model tables in Settings with installed state, download size, selection,
  install, load, unload, and uninstall actions where supported.
- Basic settings tailored to the selected model and separate advanced controls.
- Optional preload at startup. Models remain resident after use until they are
  unloaded, replaced, or the application shuts down.

## Demo

For the new Bonsai benchmark, see the [performance demo above](#performance-demo).
The original presentation covers the broader recording-to-notes workflow:

<p align="center">
  <a href="https://youtu.be/Z2wRrs9Q9pU">
    <img src="https://img.youtube.com/vi/Z2wRrs9Q9pU/maxresdefault.jpg" alt="Meet2Notes presentation and demo" width="800">
  </a>
</p>

<p align="center"><a href="https://youtu.be/Z2wRrs9Q9pU">Watch the Meet2Notes presentation and demo on YouTube</a></p>

<a id="installation"></a>

## Easy installation

### Windows: download and run one file

1. Download [`install-update.bat`](https://github.com/estebanstifli/Meet2Notes/raw/main/install-update.bat).
2. Double-click the downloaded file.
3. Wait for setup to finish, then open the new `Meet2Notes` folder and
   double-click `start.bat`.
4. Open `http://127.0.0.1:8765` in your browser.

The bootstrap installer checks for Git and Python 3.11 or newer, installs
missing prerequisites for the current Windows user, clones Meet2Notes, and
runs the normal isolated-environment installer. On an existing installation it
delegates to the safe stable-Release updater. It does not install Python
packages globally.

The installation folder is deterministic: the installer creates a
`Meet2Notes` folder beside the downloaded `.bat`. For example, a file saved as
`C:\Users\Name\Downloads\install-update.bat` installs the application in
`C:\Users\Name\Downloads\Meet2Notes`. Move the `.bat` to another writable
folder before running it if you want the application installed elsewhere.
Re-running the same file checks for a newer stable Release. `update.bat` can be
run directly for the same purpose.

> Windows may show a SmartScreen warning because this open-source batch file is
> not code-signed. Review its contents before running it and download it only
> from the official Meet2Notes repository or [meet2notes.eu](https://meet2notes.eu).

### Pinokio: one-click local installation

Meet2Notes can also be installed through [Pinokio](https://pinokio.computer),
which keeps the application, Python runtime, FFmpeg, dependencies, and
recommended local models in its isolated application environment.

1. In Pinokio, choose the option to install an app from a Git repository.
2. Enter `https://github.com/estebanstifli/Meet2Notes.git`.
3. Select **Install Meet2Notes**, wait for the model downloads to finish, then
   select **Start Meet2Notes**.
4. Use **Open Meet2Notes** in Pinokio to open the local web interface.

The Pinokio menu also provides **Update** and **Repair installation**. Repair
recreates only Pinokio's private runtime; meeting data and downloaded models
remain managed by Meet2Notes and are not removed automatically.

### macOS and Linux

```bash
git clone https://github.com/estebanstifli/Meet2Notes.git
cd Meet2Notes
chmod +x install.sh
./install.sh --ai-backend auto
.venv/bin/meet2notes --no-browser
```

Python 3.11 or newer must already be installed on macOS and Linux. For CUDA,
custom model storage, and backend-specific setup, continue to the
[advanced installation options](#advanced-installation-from-source).

On macOS, `./scripts/create_macos_app.sh` builds a double-clickable
`Meet2Notes.app` that starts this installation; add `--install` to copy it to
`/Applications`. The bundle is unsigned and points at this folder, so rebuild
it if the folder is moved.

## Modular processing pipeline

```mermaid
flowchart LR
    A["Microphone + system audio, or media file"] --> B["Capture and FFmpeg normalization"]
    B --> C["Selected live ASR"]
    B --> D["Selected final ASR"]
    C --> E["Timestamped transcript"]
    D --> E
    E --> F{"Diarization enabled?"}
    F -->|Yes| G["Selected diarization engine"]
    F -->|No| I["Transcript"]
    G --> H{"Recognize saved voices?"}
    H -->|Yes| J["Shared voice-profile matcher"]
    H -->|No| I
    J --> I
    I --> K{"AI analysis enabled?"}
    K -->|Yes| L["Selected local or LiteLLM model"]
    K -->|No| M["Local meeting workspace"]
    N["Selected note format"] --> L
    L --> M
```

Every inference adapter owns a dedicated executor or isolated worker. Heavy
model work does not run on FastAPI's event loop, and engines can be prepared,
loaded, unloaded, or replaced independently. This is the extension point for
adding more built-in or custom engines without changing the meeting workflow.

Webhook network delivery is similarly isolated from capture and inference. See
the [webhook integration guide](docs/webhooks.md) for events, payloads,
signatures, privacy controls and the Live-agent response contract.

## Engine catalog

### Transcription

Live and final transcription have independent selections. The current catalog
contains:

| Engine/model | Download | CPU | CUDA | Live | Final |
|---|---:|:---:|:---:|:---:|:---:|
| Faster Whisper Tiny | 78.2 MB | Yes | Yes | Yes | Yes |
| Faster Whisper Base | 148 MB | Yes | Yes | Yes | Yes |
| Faster Whisper Small | 486 MB | Yes | Yes | Yes | Yes |
| Faster Whisper Medium | 1.53 GB | Yes | Yes | Yes | Yes |
| Faster Whisper Large v3 | 3.09 GB | Yes | Yes | Yes | Yes |
| Faster Whisper Distil Large v3 | 1.52 GB | Yes | Yes | No | Yes |
| Faster Whisper Large v3 Turbo | 1.62 GB | Yes | Yes | No | Yes |
| NVIDIA Nemotron 3.5 ASR Streaming 0.6B | ~2.6 GB | Supported by runtime | Recommended | Yes | Yes |
| NVIDIA Parakeet TDT 0.6B v3 | ~2.6 GB | Supported by runtime | Recommended | No | Yes |
| Microsoft VibeVoice ASR BitNet | 1.58 GB | Yes | No | No | Yes |

Fresh installations choose Faster Whisper models for the hardware: Small/Turbo
on compatible NVIDIA GPUs, or lighter INT8 bundles on CPU. Existing selections
are preserved. Faster Whisper supports automatic language detection,
an explicit language such as Spanish, word timestamps, VAD, beam search,
compute type, CPU thread count, worker count, and live window overlap.
Distil Large v3 is English-only. The experimental VibeVoice BitNet runtime is
CPU-only and Spanish is not in Microsoft's currently validated language list.
The unsupported VibeVoice ASR 7B model is not exposed in the catalog.

NVIDIA models are optional and never downloaded by the default installation.
Their official support matrix focuses on Linux, although the application checks
the installed Windows runtime and reports actual readiness.

### Speaker diarization

NVIDIA Nemotron 3 Diarization is the default (up to eight speakers). The Settings -> Speakers table also exposes the
optional alternatives:

| Engine | Device | Installation and use |
|---|---|---|
| Sherpa-ONNX | CPU, CUDA, CoreML when available | Lightweight alternative using local Pyannote segmentation and 3D-Speaker models |
| Pyannote Community-1 | CPU or CUDA | Higher-accuracy gated Hugging Face model with exclusive diarization support |
| `diarize` | CPU only | Runs in a private child virtual environment to avoid dependency conflicts |
| NVIDIA Nemotron 3 Diarization | CPU or CUDA | Default 100M model, automatic detection of up to 8 speakers, continuous speaker cache and progress for long recordings |

The basic options are speaker count (automatic or known), supported execution
device, preload on startup, and saved-voice recognition. Thresholds, clustering,
segmentation, batching, and provider-specific parameters live under Advanced.

Saved-voice matching is a separate shared component, not part of a diarizer.
Consequently, existing WAV profiles can be matched after Sherpa-ONNX,
Pyannote, `diarize`, or Nemotron 3 produces the speaker turns. It extracts
3D-Speaker or TitaNet embeddings from the saved WAV samples and each detected
speaker, then compares normalized embeddings using cosine similarity. Voiceprints
are cached locally beside their source WAVs, separately from Nemotron's speaker
cache. A changed audio file, model/runtime version or diarization invalidates the
relevant cached embeddings. Deleting a saved voice also deletes its voiceprint cache.

Matching samples up to five distributed fragments (at most 30 seconds) per saved
voice and meeting speaker, excluding overlapping speakers in the meeting. Uncertain
matches can use five additional meeting fragments, capped at 60 seconds per speaker.
Short, silent or heavily clipped samples are skipped. Cosine threshold, separation
from the next candidate and agreement between fragments must all pass; ambiguous
voices keep their speaker labels. The first run builds the cache. Repeated matching
can compare cached vectors without loading the embedding model or reading PCM audio.
Progress and cancellation remain available during extraction; logs include cache
hits, processed audio duration and timings for saved voices, meeting speakers,
model loading and comparisons.

Install **NVIDIA Nemotron 3 Diarization** from **Settings → Speakers → Install**,
then select it and choose CPU or CUDA. Its model is about 400 MB; a private Python
runtime adds dependencies and reuses compatible packages from the application
without upgrading them. The Transformers implementation is currently a pinned
development revision, so this engine remains optional. Installation downloads
the model; **Load** or the first diarization request loads it into memory.

Nemotron detects speaker count automatically (maximum eight), so the manual
speaker-count setting is disabled for this engine. Meetings with more speakers
need another engine. Audio is read in bounded chunks with the speaker cache
preserved across chunks and reset between recordings. Cancellation stops the
worker, and the existing model-memory settings control unloading. Voice-profile
matching uses Sherpa embeddings on CPU even when Nemotron runs on CUDA.
See the [model card](https://huggingface.co/nvidia/Nemotron-3-Diarization)
for model limitations and its NVIDIA OpenMDW 1.1 license.

Pyannote Community-1 requires accepting the conditions on its
[Hugging Face model page](https://huggingface.co/pyannote/speaker-diarization-community-1).
Create a read token, add it to `.env`, restart Meet2Notes, and install the model
from Settings:

```dotenv
M2N_PYANNOTE_TOKEN=hf_your_read_token_here
```

Pyannote telemetry is disabled by default, and the token is not written to the
application database.

### AI analysis

The AI engine is independent of transcription and diarization. The managed
local catalog uses llama.cpp:

| Model | Approx. download | Notes |
|---|---:|---|
| Bonsai 27B 1-bit | 3.80 GB + runtime | Compatible NVIDIA GPUs with 8–15 GB VRAM; starts at 8K, automatic context growth |
| Bonsai 27B Ternary | 7.17 GB + runtime | Compatible NVIDIA GPUs with 16+ GB VRAM; starts at 16K, automatic context growth |
| LFM2.5 1.2B Q4 | 731 MB | Lightweight default for CPU and other configurations |
| Qwen3 0.6B Q8 | 639 MB | Smallest multilingual local option |
| Qwen3 1.7B Q8 | 1.83 GB | Higher-quality multilingual local option |
| Custom GGUF | User-provided | Loads an existing compatible GGUF selected with the file picker |
| Ollama | No managed download | Detects the service and lists available text LLMs for selection |
| Custom local / remote via LiteLLM | No managed download | Connects Ollama, LM Studio, OpenAI-compatible endpoints, or another LiteLLM provider |

Custom GGUF files remain owned by the user: selecting or removing a profile
does not delete the external file. Model path, context size, GPU layers,
threads, batch size, sampling, and generation limits are configurable. LiteLLM
profiles expose the model identifier, URL/base URL, and provider options.

With automatic context enabled, native Bonsai can grow its initial window up to
262,144 total tokens subject to memory checks. AI notes and assistant questions
can share the resident transcript KV prefix. This demo validates a 65,536-token
window, not the full 262K limit. See [automatic LLM setup](docs/automatic-llm-setup.md)
for hardware requirements, cache lifetime and overrides.

To reuse models already in Ollama, select **Ollama** in **Settings → AI engine**,
choose an LLM and save. Meet2Notes fills in the connection and model identifier,
shows connection diagnostics, and sends the configured context window to Ollama.
Discovery does not start the service, load models, or download anything.
See [Ollama setup](docs/ollama.md) for local, remote and Docker addresses.

LiteLLM API keys are stored through the operating system keyring (Windows
Credential Manager on Windows), not in SQLite or browser storage. The UI stores
only whether a secret is configured. Environment-based provider credentials
remain available when supported by LiteLLM.

### Live AI Assistant

Settings -> Live Assistant configures an optional native assistant for active
meetings. It has its own model selection, API-key vault entry, monitoring
instructions, trigger phrases, rolling context, compact memory, cooldown, rate
limit, and request timeout. Its model does not have to match the AI Notes model.
Responses appear in a movable, resizable, and minimizable floating meeting
widget so they remain visible without changing the transcript layout. Insights
are accumulated chronologically while the assistant continues listening; they
do not require manual accept or dismiss actions.

Capture publishes committed Live segments with a non-blocking `put_nowait` into
a bounded in-memory queue. A separate dispatcher coalesces updates and invokes a
dedicated inference engine; recording and transcription never wait for the
assistant. A separate local model runtime still consumes additional RAM/VRAM and
can contend for the same physical CPU or GPU, so the feature is disabled by
default. See the [Live AI Assistant guide](docs/live-ai-assistant.md).

## Note formats

Settings -> Note formats controls how the selected AI model turns a transcript
into structured Markdown. Formats do not alter the recording, transcript, or
speaker turns. A format defines a name, description, overall instructions, and
an ordered set of sections. Each section has a title, an instruction, an output
type (`paragraph`, `list`, or `text`), and an optional Markdown item format.

Nine built-in formats are included:

- General Meeting (default)
- Daily Stand-up
- Project Sync
- Sales Call
- Technical Meeting
- Interview
- Lecture Notes
- Brainstorming
- Formal Minutes

Users can create custom formats, duplicate a built-in format, edit or delete
custom formats, and choose any format as the default. Each summary records both
the selected format ID and an immutable snapshot of its prompt and sections, so
old results remain reproducible after a format is edited.

Completed AI notes can be edited as Markdown directly from the meeting's AI
Notes tab. The first generated version and manual-edit timestamp remain in the
summary metadata. **Rebuild AI notes** creates a new version from the active
transcript, asks for a Note Format with the Settings default preselected, and
keeps prior successful, failed, or manually edited versions in local history.
The compact header actions also copy the complete report and save edits without
adding another toolbar row. Meet2Notes warns before unsaved edits are discarded
when changing sections, following an internal link, refreshing, or closing the
page.

Long transcripts are handled with automatic context sizing for native Bonsai and
hierarchical AI notes when chunking is needed. The summary worker estimates the
prompt against the available context window,
splits oversized meetings at transcript-line boundaries, extracts grounded
evidence from every block, recursively consolidates those reports, and only
then applies the selected Note Format. The finishing dialog shows the estimated
input size and block count before processing starts; short meetings continue to
use the faster single-pass path.

## Historical RAG and Prompt

Settings -> RAG provides three embedding choices: managed BGE-M3 through
FastEmbed/ONNX Runtime, a custom local GGUF file through llama.cpp, and a custom
local or remote endpoint through LiteLLM. Basic and advanced settings adapt to
the selected profile, and RAG can be disabled independently. SQLite is the
default vector store; plugins can register alternative vector-store backends
through the public RAG hooks.

Rebuilding the historical index requires explicit confirmation and runs as a
persistent local job. Its progress dialog reports each meeting and embedding
batch without blocking the Settings request.

The post-meeting Meeting Assistant is available from the meeting library and every
completed meeting. It uses local RAG by default, combines dense retrieval with
SQLite FTS5/BM25 through Reciprocal Rank Fusion, and narrows all-history searches to
a relevant meeting shortlist before retrieving transcript evidence. For a selected
meeting, its active completed transcript is attached automatically and can be
removed with the context chip. Other completed transcript versions and AI-note
versions can also be attached. Full transcript attachments use that document
directly rather than redundant RAG retrieval. The widget reports an estimated token
budget; native Bonsai can grow its context automatically, while oversized
attachments produce an explicit error if the model or available memory cannot
accommodate them. Both the widget and the dedicated Prompt page support streamed
Markdown answers and readable source links.

## Desktop AI clients through MCP

Meet2Notes includes a read-only local MCP server for Claude Desktop, ChatGPT
Desktop, Codex, VS Code, Cursor, and other clients that support `stdio`. Each
client launches the Python module from the existing Meet2Notes virtual
environment; no separate executable is distributed. The lightweight MCP
processes all connect to the one running Meet2Notes instance, which remains the
owner of the database, RAG index, and AI models.

Available tools list meetings, read bounded transcript pages and completed AI
notes, perform fast keyword search, and retrieve existing hybrid RAG evidence.
Every result remains tied to its meeting and source context. The MCP integration
never starts indexing or exposes capture, audio, settings, edits, or deletion
operations. Meet2Notes also generates the exact Claude Desktop JSON and
Codex/ChatGPT Desktop TOML snippets from Settings, including the correct local
Python path. See [Local MCP server](docs/mcp.md) for Windows, Linux, and macOS
configuration examples.

## Help translate Meet2Notes

Meet2Notes welcomes community-maintained UI localizations. The source catalogue
is [`src/local_meeting_ai/web/static/locales/en.json`](src/local_meeting_ai/web/static/locales/en.json);
each language has a matching JSON catalogue in the same folder and is listed in
[`index.json`](src/local_meeting_ai/web/static/locales/index.json).

To add a language, copy `en.json` to its locale code (for example, `fr.json`),
translate its values, add the language's native name to `index.json`, and open a
pull request. If you would prefer to coordinate first, open a GitHub issue with
the proposed locale and we can reserve or review it there.

AI can be useful for a first draft, but it is not a substitute for native
review. We deliberately rely on native-speaking contributors to make wording,
tone, and product terminology feel right in their language. Pull requests from
translators are very welcome.

Before submitting a localization, run:

```powershell
.\.venv\Scripts\python.exe scripts\check_ui_i18n.py
```

The check verifies catalogue completeness and preserves product and technical
terms such as Meet2Notes, RAG, Faster Whisper, Word, and Markdown.

## Community plugins

The post-recording pipeline exposes a versioned Python Plugin API with
WordPress-inspired actions and filters. Community packages can observe final
transcription, diarization, analysis, and pipeline lifecycle events or transform
the temporary document sent to AI. Translation, redaction, terminology,
enrichment and alternative RAG vector stores can therefore be added
without altering core capture code. The shared provider registry
also accepts transcription, diarization, summary, and embedding engines, models
for an existing engine, declarative settings, and composite ASR results containing
speaker turns.

Plugins are discovered through the standard `meet2notes.plugins` package entry
point and managed from Settings -> Plugins. Hook executions have priorities,
timeouts, failure policies, and a privacy-preserving provenance ledger. The
canonical recording and transcript are never overwritten by a filter.

Each community plugin is developed and released from its author's own
repository. Authors do not need to merge plugin code into Meet2Notes: when it is
ready, they may open a **Community plugin listing** issue with its public URL,
installation source, compatibility, permissions, and test results. Maintainers
may then add it to [community-plugins.json](community-plugins.json). Listing is
discretionary and is not a security audit or endorsement.

The catalog is currently an informational JSON file; Meet2Notes does not fetch
or install entries automatically. A user chooses a listed plugin, reviews its
repository, and installs it explicitly into the private environment, for
example:

```powershell
.\.venv\Scripts\python.exe -m pip install package-name
```

Then open Settings -> Plugins, rescan installed packages, review the requested
permissions, and enable it. See the [Plugin API and installation guide](docs/plugins.md),
[plugin/provider development guide](docs/plugin-development.md),
[documentation index](docs/README.md), and [public roadmap](docs/roadmap.md).

## Advanced installation from source

The installers create an isolated `.venv` inside the repository. Meet2Notes
does not install packages into the global Python environment. Python 3.11 or
newer is required; Python 3.12 is recommended for the broadest CUDA wheel
compatibility. FFmpeg is installed automatically when the platform package
manager permits it.

Clone the repository first:

```powershell
git clone https://github.com/estebanstifli/Meet2Notes.git
cd Meet2Notes
```

### CPU-only installation

Windows PowerShell:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\install.ps1 -AiBackend cpu
.\start.bat
```

macOS or Linux:

```bash
chmod +x install.sh
./install.sh --ai-backend cpu
.venv/bin/meet2notes --no-browser
```

### NVIDIA CUDA installation

Use this installation on Windows or Linux when a compatible NVIDIA driver is
present. CUDA-enabled PyTorch and compatible packages are installed only in
`.venv`; a system-wide CUDA toolkit is not required.

Windows PowerShell:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\install.ps1 -AiBackend cuda
.\start.bat
```

Linux:

```bash
./install.sh --ai-backend cuda
.venv/bin/meet2notes --no-browser
```

On Linux, Faster Whisper automatically finds CUDA 12 / cuDNN 9 libraries in the
active Python environment, conda/Pinokio, and standard CUDA locations. No manual
`LD_LIBRARY_PATH` export is needed for supported libraries. The Linux installer
and Pinokio update install missing libraries when a working NVIDIA GPU is detected.
If CUDA is unavailable, **Auto** transcribes on CPU/int8; **Settings → Transcription**
shows the diagnostic and a repair button when libraries can be installed in the
private environment. Restart after repairing. See [Linux CUDA troubleshooting](docs/linux-cuda.md).

If Meet2Notes was initially installed in CPU mode and the user later selects a
CUDA-only configuration, Settings detects the mismatch. A confirmation dialog
explains the change and a progress dialog streams the package installation log
while the CUDA PyTorch runtime is installed into `.venv`. Restart the
application after the upgrade.

Useful installer options:

```powershell
# Runtime without downloading the recommended model set
.\install.ps1 -AiBackend cpu -Models none

# Development dependencies
.\install.ps1 -Dev -Models none

# Keep large model files on another disk
.\install.ps1 -ModelsDirectory "D:\Meet2Notes\Models"
```

Equivalent Unix options are `--no-models`, `--dev`, and
`--models-dir /path/to/models`.

## Start and stop

On Windows, double-click `start.bat` or run it from CMD or PowerShell. It does
not open a browser. The same console reports configured model preload results
before the server announces its local address:

```text
http://127.0.0.1:8765
```

Open that address manually. The console remains open after a normal exit or an
error so its last messages can be read. To stop safely, use the power button in
the Local AI Status card or press `Ctrl+C` in the console. The normal shutdown
path stops capture and jobs, unloads the models, releases CUDA memory, shuts
down workers, and then terminates Python. Closing only the browser tab does not
stop the local server because a tab cannot reliably own a background process.

Direct launch and diagnostics:

```powershell
.\.venv\Scripts\python.exe -m local_meeting_ai --no-browser
.\.venv\Scripts\meet2notes.exe --help
```

Meet2Notes binds to `127.0.0.1` by default and is not exposed to the network
unless the host setting is changed explicitly. A single-instance lock prevents
accidentally starting two servers against the same data directory.

### Safe stable updates

Before starting the server, `start.bat` checks at most once every 24 hours for
a newer stable GitHub Release. If one exists, the user can accept it or defer
the notification. `update.bat` provides the same check manually.

The updater never follows arbitrary commits from `main`. It accepts only
`vX.Y.Z` tags from the official repository, requires a clean Git checkout and
a stopped application, and creates a consistent SQLite backup before changing
the source. Settings, meetings, recordings, summaries, RAG data, models, and
custom storage locations are preserved. New settings parameters receive their
new defaults without overwriting stored values. See [Safe updates](docs/updating.md)
for the complete transaction and recovery model.

## Model installation and storage

The default installer downloads the recommended live/final Faster Whisper bundle, Nemotron 3 diarization,
the shared saved-voice embedding model, and a local LLM selected for the hardware:
Bonsai 27B 1-bit on compatible 8+ GB NVIDIA GPUs, Ternary on 16+ GB GPUs,
or LFM2.5 1.2B Q4 elsewhere. Existing AI model selections are preserved on updates.
Bonsai uses a private, pinned Prism llama.cpp runtime and does not require Ollama.
LLM installation downloads and verifies files without loading the model or generating
a test answer. Full installation retains LFM for the independent Live Assistant
unless that assistant has another saved model. New installations load the LLM on demand. See
[automatic model setup](docs/automatic-llm-setup.md) for requirements and overrides.
See [automatic audio setup](docs/automatic-audio-setup.md) for the CPU/GPU profiles,
memory management, and long-diarization activity reporting. Settings → Transcription
can install and apply the recommendation to an existing installation.
Historical RAG selects
BGE-M3 by default and installs it directly through FastEmbed/ONNX Runtime without
Ollama or PyTorch. Other catalog entries are opt-in.
Models are reused between sessions and are separate from recordings and the SQLite
database.

The Settings tables are the preferred management interface. Command-line model
setup is also available:

```powershell
.\.venv\Scripts\meet2notes-models.exe --models all
.\.venv\Scripts\meet2notes-models.exe --models whisper --whisper-model medium
.\.venv\Scripts\meet2notes-models.exe --models diarization summary
.\.venv\Scripts\meet2notes-models.exe --models summary --llm-profile light
.\.venv\Scripts\meet2notes-models.exe --models summary --llm-profile bonsai-1bit
.\.venv\Scripts\meet2notes-models.exe --models embeddings
.\.venv\Scripts\meet2notes-models.exe --models nvidia-parakeet
.\.venv\Scripts\meet2notes-models.exe --models nvidia-nemotron
```

Application data defaults to `data/` and model weights to `models/` inside the
installation. Both can be moved independently from Settings -> General -> Data
storage locations. The selected locations are activated safely on the next
start. They can also be overridden with `M2N_DATA_DIR`, `M2N_MODELS_DIR`,
`--data-dir`, or `--models-dir`.

## Recording and post-processing

After stopping a recording or importing a media file, Meet2Notes presents the
processing choices before starting expensive work:

1. Run or skip speaker diarization.
2. Detect the number of speakers automatically or provide the known count.
3. Run or skip the selected final transcription pass.
4. Run or skip AI analysis using the selected note format.

The processing dialog includes a live text log as well as progress. Each job
records timestamps and intermediate stages. A failure in an optional stage is
reported without coupling the remaining engines to that implementation.

## Privacy and local data

- Recordings, transcripts, speaker turns, summaries, preferences, and job state
  are stored locally.
- There is no telemetry and no automatic cloud upload.
- Local engines do not require an Internet connection after their packages and
  weights are installed.
- Network access occurs for the short cached GitHub Release check, an explicit
  update or model download, or when the user selects a remote LiteLLM provider.
  The update check sends no meeting data or telemetry.
- Provider secrets use the OS keyring; the Pyannote download token is read from
  `.env` or the process environment.
- `.env`, databases, recordings, model weights, logs, benchmarks, local path
  overrides, and UI test workspaces are excluded from version control.

See [Privacy](docs/privacy.md) for the threat model and storage details.

## ASR evaluator

`scripts/evaluate_asr.py` benchmarks installed ASR engines outside the unit test
suite. It uses a separate Python process and an orchestration thread, unloads
the model before and after every pass, and never downloads missing engines.

```powershell
.\.venv\Scripts\python.exe scripts\evaluate_asr.py --input debate_ceuta.wav
```

For every selected engine and input it attempts four cold passes: CPU with
automatic language detection, CPU with Spanish, CUDA with automatic detection,
and CUDA with Spanish. Permanent results are written to
`<data-dir>/benchmarks/asr`:

- A timestamped JSON run with start/end times, load, inference, unload and
  intermediate progress timings, effective configuration, errors, and the
  complete transcript text.
- `asr-evaluations.json`, an append-only comparison ledger.

Use `--profile <ids...>`, `--input <files...>`, or `--results-dir <folder>` to
limit or relocate a run. Unsupported devices and missing models are retained as
explicitly skipped passes rather than disappearing from the comparison.

## Diarization evaluator

`scripts/evaluate_diarization.py` benchmarks the installed diarization engines
without starting the web application. It performs one cold CPU pass and one
cold CUDA pass per selected engine when supported, using a separate process and
orchestration thread.

```powershell
.\.venv\Scripts\python.exe scripts\evaluate_diarization.py --input debate_ceuta.wav
```

The input is normalized once to 16 kHz mono WAV. Results under
`<data-dir>/benchmarks/diarization` include load, diarization, unload, start/end,
and intermediate progress timings; the effective configuration; detected
speaker statistics; every speaker segment; a readable timeline per pass; and
the append-only `diarization-evaluations.json` ledger. Missing runtimes, tokens,
or CUDA support are recorded explicitly. Use `--engines sherpa-onnx pyannote-community-1`
to limit a run and `--num-speakers 2` only when the count is known.

## Development

```powershell
.\install.ps1 -Dev -Models none
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\mypy.exe src
.\.venv\Scripts\python.exe -m pytest
```

On macOS or Linux, use `./install.sh --dev --no-models`. The repository includes
database migrations, API/integration/unit tests, model lifecycle tests,
timestamp normalization tests, and multi-platform GitHub Actions checks for
Python 3.11 and 3.13.

The main boundaries are:

- `adapters/`: capture, transcription, diarization, voice matching, summaries,
  model files, and credential storage.
- `application/`: orchestration, engine settings, speaker services, note formats,
  and job workflows.
- `infrastructure/`: SQLite repositories, migrations, FFmpeg, storage, model
  installation, job execution, CUDA setup, and instance locking.
- `api/`: versioned request/response schemas and local HTTP endpoints.
- `web/`: server-rendered pages plus the browser UI.

Start with the [documentation index](docs/README.md). Read
[Architecture](docs/architecture.md), [Contributing](CONTRIBUTING.md), and the
[Roadmap](docs/roadmap.md) before extending an engine or submitting core changes.

## Platform support

| Capability | Windows | macOS | Linux |
|---|---|---|---|
| Microphone/audio interface | WASAPI | CoreAudio input | PipeWire/Pulse/ALSA input |
| Desktop audio | WASAPI loopback | Virtual/tap-backed input* | Monitor input* |
| Faster Whisper CPU | Yes | Yes | Yes |
| Faster Whisper CUDA | NVIDIA | No | NVIDIA |
| llama.cpp acceleration | CUDA or CPU | Metal or CPU | CUDA or CPU |

\* Availability depends on the source exposed by the operating system.

## License

Meet2Notes is released under the [MIT License](LICENSE).
