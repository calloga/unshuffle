from __future__ import annotations

import logging
import time
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
    started_at = time.perf_counter()
    if progress_callback is not None:
        progress_callback(
            {
                "phase": "Checking Library Coherence",
                "message": "Loading coherence vectors...",
                "current": 0,
                "total": 3,
            }
        )
    if hasattr(db, "ensure_verified_anchors_for_session"):
        db.ensure_verified_anchors_for_session(session_id)
    ensure_elapsed = time.perf_counter()
    if hasattr(db, "iter_coherence_staging_records"):
        rows = (
            row
            for batch in db.iter_coherence_staging_records(session_id, batch_size=1000)
            for row in batch
        )
        row_count = None
    elif hasattr(db, "get_coherence_staging_records"):
        rows = db.get_coherence_staging_records(session_id)
        row_count = len(rows)
    else:
        rows = db.get_staging_records(session_id)
        row_count = len(rows)
    rows_elapsed = time.perf_counter()
    records, stats = records_from_staging_rows(rows)
    index_elapsed = time.perf_counter()
    logging.info(
        "Coherence audit loaded %s row(s), %s eligible vector record(s) in %.2fs (anchors %.2fs, rows %.2fs, index %.2fs).",
        stats.total_records if row_count is None else row_count,
        stats.valid_vector_records,
        index_elapsed - started_at,
        ensure_elapsed - started_at,
        rows_elapsed - ensure_elapsed,
        index_elapsed - rows_elapsed,
    )
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
    audit_elapsed = time.perf_counter()
    logging.info(
        "Coherence audit engine produced %s result(s) and %s candidate(s) in %.2fs.",
        len(results),
        len(candidates),
        audit_elapsed - index_elapsed,
    )
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
    anchors_elapsed = time.perf_counter()
    logging.info("Coherence anchor generation produced %s anchor candidate(s) in %.2fs.", len(anchors), anchors_elapsed - audit_elapsed)
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
    save_elapsed = time.perf_counter()
    logging.info("Coherence audit save completed in %.2fs.", save_elapsed - anchors_elapsed)
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
