# Plugin and provider development

This document is the implementation contract for extending Meet2Notes without
editing application core modules. It complements the shorter [Plugin API
reference](plugins.md) with provider, model, settings, packaging, and testing
guidance.

## Choose the narrowest extension point

| Requirement | Public extension point |
|---|---|
| Observe a completed stage | `PluginRegistrar.add_action(...)` |
| Transform the temporary document sent to AI | `PluginRegistrar.add_filter(...)` |
| Add a final/live ASR runtime | `PluginRegistrar.add_provider(...)` with `kind="transcription"` |
| Add a diarization runtime | `add_provider(...)` with `kind="diarization"` |
| Add an analysis/chat runtime | `add_provider(...)` with `kind="summary"` |
| Add an embedding runtime | `add_provider(...)` with `kind="embedding"` |
| Add a model to a registered runtime | `PluginRegistrar.add_model(...)` |
| Replace SQLite as the RAG destination | `rag.vector_store.catalog` and `rag.vector_store.operation` |

Audio-capture backends, arbitrary navigation pages, database migrations, and
arbitrary job types are intentionally not public plugin surfaces yet. They have
larger security and compatibility implications and must not be implemented by
monkey-patching core modules.

## Package and discovery

A plugin should live in its author's own repository as an ordinary Python
package. Forking Meet2Notes is useful for integration testing, but plugin code
does not need a pull request into the core when the public API is sufficient.
Once installed into the private Meet2Notes environment, its `pyproject.toml`
declares one entry point:

```toml
[project.entry-points."meet2notes.plugins"]
vibevoice = "meet2notes_vibevoice.plugin:create_plugin"
```

The loaded object exposes a `PluginManifest` and a synchronous `register`
method. Registration must only declare hooks, providers, models, and settings;
it must not download a model or initialize CUDA. Provider factories are lazy and
are called only when capabilities or execution require that provider.

## Provider registration

All public SDK imports come from `local_meeting_ai.plugins`. A transcription
provider can be declared as follows:

```python
from local_meeting_ai.plugins import (
    PluginManifest,
    PluginRegistrar,
    PluginSettingField,
    ProviderDescriptor,
    ProviderModel,
    ProviderRuntimeContext,
)


class VibeVoicePlugin:
    manifest = PluginManifest(
        id="community.microsoft-vibevoice",
        name="Microsoft VibeVoice provider",
        version="1.0.0",
        description="Final ASR with timestamps and integrated speaker turns.",
        permissions=("read_recording", "write_model_cache"),
    )

    def register(self, registrar: PluginRegistrar) -> None:
        registrar.add_provider(
            ProviderDescriptor(
                id="community-vibevoice",
                kind="transcription",
                display_name="Microsoft VibeVoice",
                description="Long-form transcription and speaker attribution.",
                execution_target="isolated-local",
                outputs=("transcript", "timestamps", "speaker-turns"),
                settings=(
                    PluginSettingField(
                        id="cpu_threads",
                        label="CPU threads",
                        kind="integer",
                        default=4,
                        minimum=1,
                        maximum=64,
                    ),
                ),
                models=(
                    ProviderModel(
                        id="community-vibevoice-bitnet",
                        display_name="VibeVoice ASR BitNet",
                        description="Compact CPU final-pass model.",
                        model="microsoft/VibeVoice-ASR-BitNet",
                        device="cpu",
                        compute_type="i2_s+i8_s",
                        supports_live=False,
                        supports_final=True,
                        download_size="1.58 GB",
                        defaults={"beam_size": 1, "vad_filter": False},
                    ),
                ),
            ),
            self.create_engine,
        )

    @staticmethod
    def create_engine(context: ProviderRuntimeContext):
        from .engine import VibeVoiceEngine

        return VibeVoiceEngine(context)


def create_plugin() -> VibeVoicePlugin:
    return VibeVoicePlugin()
```

`ProviderRuntimeContext.models_dir` and `data_dir` are directories scoped to the
plugin ID. `context.settings()` always returns the latest values saved from
Settings -> Plugins. A plugin must not use the application's SQLite database or
unscoped model directories.

Provider and model IDs are stable persisted identifiers. Never rename one after
release; add a migration profile or retain an alias in the plugin instead.

## Engine contracts

Providers implement the protocol for their declared kind. These public types are
re-exported by `local_meeting_ai.plugins`:

- `TranscriptionEngine`
- `DiarizationEngine`
- `SummaryEngine`
- `EmbeddingProvider`

Every engine exposes lightweight `capability()` information and explicit
prepare/install, inference, unload, uninstall, and shutdown operations. Long or
blocking work must run outside the FastAPI event loop. Inference must report
progress and honor the supplied cancellation callback. Downloads are forbidden
unless `allow_model_download=True`.

`capability()` should use this common vocabulary where applicable:

```python
{
    "engine": "community-vibevoice",
    "display_name": "Microsoft VibeVoice",
    "description": "Long-form ASR with speaker attribution.",
    "available": True,
    "runtime_available": True,
    "installed": True,
    "installed_models": ["microsoft/VibeVoice-ASR-BitNet"],
    "supports_live": False,
    "supports_final": True,
    "supports_speakers": True,
    "supports_timestamps": True,
    "providers": ["cpu"],
    "worker": {
        "state": "idle",
        "active_requests": 0,
        "model_resident": False,
        "last_error": None,
    },
}
```

### Composite transcription and diarization

An ASR engine that already identifies speakers returns both `segments` and
`speaker_turns` in `TranscriptionResult`. Meet2Notes assigns those turns to the
saved transcript and skips the separate diarization stage. Speaker numbers must
be zero-based, stable within the result, and accompanied by millisecond start/end
times.

