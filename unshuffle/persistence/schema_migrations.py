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

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS session_metadata (
            session_id TEXT,
            key TEXT,
            value_json TEXT,
            PRIMARY KEY(session_id, key),
            FOREIGN KEY(session_id) REFERENCES sessions(session_id)
        )
        """
    )

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


def ensure_staging_view_indexes(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE INDEX IF NOT EXISTS idx_staging_records_session_audio_type ON staging_records(session_id, audio_type)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_staging_records_session_category ON staging_records(session_id, category)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_staging_records_session_subcategory ON staging_records(session_id, subcategory)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_staging_records_session_pack ON staging_records(session_id, pack)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_staging_records_session_confidence ON staging_records(session_id, confidence)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_staging_records_session_pack_order ON staging_records(session_id, pack COLLATE NOCASE, sample_name COLLATE NOCASE, row_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_staging_records_session_category_order ON staging_records(session_id, category COLLATE NOCASE, sample_name COLLATE NOCASE, row_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_staging_records_session_subcategory_order ON staging_records(session_id, subcategory COLLATE NOCASE, sample_name COLLATE NOCASE, row_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_staging_records_session_audio_type_order ON staging_records(session_id, audio_type COLLATE NOCASE, sample_name COLLATE NOCASE, row_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_staging_records_session_filename_order ON staging_records(session_id, sample_name COLLATE NOCASE, row_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_staging_records_session_confidence_order ON staging_records(session_id, CAST(confidence AS REAL), sample_name COLLATE NOCASE, row_id)")


def ensure_custom_tree_memberships(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS custom_tree_memberships (
            session_id TEXT NOT NULL,
            profile_id TEXT NOT NULL,
            projection_signature TEXT NOT NULL,
            route_key TEXT NOT NULL,
            parent_route_key TEXT NOT NULL,
            label TEXT NOT NULL,
            node_type TEXT NOT NULL,
            semantic_fields_json TEXT NOT NULL DEFAULT '{}',
            source_node_id TEXT,
            source_node_type TEXT,
            read_only INTEGER NOT NULL DEFAULT 0,
            residual INTEGER NOT NULL DEFAULT 0,
            sort_order INTEGER NOT NULL DEFAULT 0,
            depth INTEGER NOT NULL,
            row_id INTEGER NOT NULL,
            PRIMARY KEY (session_id, profile_id, projection_signature, route_key, row_id),
            FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
        )
        """
    )
    columns = {
        str(row[1])
        for row in conn.execute("PRAGMA table_info(custom_tree_memberships)").fetchall()
    }
    if "sort_order" not in columns:
        conn.execute(
            "ALTER TABLE custom_tree_memberships "
            "ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0"
        )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_custom_tree_memberships_parent "
        "ON custom_tree_memberships(session_id, profile_id, projection_signature, parent_route_key, depth, sort_order, label)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_custom_tree_memberships_row "
        "ON custom_tree_memberships(session_id, profile_id, projection_signature, row_id)"
    )
    conn.executescript(
        """
        CREATE TRIGGER IF NOT EXISTS custom_tree_memberships_staging_ai
        AFTER INSERT ON staging_records BEGIN
            DELETE FROM custom_tree_memberships WHERE session_id = new.session_id;
        END;
        CREATE TRIGGER IF NOT EXISTS custom_tree_memberships_staging_au
        AFTER UPDATE ON staging_records BEGIN
            DELETE FROM custom_tree_memberships WHERE session_id = new.session_id;
        END;
        CREATE TRIGGER IF NOT EXISTS custom_tree_memberships_staging_ad
        AFTER DELETE ON staging_records BEGIN
            DELETE FROM custom_tree_memberships WHERE session_id = old.session_id;
        END;
        """
    )


def ensure_fast_hash_columns(conn: sqlite3.Connection) -> None:
    def columns(table: str) -> set[str]:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}

    additions = {
        "file_cache": "ALTER TABLE file_cache ADD COLUMN fast_hash TEXT",
        "records": "ALTER TABLE records ADD COLUMN fast_hash TEXT",
        "staging_records": "ALTER TABLE staging_records ADD COLUMN fast_hash TEXT",
    }
    for table, statement in additions.items():
        if "fast_hash" not in columns(table):
            conn.execute(statement)

    conn.execute("CREATE INDEX IF NOT EXISTS idx_cache_fast_hash ON file_cache(fast_hash)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_records_status_fast_hash ON records(status, fast_hash)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_staging_records_fast_hash ON staging_records(fast_hash)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_staging_records_session_audio_type ON staging_records(session_id, audio_type)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_staging_records_session_category ON staging_records(session_id, category)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_staging_records_session_subcategory ON staging_records(session_id, subcategory)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_staging_records_session_pack ON staging_records(session_id, pack)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_staging_records_session_confidence ON staging_records(session_id, confidence)")
    ensure_staging_view_indexes(conn)
