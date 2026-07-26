CREATE INDEX idx_transcriptions_active
ON transcriptions(meeting_id, is_active, created_at DESC);

CREATE INDEX idx_transcript_segments_time
ON transcript_segments(transcription_id, start_ms, end_ms);

CREATE TRIGGER transcript_segments_search_insert
AFTER INSERT ON transcript_segments
BEGIN
    INSERT INTO transcript_search(meeting_id, segment_id, speaker_name, text)
    SELECT t.meeting_id, NEW.id, '', NEW.text
    FROM transcriptions t
    WHERE t.id = NEW.transcription_id;
END;

CREATE TRIGGER transcript_segments_search_update
AFTER UPDATE OF text ON transcript_segments
BEGIN
    UPDATE transcript_search
    SET text = NEW.text
    WHERE segment_id = NEW.id;
END;

CREATE TRIGGER transcript_segments_search_delete
AFTER DELETE ON transcript_segments
BEGIN
    DELETE FROM transcript_search WHERE segment_id = OLD.id;
END;
