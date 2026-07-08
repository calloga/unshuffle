CREATE INDEX IF NOT EXISTS idx_staging_records_session_audio_type ON staging_records (session_id, audio_type);
CREATE INDEX IF NOT EXISTS idx_staging_records_session_category ON staging_records (session_id, category);
CREATE INDEX IF NOT EXISTS idx_staging_records_session_subcategory ON staging_records (session_id, subcategory);
CREATE INDEX IF NOT EXISTS idx_staging_records_session_pack ON staging_records (session_id, pack);
CREATE INDEX IF NOT EXISTS idx_staging_records_session_confidence ON staging_records (session_id, confidence);
