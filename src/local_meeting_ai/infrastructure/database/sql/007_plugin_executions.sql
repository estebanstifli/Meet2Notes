CREATE TABLE plugin_executions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plugin_id TEXT NOT NULL,
    plugin_version TEXT NOT NULL,
    hook TEXT NOT NULL,
    kind TEXT NOT NULL,
    pipeline_id TEXT,
    job_uuid TEXT,
    meeting_id INTEGER,
    transcription_id INTEGER,
    status TEXT NOT NULL,
    duration_ms INTEGER,
    input_digest TEXT,
    output_digest TEXT,
    message TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT,
    FOREIGN KEY(meeting_id) REFERENCES meetings(id) ON DELETE SET NULL,
    FOREIGN KEY(transcription_id) REFERENCES transcriptions(id) ON DELETE SET NULL
);

CREATE INDEX idx_plugin_executions_plugin_created
ON plugin_executions(plugin_id, created_at DESC);

CREATE INDEX idx_plugin_executions_job
ON plugin_executions(job_uuid, created_at);
