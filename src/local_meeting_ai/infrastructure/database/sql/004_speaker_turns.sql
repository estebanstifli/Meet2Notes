CREATE TABLE speaker_turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id INTEGER NOT NULL,
    transcription_id INTEGER NOT NULL,
    speaker_id INTEGER NOT NULL,
    start_ms INTEGER NOT NULL,
    end_ms INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(meeting_id) REFERENCES meetings(id) ON DELETE CASCADE,
    FOREIGN KEY(transcription_id) REFERENCES transcriptions(id) ON DELETE CASCADE,
    FOREIGN KEY(speaker_id) REFERENCES speakers(id) ON DELETE CASCADE,
    CHECK(end_ms > start_ms)
);

CREATE INDEX idx_speaker_turns_transcription
    ON speaker_turns(transcription_id, start_ms);

CREATE INDEX idx_speaker_turns_speaker
    ON speaker_turns(speaker_id, start_ms);

-- Existing diarizations predate persisted voice turns. Segment boundaries are
-- a safe compatibility fallback; future runs store Sherpa's exact intervals.
INSERT INTO speaker_turns(
    meeting_id, transcription_id, speaker_id,
    start_ms, end_ms, created_at
)
SELECT
    t.meeting_id,
    ts.transcription_id,
    ts.speaker_id,
    ts.start_ms,
    ts.end_ms,
    strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
FROM transcript_segments ts
JOIN transcriptions t ON t.id = ts.transcription_id
WHERE ts.speaker_id IS NOT NULL AND ts.end_ms > ts.start_ms;
