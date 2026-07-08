CREATE INDEX IF NOT EXISTS idx_staging_records_session_pack_order
ON staging_records (session_id, pack COLLATE NOCASE, sample_name COLLATE NOCASE, row_id);

CREATE INDEX IF NOT EXISTS idx_staging_records_session_category_order
ON staging_records (session_id, category COLLATE NOCASE, sample_name COLLATE NOCASE, row_id);

CREATE INDEX IF NOT EXISTS idx_staging_records_session_subcategory_order
ON staging_records (session_id, subcategory COLLATE NOCASE, sample_name COLLATE NOCASE, row_id);

CREATE INDEX IF NOT EXISTS idx_staging_records_session_audio_type_order
ON staging_records (session_id, audio_type COLLATE NOCASE, sample_name COLLATE NOCASE, row_id);

CREATE INDEX IF NOT EXISTS idx_staging_records_session_filename_order
ON staging_records (session_id, sample_name COLLATE NOCASE, row_id);

CREATE INDEX IF NOT EXISTS idx_staging_records_session_confidence_order
ON staging_records (session_id, CAST(confidence AS REAL), sample_name COLLATE NOCASE, row_id);
