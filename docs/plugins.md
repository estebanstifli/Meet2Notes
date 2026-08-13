# Meet2Notes Plugin API v1

Meet2Notes plugins can extend the post-recording pipeline, register selectable
transcription, diarization, summary, and embedding providers, contribute models
to an existing provider, and provide a RAG vector store. The saved recording and
canonical transcript remain owned by Meet2Notes; filters work with temporary,
serializable artifacts used by downstream stages.

This page is the user and API reference. Package authors should also read
[Plugin and provider development](plugin-development.md); the repository's
[documentation index](README.md) explains the role of every document.

## Community catalog and installation

The public [community plugin catalog](../community-plugins.json) is a small,
maintainer-controlled list of independently developed plugins. Plugin source
code and releases stay in the author's repository. An entry means only that the
maintainers chose to make the project discoverable; it is not a security audit,
warranty, or endorsement.

Meet2Notes does not currently download or install this catalog. Users must
inspect the linked repository and install a chosen package explicitly into the
private Meet2Notes `.venv`, using the exact command documented by that plugin.
A typical PyPI installation on Windows is:

```powershell
.\.venv\Scripts\python.exe -m pip install package-name
```

For a Git repository it may be:

```powershell
.\.venv\Scripts\python.exe -m pip install git+https://github.com/author/repository.git
```

After installation, open **Settings -> Plugins**, select **Rescan installed
plugins**, review the manifest and permissions, and enable the plugin. Rescan
only discovers packages already installed in the environment; it does not
contact the catalog, PyPI, or GitHub. To remove a plugin, disable it, uninstall
the package with the same Python environment, and rescan.

Plugin authors request a listing through the repository's **Community plugin
listing** issue template. Maintainers may accept, decline, update, or remove an
entry. The author remains responsible for releases, support, security fixes,
and compatibility declarations.

## Package discovery

Plugins are regular Python packages installed into the private Meet2Notes
`.venv`. Declare an entry point in the plugin's `pyproject.toml`:

```toml
[project.entry-points."meet2notes.plugins"]
example = "meet2notes_example.plugin:create_plugin"
```

Discovery does not enable a third-party plugin automatically. There is no
watched `plugins/` directory: discovery uses installed Python package metadata
from the `meet2notes.plugins` entry-point group.

For local development on Windows:

```powershell
.\.venv\Scripts\python.exe -m pip install -e .\examples\plugins\analysis_glossary
```

## Minimal filter

```python
from local_meeting_ai.plugins import (
    HookContext,
    MeetingDocument,
    PluginManifest,
    PluginRegistrar,
)


class ExamplePlugin:
    manifest = PluginManifest(
        id="example.analysis-filter",
        name="Example analysis filter",
        version="1.0.0",
        author="Example contributor",
        description="Changes a derived AI input without editing the transcript.",
        permissions=("read_transcript", "write_derived_artifact"),
    )

    def register(self, registrar: PluginRegistrar) -> None:
        registrar.add_filter("analysis.before", self.transform, priority=50)

    def transform(
        self,
        document: MeetingDocument,
        context: HookContext,
    ) -> MeetingDocument:
        result = document.model_copy(deep=True)
        # Transform result.segments here. Never access SQLite directly.
        result.metadata["processed_by"] = self.manifest.id
        return result


def create_plugin() -> ExamplePlugin:
    return ExamplePlugin()
```

Callbacks may be synchronous or asynchronous. A filter should return the same
artifact type it receives. An action returns `None`.

## Stable hooks

| Hook | Kind | Payload |
|---|---|---|
| `final_transcription.completed` | Action | Final job metadata |
| `final_transcription.failed` | Action | Final job metadata |
| `final_transcription.cancelled` | Action | Final job metadata |
| `diarization.completed` | Action | Diarization job metadata |
| `diarization.failed` | Action | Diarization job metadata |
| `diarization.cancelled` | Action | Diarization job metadata |
| `analysis.before` | Filter | `MeetingDocument` |
| `analysis.after` | Filter | `AnalysisArtifact` |
| `analysis.completed` | Action | Analysis job metadata |
| `analysis.failed` | Action | Analysis job metadata |
| `analysis.cancelled` | Action | Analysis job metadata |
| `pipeline.finished` | Action | Pipeline result metadata |
| `rag.vector_store.catalog` | Filter | `VectorStoreCatalog` |
| `rag.vector_store.operation` | Filter | `VectorStoreOperation` |

`analysis.before` is the correct location for translation, redaction,
terminology correction, or enrichment before the LLM. Set
`analysis_language` when producing a translated document. The original text in
SQLite is unaffected.

`rag.vector_store.catalog` may append descriptors for destinations such as Chroma
or a remote vector database; it must preserve the built-in `sqlite` entry. For a
selected plugin destination, `rag.vector_store.operation` receives one of
`rows_for_transcription`, `replace_transcription`, `candidates`, `counts`, or
`clear`. A plugin handles only its own `store_id` and returns a copied operation
with `handled=True` and a serializable `result`. Search candidates must include
the embedding plus the meeting, transcript, chunk, timestamps, text, title, and
date needed by the core ranker and provenance UI.

## Ordering and failures

Lower priorities run first. Equal priorities are ordered by plugin ID for
reproducibility:

```python
registrar.add_filter(
    "analysis.before",
    callback,
    priority=40,
    timeout_seconds=30,
    failure_policy="continue",  # or "fail"
)
```

Every call records plugin ID/version, hook, job and pipeline identifiers,
duration, status, error, and SHA-256 input/output digests. Transcript or summary
content is never stored in the plugin execution ledger.

## Security rules

- A Python plugin is executable code. Install and enable only packages you
  trust.
- Do not read Meet2Notes' SQLite database or model directories directly.
- Declare permissions accurately. Network access must never be hidden.
- Do not log transcripts, API keys, tokens, or recording paths.
- Use declarative provider settings for non-secret configuration and the
  operating-system keyring for secrets.
- Pin dependencies and test against the declared Plugin API version.

Third-party process isolation, signed packages, in-app installation, staged
updates, and rollback are tracked in the [roadmap](roadmap.md). The current JSON
catalog remains informational until those protections exist.
