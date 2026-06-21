CREATE TABLE IF NOT EXISTS coherence_results
(
    session_id                      TEXT NOT NULL,
    record_id                       TEXT NOT NULL,
    category                        TEXT,
    subcategory                     TEXT,
    coherence_status                TEXT,
    coherence_score                 REAL,
    cluster_id                      TEXT,
    is_outlier                      INTEGER  DEFAULT 0,
    review_reason                   TEXT,
    suggested_alternate_category    TEXT,
    suggested_alternate_subcategory TEXT,
    nearest_neighbor_summary_json   TEXT,
    anchor_fit_status               TEXT,
    created_at                      DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at                      DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (session_id, record_id)
);

CREATE TABLE IF NOT EXISTS refinement_candidates
(
    session_id            TEXT NOT NULL,
    candidate_id          TEXT NOT NULL,
    record_id             TEXT NOT NULL,
    current_audio_type    TEXT,
    current_category      TEXT,
    current_subcategory   TEXT,
    suggested_audio_type  TEXT,
    suggested_category    TEXT,
    suggested_subcategory TEXT,
    evidence              TEXT,
    coherence_status      TEXT,
    confidence_score      REAL,
    state                 TEXT     DEFAULT 'pending',
    created_at            DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at            DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (session_id, candidate_id)
);

CREATE TABLE IF NOT EXISTS anchor_profiles
(
    session_id            TEXT NOT NULL,
    anchor_id             TEXT NOT NULL,
    audio_type            TEXT,
    category              TEXT,
    subcategory           TEXT,
    cluster_id            TEXT,
    feature_space_version TEXT,
    extractor_version     TEXT,
    feature_schema_json   TEXT,
    medoid_vector         BLOB,
    cluster_centroid      BLOB,
    cluster_std           BLOB,
    coherence_radius      REAL,
    n_reference_items     INTEGER,
    state                 TEXT     DEFAULT 'candidate',
    profile_json          TEXT,
    created_at            DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at            DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (session_id, anchor_id)
);

CREATE TABLE IF NOT EXISTS coherence_review_decisions
(
    source_path         TEXT PRIMARY KEY,
    file_hash           TEXT,
    decision_type       TEXT NOT NULL,
    current_audio_type  TEXT,
    current_category    TEXT,
    current_subcategory TEXT,
    target_audio_type   TEXT,
    target_category     TEXT,
    target_subcategory  TEXT,
    created_session_id  TEXT,
    created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at          DATETIME DEFAULT CURRENT_TIMESTAMP
);