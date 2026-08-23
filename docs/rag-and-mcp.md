# Historical RAG and MCP analysis

## Implemented RAG boundary

The historical index is deliberately split into three replaceable components:

1. `EmbeddingProvider` turns batches of text into vectors and exposes the same
   prepare/load/unload/uninstall lifecycle as the other model engines. The catalog
   intentionally contains only `BGE-M3` through FastEmbed/ONNX Runtime, `Custom GGUF` through
   llama.cpp, and `Custom local / remote via LiteLLM`.
2. `RagRepository` persists chunks, provenance and float32 vectors in the main
   SQLite database. `RagVectorStoreGateway` keeps that implementation as the
   default while routing plugin-provided destinations through the public Plugin API.
   If the optional `sqlite-vec` package is present, its extension is loaded on each
   connection; the ordinary BLOB representation remains the portable source of truth.
3. `RagService` owns segmentation, incremental invalidation, temporal filters and
   hybrid ranking. Dense candidates from the configured embedding provider and
   SQLite FTS5/BM25 candidates are combined with weighted Reciprocal Rank Fusion.

An active completed transcript is split only on segment boundaries. Each chunk
contains meeting title, date, description, timestamps and speaker labels. Before a
selected-meeting search, the active vector index identity is checked. Completed
pipelines, transcript edits, activation changes, find/replace operations and meeting
metadata edits enqueue deduplicated incremental refresh jobs. Changing the embedding
profile changes the index fingerprint and causes a compatible rebuild without coupling
the hybrid ranker to BGE-M3. Search responses expose provenance, retrieval methods and
semantic, BM25 and final RRF scores.

All-meetings queries first fuse a bounded global candidate set into a shortlist of at
most five meetings, then perform the final chunk retrieval inside that shortlist. This
keeps broad history queries efficient without changing single-meeting behavior.

The post-meeting assistant can attach completed transcript versions and AI-note
versions as raw, non-embedded context. Attachments are resolved by server-side IDs,
validated against the selected scope and accounted for by a model-independent token
budget. Raw attachments consume the budget first, recent conversation is bounded, and
RAG fills the remaining evidence budget. A raw transcript replaces retrieved chunks
from the same transcription to prevent duplicate context. Oversized selections fail
explicitly instead of silently invoking hierarchical summarization.

The API seams (`/api/rag/status`, `/api/rag/index`, `/api/rag/search`) are suitable
for a future Chroma or remote vector database without changing the Prompt UI.
Plugins register those destinations by filtering `rag.vector_store.catalog` with a
`VectorStoreCatalog`, then handle `rag.vector_store.operation` commands using a
`VectorStoreOperation`. Supported operations are `rows_for_transcription`,
`replace_transcription`, `candidates`, `counts`, and `clear`. Candidate results must
retain meeting/chunk provenance. Plugin vector stores provide the dense branch; the
local SQLite FTS5 branch is used when SQLite is the selected store.

The default BGE-M3 profile is selected but never downloaded merely by opening the
application. Its Install action downloads the official BAAI ONNX graph into the
Meet2Notes model directory. The dedicated FastEmbed worker uses ONNX Runtime on CPU
without PyTorch or an external Ollama service. The upstream FP32 ONNX files occupy
about 2.3 GB; bulk indexing is still expected to be slower than small-model inference.

## MCP: recommendation (analysis only)

Do not start with an MCP server for the complete transcription system. That surface
would immediately mix safe reads with recording control, deletion, model downloads,
long-running jobs and filesystem access. Each of those needs separate permission,
confirmation and progress semantics.

The useful first MCP should be a **read-only historical-meetings MCP** implemented as
a thin client of the application API, never as a second process that opens the
SQLite file directly. Direct database access would bypass migrations, transcript
selection rules, chunk invalidation, embedding configuration and job coordination.

Recommended first tool surface:

- `meetings.list(search?, date_from?, date_to?)`
- `meetings.get_transcript(meeting_id, start_ms?, end_ms?)`
- `meetings.search(query, meeting_id?, top_k?)`
- `meetings.ask(question, meeting_id?, use_rag=true)`
- `meetings.rag_status()`

Useful MCP resources could expose stable read-only URIs such as
`meet2notes://meetings/{id}` and `meet2notes://meetings/{id}/transcript`. Search
results should preserve the same meeting/chunk/timestamp provenance returned by the
HTTP API so clients can cite evidence.

Security and lifecycle requirements before implementing it:

- Bind to loopback by default and require an installation-specific bearer token,
  even locally. Browser origin protections do not protect an MCP transport.
- Declare that transcript text and retrieved excerpts are sensitive local data.
- Keep indexing explicit or query-driven. Never download BGE-M3 merely because an
  MCP client connected.
- Return structured errors for a missing embedding runtime/model, incomplete transcripts and a
  disabled RAG; do not silently fall back to ungrounded answers.
- Put response size and `top_k` caps on transcript/search tools.
- Version the tool schemas independently from the internal Python classes.

Only after the read-only server has proven useful should a broader Meet2Notes MCP be
considered. That second phase should use separate capabilities for capture control,
imports, transcription jobs, model management and destructive operations. Writes
such as delete, rename or model download need explicit user confirmation, while
long-running work should return job identifiers and use polling rather than holding
an MCP call open.

In short: a small read-only RAG MCP is valuable and low-coupling; a single MCP for
the whole system is premature and would create an unnecessarily large security and
maintenance boundary.
