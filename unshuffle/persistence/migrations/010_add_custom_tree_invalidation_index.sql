CREATE INDEX IF NOT EXISTS idx_custom_tree_memberships_session_row
ON custom_tree_memberships (session_id, row_id);
