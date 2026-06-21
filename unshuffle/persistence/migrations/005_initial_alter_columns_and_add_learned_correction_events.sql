-- records table
ALTER TABLE records ADD COLUMN status TEXT;
ALTER TABLE records ADD COLUMN tags TEXT;
ALTER TABLE records ADD COLUMN step_status TEXT DEFAULT 'PENDING';
ALTER TABLE records ADD COLUMN original_action TEXT;
ALTER TABLE records ADD COLUMN trash_path TEXT;
ALTER TABLE records ADD COLUMN preserved_root TEXT;
ALTER TABLE records ADD COLUMN is_preserved INTEGER DEFAULT 0;

-- create session_metadata
CREATE TABLE IF NOT EXISTS session_metadata (
            session_id TEXT,
            key TEXT,
            value_json TEXT,
            PRIMARY KEY(session_id, key),
            FOREIGN KEY(session_id) REFERENCES sessions(session_id)
);

-- alter file_cache
ALTER TABLE file_cache ADD COLUMN feature_vector BLOB;
ALTER TABLE file_cache ADD COLUMN feature_space_version TEXT;
ALTER TABLE file_cache ADD COLUMN extractor_version TEXT;
ALTER TABLE file_cache ADD COLUMN feature_schema_json TEXT;
ALTER TABLE file_cache ADD COLUMN analysis_status TEXT;
ALTER TABLE file_cache ADD COLUMN analysis_tags_json TEXT;
ALTER TABLE file_cache ADD COLUMN updated_at DATETIME;

-- alter staging records
ALTER TABLE staging_records ADD COLUMN feature_vector BLOB;
ALTER TABLE staging_records ADD COLUMN feature_space_version TEXT;
ALTER TABLE staging_records ADD COLUMN feature_schema_json TEXT;
ALTER TABLE staging_records ADD COLUMN analysis_status TEXT;
ALTER TABLE staging_records ADD COLUMN analysis_tags_json TEXT;
ALTER TABLE staging_records ADD COLUMN evidence_json TEXT;

-- create learned_correction_events
CREATE TABLE IF NOT EXISTS learned_correction_events (
            source_key TEXT,
            token TEXT,
            old_category TEXT,
            new_category TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(source_key, token, old_category, new_category)
);

-- alter anchor profiles
ALTER TABLE anchor_profiles ADD COLUMN feature_schema_json TEXT;