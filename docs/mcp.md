# Local MCP server

Meet2Notes includes a read-only Model Context Protocol server for desktop AI
clients. Each client starts its own lightweight `stdio` process. That process
does not open the database or load AI models; it discovers the running
Meet2Notes instance and uses its loopback HTTP API.

Meet2Notes must be running when a tool is called. The MCP process can start
while the app is closed: `meet2notes_status` reports the disconnected state and
the other tools return a retryable, user-readable error.

## Client configuration

Use the Python interpreter from the Meet2Notes virtual environment. There is no
separate MCP executable to sign or distribute.

Claude Desktop on Windows:

```json
{
  "mcpServers": {
    "meet2notes": {
      "command": "C:\\path\\to\\Meet2Notes\\.venv\\Scripts\\python.exe",
      "args": ["-m", "local_meeting_ai.mcp.server"]
    }
  }
}
```

Claude Desktop on Linux or macOS:

```json
{
  "mcpServers": {
    "meet2notes": {
      "command": "/path/to/Meet2Notes/.venv/bin/python",
      "args": ["-m", "local_meeting_ai.mcp.server"]
    }
  }
}
```

Clients whose schema requires a transport field can add `"type": "stdio"`.
Restart or reload the client after changing its configuration.

When Meet2Notes uses a custom data directory, pass the same override:

```json
{
  "env": {
    "M2N_DATA_DIR": "/absolute/path/to/Meet2NotesData"
  }
}
```

`M2N_MCP_BASE_URL=http://127.0.0.1:8899` can select an explicit local API URL.
Non-loopback URLs are rejected unless `M2N_MCP_ALLOW_REMOTE=1` is also set.

## Read-only tools

- `meet2notes_status`: app, database, queue, and RAG availability.
- `list_meetings`: text, ISO-date, and result-limit filters.
- `get_meeting`: meeting metadata, transcript versions, and AI-note versions.
- `get_transcript`: bounded pages of the active completed transcript with
  timestamps and speaker names.
- `get_summary`: bounded pages of specific AI notes or the newest completed
  notes for a meeting.
- `find_in_transcripts`: fast local FTS5 keyword search over active transcripts.
- `search_meetings`: conceptual hybrid RAG evidence with provenance.

`search_meetings` never builds or refreshes the index. It fails clearly when
RAG is disabled or the index is empty; indexing remains an explicit action in
Meet2Notes. The tool may load the configured embedding model to encode the
query, but every model remains owned by the single running app process.

The server exposes no recording control, imports, audio files, settings,
filesystem paths, model management, indexing, edits, or deletion tools.

## Lifecycle and logs

The AI client owns the MCP subprocess and normally stops it by closing its
standard input. Multiple clients may run separate MCP processes concurrently;
all of them use the same Meet2Notes application instance.

Standard output is reserved for MCP protocol messages. Server diagnostics use
standard error, so wrapper scripts that print banners or pause for input must
not be used as the configured command.

For a direct smoke test, start Meet2Notes and run the MCP Inspector against:

```text
<Meet2Notes Python> -m local_meeting_ai.mcp.server
```
