CREATE UNIQUE INDEX IF NOT EXISTS idx_speaker_profiles_name_nocase
ON speaker_profiles(name COLLATE NOCASE);

CREATE INDEX IF NOT EXISTS idx_speakers_profile_id ON speakers(profile_id);
