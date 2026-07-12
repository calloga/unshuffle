CREATE TABLE IF NOT EXISTS scan_runs (
    scan_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    target_root TEXT NOT NULL,
    roots_json TEXT NOT NULL,
    mode TEXT NOT NULL DEFAULT 'new',
    state TEXT NOT NULL DEFAULT 'running',
    phase TEXT NOT NULL DEFAULT 'discovery',
    hash_version TEXT,
    feature_version TEXT,
    taxonomy_version TEXT,
    classification_version TEXT,
    tagging_version TEXT,
    coherence_version TEXT,
    source_signature TEXT,
    discovered_count INTEGER NOT NULL DEFAULT 0,
    completed_count INTEGER NOT NULL DEFAULT 0,
    error_count INTEGER NOT NULL DEFAULT 0,
    last_error_json TEXT,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_scan_runs_session
ON scan_runs(session_id, updated_at);

CREATE INDEX IF NOT EXISTS idx_scan_runs_state
ON scan_runs(state, updated_at);

CREATE TABLE IF NOT EXISTS scan_directories (
    scan_id TEXT NOT NULL,
    directory_id INTEGER NOT NULL,
    discovery_order INTEGER NOT NULL,
    parent_directory_id INTEGER,
    depth INTEGER NOT NULL DEFAULT 0,
    normalized_path TEXT NOT NULL,
    display_name TEXT NOT NULL,
    is_preserved INTEGER NOT NULL DEFAULT 0,
    is_protected INTEGER NOT NULL DEFAULT 0,
    token_blob BLOB,
    descendant_token_blob BLOB,
    immediate_directory_count INTEGER NOT NULL DEFAULT 0,
    immediate_file_count INTEGER NOT NULL DEFAULT 0,
    descendant_count INTEGER NOT NULL DEFAULT 0,
    role_flags INTEGER NOT NULL DEFAULT 0,
    pack_weight REAL NOT NULL DEFAULT 0.0,
    weight_evidence_json TEXT NOT NULL DEFAULT '{}',
    structure_state TEXT NOT NULL DEFAULT 'pending',
    PRIMARY KEY (scan_id, directory_id),
    UNIQUE (scan_id, normalized_path),
    FOREIGN KEY (scan_id) REFERENCES scan_runs(scan_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_scan_directories_parent
ON scan_directories(scan_id, parent_directory_id, directory_id);

CREATE INDEX IF NOT EXISTS idx_scan_directories_order
ON scan_directories(scan_id, discovery_order);

CREATE INDEX IF NOT EXISTS idx_scan_directories_depth_state
ON scan_directories(scan_id, depth DESC, structure_state, directory_id);

CREATE TABLE IF NOT EXISTS scan_items (
    scan_id TEXT NOT NULL,
    item_id INTEGER NOT NULL,
    discovery_order INTEGER NOT NULL,
    parent_directory_id INTEGER NOT NULL,
    normalized_path TEXT NOT NULL,
    sample_name TEXT NOT NULL,
    extension TEXT NOT NULL DEFAULT '',
    size INTEGER NOT NULL DEFAULT 0,
    mtime REAL,
    mtime_ns INTEGER,
    is_preserved INTEGER NOT NULL DEFAULT 0,
    is_protected INTEGER NOT NULL DEFAULT 0,
    is_supported_audio INTEGER NOT NULL DEFAULT 0,
    discovery_state TEXT NOT NULL DEFAULT 'ready',
    hash_state TEXT NOT NULL DEFAULT 'pending',
    fast_hash TEXT,
    effective_hash TEXT,
    analysis_state TEXT NOT NULL DEFAULT 'pending',
    analysis_error_code TEXT,
    analysis_error_text TEXT,
    analysis_attempts INTEGER NOT NULL DEFAULT 0,
    canonical_analysis_item_id INTEGER,
    classification_state TEXT NOT NULL DEFAULT 'pending',
    pack TEXT,
    category TEXT,
    subcategory TEXT,
    audio_type TEXT,
    confidence TEXT,
    duration REAL,
    tags TEXT,
    pack_candidates TEXT,
    evidence_json TEXT,
    analysis_status TEXT,
    analysis_tags_json TEXT,
    duplicate_of_item_id INTEGER,
    staging_state TEXT NOT NULL DEFAULT 'pending',
    claimed_at DATETIME,
    claim_owner TEXT,
    PRIMARY KEY (scan_id, item_id),
    UNIQUE (scan_id, normalized_path),
    FOREIGN KEY (scan_id) REFERENCES scan_runs(scan_id) ON DELETE CASCADE,
    FOREIGN KEY (scan_id, parent_directory_id)
        REFERENCES scan_directories(scan_id, directory_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_scan_items_discovery
ON scan_items(scan_id, discovery_state, item_id);

CREATE INDEX IF NOT EXISTS idx_scan_items_order
ON scan_items(scan_id, discovery_order);

CREATE INDEX IF NOT EXISTS idx_scan_items_hash
ON scan_items(scan_id, hash_state, item_id);

CREATE INDEX IF NOT EXISTS idx_scan_items_fast_hash
ON scan_items(scan_id, size, fast_hash);

CREATE INDEX IF NOT EXISTS idx_scan_items_effective_hash
ON scan_items(scan_id, effective_hash);

CREATE INDEX IF NOT EXISTS idx_scan_items_analysis
ON scan_items(scan_id, is_supported_audio, analysis_state, item_id);

CREATE INDEX IF NOT EXISTS idx_scan_items_classification
ON scan_items(scan_id, classification_state, item_id);

CREATE INDEX IF NOT EXISTS idx_scan_items_staging
ON scan_items(scan_id, staging_state, item_id);

CREATE INDEX IF NOT EXISTS idx_scan_items_parent
ON scan_items(scan_id, parent_directory_id, item_id);

CREATE INDEX IF NOT EXISTS idx_scan_items_group
ON scan_items(scan_id, audio_type, category, subcategory, item_id);
