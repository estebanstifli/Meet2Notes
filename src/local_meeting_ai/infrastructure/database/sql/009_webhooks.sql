CREATE TABLE webhook_endpoints (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    url TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 0,
    mode TEXT NOT NULL CHECK(mode IN ('notification', 'live_agent')),
    events_json TEXT NOT NULL,
    content_level TEXT NOT NULL CHECK(content_level IN ('metadata', 'segments', 'full')),
    timeout_seconds REAL NOT NULL DEFAULT 10,
    max_attempts INTEGER NOT NULL DEFAULT 4,
    allow_private_network INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE webhook_events (
    id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    subject TEXT NOT NULL,
    meeting_id INTEGER,
    transcription_id INTEGER,
    data_json TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    expires_at TEXT,
    FOREIGN KEY(meeting_id) REFERENCES meetings(id) ON DELETE SET NULL,
    FOREIGN KEY(transcription_id) REFERENCES transcriptions(id) ON DELETE SET NULL
);

CREATE TABLE webhook_deliveries (
    id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL,
    endpoint_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN (
        'queued', 'delivering', 'retry', 'delivered', 'failed', 'expired'
    )),
    attempt_count INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT NOT NULL,
    last_status_code INTEGER,
    last_error TEXT,
    duration_ms INTEGER,
    response_excerpt TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT,
    FOREIGN KEY(event_id) REFERENCES webhook_events(id) ON DELETE CASCADE,
    FOREIGN KEY(endpoint_id) REFERENCES webhook_endpoints(id) ON DELETE CASCADE
);

CREATE INDEX idx_webhook_deliveries_due
ON webhook_deliveries(status, next_attempt_at);

CREATE INDEX idx_webhook_deliveries_endpoint_created
ON webhook_deliveries(endpoint_id, created_at DESC);

CREATE INDEX idx_webhook_events_meeting
ON webhook_events(meeting_id, occurred_at DESC);

CREATE TABLE webhook_insights (
    id TEXT PRIMARY KEY,
    endpoint_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    meeting_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    text TEXT NOT NULL,
    confidence REAL,
    related_segment_ids_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'new' CHECK(status IN ('new', 'accepted', 'dismissed')),
    created_at TEXT NOT NULL,
    FOREIGN KEY(endpoint_id) REFERENCES webhook_endpoints(id) ON DELETE CASCADE,
    FOREIGN KEY(event_id) REFERENCES webhook_events(id) ON DELETE CASCADE,
    FOREIGN KEY(meeting_id) REFERENCES meetings(id) ON DELETE CASCADE
);

CREATE INDEX idx_webhook_insights_meeting_created
ON webhook_insights(meeting_id, created_at DESC);
