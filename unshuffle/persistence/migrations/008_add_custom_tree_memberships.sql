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
);

CREATE INDEX IF NOT EXISTS idx_custom_tree_memberships_parent
ON custom_tree_memberships (
    session_id, profile_id, projection_signature, parent_route_key, depth, sort_order, label
);

CREATE INDEX IF NOT EXISTS idx_custom_tree_memberships_row
ON custom_tree_memberships (session_id, profile_id, projection_signature, row_id);

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
