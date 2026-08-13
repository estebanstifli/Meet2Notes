# Contributing

Use Python 3.11 or newer and keep domain code independent from FastAPI, SQLite,
FFmpeg, transcription engines, and model providers. New external integrations
must implement a protocol or adapter boundary.

Before opening a change, run:

```bash
ruff check .
mypy src
pytest
```

Do not add telemetry, remote providers, silent downloads, or network exposure
without an explicit opt-in design and tests. Managed downloads must be initiated
by an installer flag, a setup command, or a visible confirmation in Settings.

Community extensions should use the versioned public API described in
[docs/plugins.md](docs/plugins.md). Plugins must not import application,
database, or web internals. Declare permissions, preserve canonical artifacts,
and include tests for every registered hook.
