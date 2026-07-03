ALTER TABLE file_cache ADD COLUMN fast_hash TEXT;
ALTER TABLE records ADD COLUMN fast_hash TEXT;
ALTER TABLE staging_records ADD COLUMN fast_hash TEXT;

CREATE INDEX IF NOT EXISTS idx_cache_fast_hash ON file_cache(fast_hash);
CREATE INDEX IF NOT EXISTS idx_records_status_fast_hash ON records(status, fast_hash);
CREATE INDEX IF NOT EXISTS idx_staging_records_fast_hash ON staging_records(fast_hash);