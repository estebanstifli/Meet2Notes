CREATE TABLE live_assistant_sessions (
    id TEXT PRIMARY KEY,
    meeting_id INTEGER NOT NULL,
    transcription_id INTEGER NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('active', 'stopped', 'interrupted', 'failed')),
    configuration_json TEXT NOT NULL,
    memory_json TEXT NOT NULL DEFAULT '{}',
    last_sequence INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    started_at TEXT NOT NULL,
    stopped_at TEXT,
    FOREIGN KEY(meeting_id) REFERENCES meetings(id) ON DELETE CASCADE,
    FOREIGN KEY(transcription_id) REFERENCES transcriptions(id) ON DELETE CASCADE
);

CREATE INDEX idx_live_assistant_sessions_meeting_started
ON live_assistant_sessions(meeting_id, started_at DESC);

CREATE TABLE live_assistant_insights (
    id TEXT PRIMARY KEY,
    session_id TEXT,
    meeting_id INTEGER NOT NULL,
    transcription_id INTEGER,
    kind TEXT NOT NULL,
    text TEXT NOT NULL,
    confidence REAL,
    related_segment_ids_json TEXT NOT NULL DEFAULT '[]',
    start_ms INTEGER,
    end_ms INTEGER,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    latency_ms INTEGER,
    status TEXT NOT NULL DEFAULT 'new' CHECK(status IN ('new', 'accepted', 'dismissed')),
    created_at TEXT NOT NULL,
    FOREIGN KEY(session_id) REFERENCES live_assistant_sessions(id) ON DELETE SET NULL,
    FOREIGN KEY(meeting_id) REFERENCES meetings(id) ON DELETE CASCADE,
    FOREIGN KEY(transcription_id) REFERENCES transcriptions(id) ON DELETE SET NULL
);

CREATE INDEX idx_live_assistant_insights_meeting_created
ON live_assistant_insights(meeting_id, created_at DESC);