This is the standard integration for VibeVoice-style end-to-end models; a plugin
must not create speaker rows or write transcript segments itself.

## Adding only a model

When the existing provider already supports the model format, no new engine is
needed:

```python
registrar.add_model(
    "transcription",
    "faster-whisper",
    ProviderModel(
        id="community-whisper-domain",
        display_name="Whisper Domain Fine-tune",
        description="A domain-specific CTranslate2 checkpoint.",
        model="publisher/whisper-domain-ct2",
        supports_live=False,
        supports_final=True,
        download_size="1.6 GB",
    ),
)
```

The target provider must genuinely understand that repository/file format.
Model IDs are unique within a provider kind; provider IDs are unique within a
kind. Collisions and references to unknown providers fail registration.

## Declarative settings

Provider settings use `PluginSettingField` and are rendered automatically in
Settings -> Plugins. Supported field kinds are `string`, `integer`, `number`,
`boolean`, and `select`. Numeric bounds, required values, and choices are
validated by the host before persistence. Declared defaults are available to
the provider immediately, even before the user has saved a plugin setting.

Secrets are deliberately not accepted by this schema. Store only an environment
variable name or a non-secret endpoint in plugin settings, and obtain secrets
from the operating-system keyring inside the plugin until a scoped public secret
API is added. Never place credentials in the manifest, SQLite, logs, capability
responses, or exceptions.

## Permissions

Provider registration enforces these minimum declarations:

| Provider | Required permission |
|---|---|
| Transcription | `read_recording` |
| Diarization | `read_recording` |
| Summary | `read_transcript` |
| Embedding | `read_transcript` |
| Any managed model | `write_model_cache` |
| `execution_target="remote"` | `network` |

Permissions are visible consent metadata, not a security sandbox. Third-party
plugins currently execute Python code in the application process, so users must
install only trusted packages.

## Runtime behavior

- Enabled providers appear in the existing model tables; no template or route
  changes are required.
- Provider factories are lazy. Importing or rescanning a plugin must remain fast.
- Enable, disable, and rescan operations refresh the shared registry. Removed
  provider instances receive `shutdown()`.
- A provider receives current plugin settings on every routed operation through
  `plugin_settings` in its config, or through `context.settings()`.
- Built-ins and plugins use the same router and lifecycle path.
- Plugin settings and enablement persist, but third-party code is disabled by
  default unless the manifest explicitly marks a trusted built-in default.

## Test checklist

At minimum, a provider package should test:

1. Manifest, provider, model, and permission validation.
2. Capability reporting when dependencies or models are absent.
3. No implicit download when `allow_model_download=False`.
4. Install, load, inference, unload, uninstall, and idempotent shutdown.
5. Progress and cooperative cancellation.
6. CPU behavior and every advertised accelerator.
7. Timestamp normalization and empty/malformed model output.
8. Composite speaker turns, if advertised.
9. Settings validation and absence of secrets from logs.
10. Installation into a clean Meet2Notes `.venv` followed by rescan,
    enablement, selection, and removal.

Run the host checks before publishing:

```powershell
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\mypy.exe src
.\.venv\Scripts\python.exe -m pytest
```

## Installation by an end user

The root [community plugin catalog](../community-plugins.json) is currently an
informational, maintainer-controlled list. It is not consumed by the application
and does not imply a security audit. Installation remains explicit:

```powershell
.\.venv\Scripts\python.exe -m pip install meet2notes-vibevoice
```

The user then opens Settings -> Plugins, rescans installed packages, reviews the
manifest and permissions, enables the plugin, configures it, and selects its
model in the corresponding engine table. Uninstall the package with the same
private Python environment and rescan again.

## Requesting a public listing

When a plugin has a public repository, installable release, documentation, and
passing compatibility tests, its author may open the repository's **Community
plugin listing** issue. Include:

1. Repository and package/release URL.
2. Current version plus Meet2Notes and Plugin API compatibility.
3. Registered hooks, providers, models, and settings.
4. Permissions, network behavior, model downloads, and execution location.
5. Tested operating systems and CPU/GPU configurations.

Maintainers may add the project to `community-plugins.json`, decline it, or
remove it later. Listing does not move the code into the Meet2Notes repository:
the author owns releases, support, security fixes, and compatibility. A core PR
is appropriate only when the plugin exposes a generic missing capability in the
public API; discuss that capability in a core issue first.

Catalog entries use this intentionally small shape:

```json
{
  "id": "community.microsoft-vibevoice",
  "name": "Microsoft VibeVoice provider",
  "description": "Final ASR with integrated speaker turns.",
  "repository": "https://github.com/author/meet2notes-vibevoice",
  "install": "meet2notes-vibevoice",
  "version": "1.0.0",
  "plugin_api": "1",
  "requires_meet2notes": ">=0.5,<1",
  "permissions": ["read_recording", "write_model_cache"]
}
```

`install` is a PyPI package name or a stable Git/release URL accepted by pip.
The catalog does not duplicate full documentation, supported hardware, or
security claims; those remain in the linked repository and listing issue.

## Compatibility policy

Plugins must import only from `local_meeting_ai.plugins`. Imports from
`application`, `infrastructure`, `web`, `bootstrap`, or concrete built-in
adapters are private and may change without compatibility guarantees. Additive
contracts remain within Plugin API v1; a future breaking contract increments
`PLUGIN_API_VERSION` and incompatible plugins remain discovered but disabled.
