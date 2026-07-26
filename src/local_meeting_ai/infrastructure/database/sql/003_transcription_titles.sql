ALTER TABLE transcriptions ADD COLUMN title TEXT;

UPDATE transcriptions
SET title = CASE
    WHEN id = (SELECT MIN(id) FROM transcriptions) THEN 'New Transcription'
    ELSE 'New Transcription ' || id
END
WHERE title IS NULL;
