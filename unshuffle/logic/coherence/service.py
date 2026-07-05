from __future__ import annotations

from typing import Callable

from .anchor_profiles import generate_anchor_candidates
from .coherence_engine import CoherenceEngine
from .models import CoherenceRunSummary, REFINEMENT_AUTO_STAGED
from .vector_index import records_from_staging_rows, valid_coherence_vector


def run_coherence_audit(
    db,
    session_id: str,
    *,
    force: bool = False,
    progress_callback: Callable[[dict], None] | None = None,
) -> CoherenceRunSummary:
    if hasattr(db, "ensure_verified_anchors_for_session"):
        db.ensure_verified_anchors_for_session(session_id)
    rows = db.get_staging_records(session_id)
    records, stats = records_from_staging_rows(rows)
    if not force and not stats.can_run:
        return CoherenceRunSummary(
            total_records=stats.total_records,
            eligible_records=stats.eligible_records,
            valid_vector_records=stats.valid_vector_records,
            coverage=stats.coverage,
            ran=False,
            reason="Coherence needs more indexed audio",
        )

    engine = CoherenceEngine(verified_anchors=_verified_anchors(db, session_id))
    results, candidates = engine.audit(records, progress_callback=progress_callback)
    if progress_callback is not None:
        progress_callback(
            {
                "phase": "Checking Library Coherence",
                "message": "Generating coherence anchors...",
                "current": 1,
                "total": 3,
            }
        )
    anchors = generate_anchor_candidates(records, results, engine.similarity_engine)
    if progress_callback is not None:
        progress_callback(
            {
                "phase": "Checking Library Coherence",
                "message": "Saving coherence results...",
                "current": 2,
                "total": 3,
            }
        )
    if hasattr(db, "upsert_coherence_audit"):
        db.upsert_coherence_audit(session_id, results, candidates, anchors)
    else:
        db.upsert_coherence_results(session_id, results)
        db.upsert_refinement_candidates(session_id, candidates)
        db.upsert_anchor_candidates(session_id, anchors)
    if progress_callback is not None:
        progress_callback(
            {
                "phase": "Checking Library Coherence",
                "message": "Saving coherence results...",
                "current": 3,
                "total": 3,
            }
        )
    if hasattr(db, "count_refinement_candidates"):
        pending_count = db.count_refinement_candidates(session_id, state="pending")
        auto_staged_count = db.count_refinement_candidates(session_id, state=REFINEMENT_AUTO_STAGED)
    else:
        pending_count = len(db.list_refinement_candidates(session_id, state="pending"))
        auto_staged_count = len(db.list_refinement_candidates(session_id, state=REFINEMENT_AUTO_STAGED))
    return CoherenceRunSummary(
        total_records=stats.total_records,
        eligible_records=stats.eligible_records,
        valid_vector_records=stats.valid_vector_records,
        coverage=stats.coverage,
        ran=True,
        result_count=len(results),
        pending_candidate_count=pending_count,
        auto_staged_candidate_count=auto_staged_count,
        anchor_candidate_count=len(anchors),
    )


def _verified_anchors(db, session_id: str) -> list[dict]:
    anchors = []
    for row in db.list_anchor_candidates(session_id, state="verified"):
        vector = valid_coherence_vector(row.get("medoid_vector"))
        if vector is None:
            continue
        try:
            radius = float(row.get("coherence_radius") or 0.0)
        except (TypeError, ValueError):
            continue
        anchors.append(
            {
                "audio_type": row.get("audio_type"),
                "category": row.get("category"),
                "subcategory": row.get("subcategory"),
                "medoid_vector": vector,
                "coherence_radius": radius,
            }
        )
    return anchors
