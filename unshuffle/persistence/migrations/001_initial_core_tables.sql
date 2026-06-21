CREATE TABLE IF NOT EXISTS schema_version
(
    version INTEGER NOT NULL
);

INSERT INTO schema_version (version)
VALUES (1)
ON CONFLICT DO UPDATE SET version=version;

CREATE TABLE IF NOT EXISTS file_cache
(
    hash                  TEXT PRIMARY KEY,
    last_path             TEXT,
    size                  INTEGER,
    mtime                 REAL,
    first_seen            DATETIME DEFAULT CURRENT_TIMESTAMP,
    feature_vector        BLOB,
    feature_space_version TEXT,
    extractor_version     TEXT,
    feature_schema_json   TEXT,
    analysis_status       TEXT,
    analysis_tags_json    TEXT,
    updated_at            DATETIME
);

CREATE TABLE IF NOT EXISTS sessions
(
    session_id  TEXT PRIMARY KEY,
    timestamp   DATETIME DEFAULT CURRENT_TIMESTAMP,
    source_path TEXT,
    target_root TEXT,
    mode        TEXT,
    is_flat     BOOLEAN
);

CREATE TABLE IF NOT EXISTS session_sources
(
    session_id  TEXT,
    source_path TEXT,
    ordinal     INTEGER DEFAULT 0,
    PRIMARY KEY (session_id, source_path),
    FOREIGN KEY (session_id) REFERENCES sessions (session_id)
);

CREATE TABLE IF NOT EXISTS session_metadata
(
    session_id TEXT,
    key        TEXT,
    value_json TEXT,
    PRIMARY KEY (session_id, key),
    FOREIGN KEY (session_id) REFERENCES sessions (session_id)
);

CREATE TABLE IF NOT EXISTS records
(
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT,
    source_path     TEXT,
    target_path     TEXT,
    category        TEXT,
    subcategory     TEXT,
    pack            TEXT,
    file_hash       TEXT,
    confidence      REAL,
    status          TEXT,
    tags            TEXT,
    step_status     TEXT    DEFAULT 'PENDING',
    original_action TEXT,
    trash_path      TEXT,
    preserved_root  TEXT,
    is_preserved    INTEGER DEFAULT 0,
    FOREIGN KEY (session_id) REFERENCES sessions (session_id)
);


CREATE TABLE IF NOT EXISTS token_adjustments
(
    token         TEXT,
    category      TEXT,
    weight_offset REAL DEFAULT 0.0,
    PRIMARY KEY (token, category)
);

CREATE TABLE IF NOT EXISTS learned_correction_events
(
    source_key   TEXT,
    token        TEXT,
    old_category TEXT,
    new_category TEXT,
    created_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (source_key, token, old_category, new_category)
);

CREATE TABLE IF NOT EXISTS aliases
(
    alias    TEXT PRIMARY KEY,
    category TEXT NOT NULL,
    weight   REAL NOT NULL,
    source   TEXT DEFAULT 'system'
);

CREATE TABLE IF NOT EXISTS config_lists
(
    list_type TEXT,
    value     TEXT,
    PRIMARY KEY (list_type, value)
);

CREATE TABLE IF NOT EXISTS exclusions
(
    path      TEXT PRIMARY KEY,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS suppression_rules
(
    suppressor TEXT,
    target     TEXT,
    PRIMARY KEY (suppressor, target)
);

CREATE TABLE IF NOT EXISTS sub_taxonomy
(
    category     TEXT NOT NULL,
    token        TEXT NOT NULL,
    sub_category TEXT NOT NULL,
    PRIMARY KEY (category, token)
);

CREATE TABLE IF NOT EXISTS staging_records
(
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    row_id                INTEGER,
    session_id            TEXT,
    source_path           TEXT,
    sample_name           TEXT,
    pack                  TEXT,
    category              TEXT,
    subcategory           TEXT,
    audio_type            TEXT,
    tags                  TEXT,
    confidence            TEXT,
    duration              REAL,
    hash                  TEXT,
    pack_candidates       TEXT,
    evidence_json         TEXT,
    feature_vector        BLOB,
    feature_space_version TEXT,
    feature_schema_json   TEXT,
    analysis_status       TEXT,
    analysis_tags_json    TEXT,
    preserved_root        TEXT,
    is_preserved          INTEGER DEFAULT 0,
    FOREIGN KEY (session_id) REFERENCES sessions (session_id)
);