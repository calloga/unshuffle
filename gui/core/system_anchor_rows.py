from __future__ import annotations

from gui.core.taxonomy_anchor_records import add_anchor_consistency, anchor_neighbors, enrich_anchor_candidate_rows


def rows_for_controller(controller) -> tuple[list[dict], list[dict]]:
    engine = controller._engine()
    candidate_rows: list[dict] = []
    verified_rows: list[dict] = []
    if not engine or not getattr(engine, "db", None):
        return candidate_rows, verified_rows

    if hasattr(engine.db, "ensure_verified_anchors_for_session"):
        engine.db.ensure_verified_anchors_for_session(engine.session_id)
    candidate_rows = engine.db.list_anchor_candidates(engine.session_id, state="candidate")
    verified_rows = engine.db.list_anchor_candidates(engine.session_id, state="verified")
    all_rows = enrich_anchor_candidate_rows(engine, candidate_rows + verified_rows)
    all_rows = add_anchor_consistency(all_rows)
    candidate_ids = {row.get("anchor_id") for row in candidate_rows}
    verified_ids = {row.get("anchor_id") for row in verified_rows}
    return (
        [row for row in all_rows if row.get("anchor_id") in candidate_ids],
        [row for row in all_rows if row.get("anchor_id") in verified_ids],
    )

