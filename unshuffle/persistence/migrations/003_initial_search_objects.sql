CREATE VIRTUAL TABLE IF NOT EXISTS staging_fts USING fts5
(
    session_id UNINDEXED,
    row_id UNINDEXED,
    source_path UNINDEXED,
    sample_name,
    pack,
    category,
    subcategory,
    audio_type,
    tags,
    content='staging_records',
    content_rowid='id'
);

CREATE TRIGGER IF NOT EXISTS staging_ai
    AFTER INSERT
    ON staging_records
BEGIN
    INSERT INTO staging_fts(rowid, session_id, row_id, source_path, sample_name, pack, category, subcategory,
                            audio_type, tags)
    VALUES (new.id, new.session_id, new.row_id, new.source_path, new.sample_name, new.pack, new.category,
            new.subcategory, new.audio_type, new.tags);
END;

CREATE TRIGGER IF NOT EXISTS staging_ad
    AFTER DELETE
    ON staging_records
BEGIN
    INSERT INTO staging_fts(staging_fts, rowid, session_id, row_id, source_path, sample_name, pack, category,
                            subcategory, audio_type, tags)
    VALUES ('delete', old.id, old.session_id, old.row_id, old.source_path, old.sample_name, old.pack, old.category,
            old.subcategory, old.audio_type, old.tags);
END;

CREATE TRIGGER IF NOT EXISTS staging_au
    AFTER UPDATE
    ON staging_records
BEGIN
    INSERT INTO staging_fts(staging_fts, rowid, session_id, row_id, source_path, sample_name, pack, category,
                            subcategory, audio_type, tags)
    VALUES ('delete', old.id, old.session_id, old.row_id, old.source_path, old.sample_name, old.pack, old.category,
            old.subcategory, old.audio_type, old.tags);
    INSERT INTO staging_fts(rowid, session_id, row_id, source_path, sample_name, pack, category, subcategory,
                            audio_type, tags)
    VALUES (new.id, new.session_id, new.row_id, new.source_path, new.sample_name, new.pack, new.category,
            new.subcategory, new.audio_type, new.tags);
END;