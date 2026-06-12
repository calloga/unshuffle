import sqlite3


def ensure_schema_version(conn: sqlite3.Connection, schema_version: int) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER NOT NULL
        )
        """
    )
    if conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0] == 0:
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (schema_version,))
    else:
        conn.execute("UPDATE schema_version SET version = ?", (schema_version,))


def ensure_feature_schema_columns(conn: sqlite3.Connection) -> None:
    def columns(table: str) -> set[str]:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}

    records_cols = columns("records")
    records_additions = {
        "status": "ALTER TABLE records ADD COLUMN status TEXT",
        "tags": "ALTER TABLE records ADD COLUMN tags TEXT",
        "step_status": "ALTER TABLE records ADD COLUMN step_status TEXT DEFAULT 'PENDING'",
        "original_action": "ALTER TABLE records ADD COLUMN original_action TEXT",
        "trash_path": "ALTER TABLE records ADD COLUMN trash_path TEXT",
        "preserved_root": "ALTER TABLE records ADD COLUMN preserved_root TEXT",
        "is_preserved": "ALTER TABLE records ADD COLUMN is_preserved INTEGER DEFAULT 0",
    }
    for name, statement in records_additions.items():
        if name not in records_cols:
            conn.execute(statement)

    file_cache_cols = columns("file_cache")
    additions = {
        "feature_vector": "ALTER TABLE file_cache ADD COLUMN feature_vector BLOB",
        "feature_space_version": "ALTER TABLE file_cache ADD COLUMN feature_space_version TEXT",
        "extractor_version": "ALTER TABLE file_cache ADD COLUMN extractor_version TEXT",
        "feature_schema_json": "ALTER TABLE file_cache ADD COLUMN feature_schema_json TEXT",
        "analysis_status": "ALTER TABLE file_cache ADD COLUMN analysis_status TEXT",
        "analysis_tags_json": "ALTER TABLE file_cache ADD COLUMN analysis_tags_json TEXT",
        "updated_at": "ALTER TABLE file_cache ADD COLUMN updated_at DATETIME",
    }
    for name, statement in additions.items():
        if name not in file_cache_cols:
            conn.execute(statement)

    staging_cols = columns("staging_records")
    additions = {
        "feature_vector": "ALTER TABLE staging_records ADD COLUMN feature_vector BLOB",
        "feature_space_version": "ALTER TABLE staging_records ADD COLUMN feature_space_version TEXT",
        "feature_schema_json": "ALTER TABLE staging_records ADD COLUMN feature_schema_json TEXT",
        "analysis_status": "ALTER TABLE staging_records ADD COLUMN analysis_status TEXT",
        "analysis_tags_json": "ALTER TABLE staging_records ADD COLUMN analysis_tags_json TEXT",
        "evidence_json": "ALTER TABLE staging_records ADD COLUMN evidence_json TEXT",
    }
    for name, statement in additions.items():
        if name not in staging_cols:
            conn.execute(statement)

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS learned_correction_events (
            source_key TEXT,
            token TEXT,
            old_category TEXT,
            new_category TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(source_key, token, old_category, new_category)
        )
        """
    )

    anchor_cols = columns("anchor_profiles")
    if "feature_schema_json" not in anchor_cols:
        conn.execute("ALTER TABLE anchor_profiles ADD COLUMN feature_schema_json TEXT")
