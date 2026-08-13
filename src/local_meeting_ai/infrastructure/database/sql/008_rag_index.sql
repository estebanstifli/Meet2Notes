CREATE TABLE rag_chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id INTEGER NOT NULL,
    transcription_id INTEGER NOT NULL,
    chunk_index INTEGER NOT NULL,
    start_ms INTEGER NOT NULL,
    end_ms INTEGER NOT NULL,
    text TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    embedding_provider TEXT NOT NULL,
    embedding_model TEXT NOT NULL,
    embedding_dimensions INTEGER NOT NULL,
    embedding BLOB NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(meeting_id) REFERENCES meetings(id) ON DELETE CASCADE,
    FOREIGN KEY(transcription_id) REFERENCES transcriptions(id) ON DELETE CASCADE,
    UNIQUE(transcription_id, chunk_index)
);

CREATE INDEX idx_rag_chunks_meeting
ON rag_chunks(meeting_id, chunk_index);

CREATE INDEX idx_rag_chunks_embedding_model
ON rag_chunks(embedding_provider, embedding_model, embedding_dimensions);
