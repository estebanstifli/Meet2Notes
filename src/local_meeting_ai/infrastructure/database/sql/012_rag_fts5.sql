CREATE VIRTUAL TABLE rag_chunks_fts USING fts5(
    text,
    meeting_title,
    meeting_description,
    meeting_id UNINDEXED,
    chunk_id UNINDEXED,
    tokenize = 'unicode61 remove_diacritics 2'
);

INSERT INTO rag_chunks_fts(
    text, meeting_title, meeting_description, meeting_id, chunk_id
)
SELECT rc.text, m.title, COALESCE(m.description, ''), rc.meeting_id, rc.id
FROM rag_chunks rc
JOIN meetings m ON m.id = rc.meeting_id;

CREATE TRIGGER rag_chunks_fts_after_insert
AFTER INSERT ON rag_chunks
BEGIN
    INSERT INTO rag_chunks_fts(
        text, meeting_title, meeting_description, meeting_id, chunk_id
    )
    SELECT NEW.text, m.title, COALESCE(m.description, ''), NEW.meeting_id, NEW.id
    FROM meetings m WHERE m.id = NEW.meeting_id;
END;

CREATE TRIGGER rag_chunks_fts_after_delete
AFTER DELETE ON rag_chunks
BEGIN
    DELETE FROM rag_chunks_fts WHERE chunk_id = OLD.id;
END;

CREATE TRIGGER rag_chunks_fts_after_meeting_update
AFTER UPDATE OF title, description ON meetings
BEGIN
    UPDATE rag_chunks_fts
    SET meeting_title = NEW.title,
        meeting_description = COALESCE(NEW.description, '')
    WHERE meeting_id = NEW.id;
END;
