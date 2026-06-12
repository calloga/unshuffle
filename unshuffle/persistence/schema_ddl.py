import sqlite3


def create_core_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS file_cache (
            hash TEXT PRIMARY KEY,
            last_path TEXT,
            size INTEGER,
            mtime REAL,
            first_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
            feature_vector BLOB,
            feature_space_version TEXT,
            extractor_version TEXT,
            feature_schema_json TEXT,
            analysis_status TEXT,
            analysis_tags_json TEXT,
            updated_at DATETIME
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            source_path TEXT,
            target_root TEXT,
            mode TEXT,
            is_flat BOOLEAN
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS session_sources (
            session_id TEXT,
            source_path TEXT,
            ordinal INTEGER DEFAULT 0,
            PRIMARY KEY(session_id, source_path),
            FOREIGN KEY(session_id) REFERENCES sessions(session_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            source_path TEXT,
            target_path TEXT,
            category TEXT,
            subcategory TEXT,
            pack TEXT,
            file_hash TEXT,
            confidence REAL,
            status TEXT,
            tags TEXT,
            step_status TEXT DEFAULT 'PENDING',
            original_action TEXT,
            trash_path TEXT,
            preserved_root TEXT,
            is_preserved INTEGER DEFAULT 0,
            FOREIGN KEY(session_id) REFERENCES sessions(session_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS token_adjustments (
            token TEXT,
            category TEXT,
            weight_offset REAL DEFAULT 0.0,
            PRIMARY KEY(token, category)
        )
        """
    )
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
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS aliases (
            alias TEXT PRIMARY KEY,
            category TEXT NOT NULL,
            weight REAL NOT NULL,
            source TEXT DEFAULT 'system'
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS config_lists (
            list_type TEXT,
            value TEXT,
            PRIMARY KEY(list_type, value)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS exclusions (
            path TEXT PRIMARY KEY,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS suppression_rules (
            suppressor TEXT,
            target TEXT,
            PRIMARY KEY(suppressor, target)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sub_taxonomy (
            category TEXT NOT NULL,
            token TEXT NOT NULL,
            sub_category TEXT NOT NULL,
            PRIMARY KEY(category, token)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS staging_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            row_id INTEGER,
            session_id TEXT,
            source_path TEXT,
            sample_name TEXT,
            pack TEXT,
            category TEXT,
            subcategory TEXT,
            audio_type TEXT,
            tags TEXT,
            confidence TEXT,
            duration REAL,
            hash TEXT,
            pack_candidates TEXT,
            evidence_json TEXT,
            feature_vector BLOB,
            feature_space_version TEXT,
            feature_schema_json TEXT,
            analysis_status TEXT,
            analysis_tags_json TEXT,
            preserved_root TEXT,
            is_preserved INTEGER DEFAULT 0,
            FOREIGN KEY(session_id) REFERENCES sessions(session_id)
        )
        """
    )


def create_coherence_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS coherence_results (
            session_id TEXT NOT NULL,
            record_id TEXT NOT NULL,
            category TEXT,
            subcategory TEXT,
            coherence_status TEXT,
            coherence_score REAL,
            cluster_id TEXT,
            is_outlier INTEGER DEFAULT 0,
            review_reason TEXT,
            suggested_alternate_category TEXT,
            suggested_alternate_subcategory TEXT,
            nearest_neighbor_summary_json TEXT,
            anchor_fit_status TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(session_id, record_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS refinement_candidates (
            session_id TEXT NOT NULL,
            candidate_id TEXT NOT NULL,
            record_id TEXT NOT NULL,
            current_audio_type TEXT,
            current_category TEXT,
            current_subcategory TEXT,
            suggested_audio_type TEXT,
            suggested_category TEXT,
            suggested_subcategory TEXT,
            evidence TEXT,
            coherence_status TEXT,
            confidence_score REAL,
            state TEXT DEFAULT 'pending',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(session_id, candidate_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS anchor_profiles (
            session_id TEXT NOT NULL,
            anchor_id TEXT NOT NULL,
            audio_type TEXT,
            category TEXT,
            subcategory TEXT,
            cluster_id TEXT,
            feature_space_version TEXT,
            extractor_version TEXT,
            feature_schema_json TEXT,
            medoid_vector BLOB,
            cluster_centroid BLOB,
            cluster_std BLOB,
            coherence_radius REAL,
            n_reference_items INTEGER,
            state TEXT DEFAULT 'candidate',
            profile_json TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(session_id, anchor_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS coherence_review_decisions (
            source_path TEXT PRIMARY KEY,
            file_hash TEXT,
            decision_type TEXT NOT NULL,
            current_audio_type TEXT,
            current_category TEXT,
            current_subcategory TEXT,
            target_audio_type TEXT,
            target_category TEXT,
            target_subcategory TEXT,
            created_session_id TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def create_search_objects(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS staging_fts USING fts5(
            session_id UNINDEXED,
            row_id UNINDEXED,
            source_path UNINDEXED,
            sample_name, pack, category, subcategory, audio_type, tags,
            content='staging_records',
            content_rowid='id'
        )
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS staging_ai AFTER INSERT ON staging_records BEGIN
            INSERT INTO staging_fts(rowid, session_id, row_id, source_path, sample_name, pack, category, subcategory, audio_type, tags)
            VALUES (new.id, new.session_id, new.row_id, new.source_path, new.sample_name, new.pack, new.category, new.subcategory, new.audio_type, new.tags);
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS staging_ad AFTER DELETE ON staging_records BEGIN
            INSERT INTO staging_fts(staging_fts, rowid, session_id, row_id, source_path, sample_name, pack, category, subcategory, audio_type, tags)
            VALUES('delete', old.id, old.session_id, old.row_id, old.source_path, old.sample_name, old.pack, old.category, old.subcategory, old.audio_type, old.tags);
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS staging_au AFTER UPDATE ON staging_records BEGIN
            INSERT INTO staging_fts(staging_fts, rowid, session_id, row_id, source_path, sample_name, pack, category, subcategory, audio_type, tags)
            VALUES('delete', old.id, old.session_id, old.row_id, old.source_path, old.sample_name, old.pack, old.category, old.subcategory, old.audio_type, old.tags);
            INSERT INTO staging_fts(rowid, session_id, row_id, source_path, sample_name, pack, category, subcategory, audio_type, tags)
            VALUES (new.id, new.session_id, new.row_id, new.source_path, new.sample_name, new.pack, new.category, new.subcategory, new.audio_type, new.tags);
        END
        """
    )


def create_indexes(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cache_hash ON file_cache(hash)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cache_path ON file_cache(last_path)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cache_path_size_mtime ON file_cache(last_path, size, mtime)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_records_session ON records(session_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_records_status_file_hash ON records(status, file_hash)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_session_sources_session ON session_sources(session_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_staging_records_session ON staging_records(session_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_staging_records_session_row ON staging_records(session_id, row_id, id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_staging_records_session_source ON staging_records(session_id, source_path)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_coherence_results_session ON coherence_results(session_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_refinement_candidates_session_state ON refinement_candidates(session_id, state)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_anchor_profiles_session_state ON anchor_profiles(session_id, state)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_coherence_review_decisions_hash ON coherence_review_decisions(file_hash)")
