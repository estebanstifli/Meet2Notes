# Documentation

The repository README is the user-facing starting point. The documents in this
directory have narrower responsibilities so installation, extension contracts,
and future plans do not become mixed together.

## Current behavior and public contracts

- [Architecture](architecture.md): process boundaries, workers, storage, and the
  provider registry.
- [Plugin API](plugins.md): how users discover, install, enable, and remove
  plugins, plus the stable hook reference.
- [Plugin and provider development](plugin-development.md): package authors,
  provider contracts, model registration, permissions, tests, and listing
  submissions.
- [Privacy](privacy.md): local data, network access, credentials, and threat
  model.
- [Webhooks](webhooks.md): outbound event contract, delivery guarantees,
  signatures, Live agents, security, and rules for future changes.
- [Live AI Assistant](live-ai-assistant.md): native real-time assistant,
  independent worker, settings, persistence, API, and resource limits.

## Product direction and research

- [Roadmap](roadmap.md): completed foundations and planned work.
- [RAG and MCP analysis](rag-and-mcp.md): design research; not all options are
  implemented.
- [AI engine research](ai-engine-research.md): model/runtime evaluation notes;
  not a user guide or stable API.

The root [product specification](../Meet2Notes.md) records the original product
brief. When it differs from the application or current documentation, the
README, source code, and the public contracts above are authoritative.
