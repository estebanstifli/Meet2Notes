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
[docs/plugins.md](docs/plugins.md) and the detailed
[provider development guide](docs/plugin-development.md). Plugins must not
import application, database, web, bootstrap, or concrete built-in adapter
internals. Declare permissions, preserve canonical artifacts, and include tests
for every registered hook, provider, and model lifecycle.

## Core changes and independent plugins

Keep community plugin code in its own public repository. A fork of Meet2Notes is
useful for integration testing, but a core pull request is not required when the
existing public API is sufficient.

Open a core issue before a pull request when a plugin needs a missing typed hook,
contract, permission, or lifecycle operation. Core pull requests must implement
generic capabilities and tests; they must not add a one-off dependency on a
particular community package.

To request public discovery for a completed plugin, open the **Community plugin
listing** issue and provide its repository, installable package/release,
compatibility, declared permissions, network behavior, and verification results.
Maintainers decide whether to add or retain the entry in
[community-plugins.json](community-plugins.json). The plugin author retains all
maintenance and release responsibility.
