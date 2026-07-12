from __future__ import annotations

import logging
import json
import math
import time
from typing import Callable

import numpy as np

from ...core.features import normalize_distance_vector
from .anchor_profiles import generate_anchor_candidates
from .coherence_engine import CoherenceEngine
from .models import (
    COHERENCE_STATUS_LOW,
    COHERENCE_STATUS_MISCATEGORIZATION,
    CoherenceResult,
    CoherenceRunSummary,
    REFINEMENT_AUTO_STAGED,
)
from .vector_index import records_from_staging_rows, stats_from_staging_rows, valid_coherence_vector


GROUPED_COHERENCE_MIN_RECORDS = 25_000


def run_coherence_audit(
    db,
    session_id: str,
    *,
    force: bool = False,
    progress_callback: Callable[[dict], None] | None = None,
) -> CoherenceRunSummary:
    grouped_supported = all(
        hasattr(db, name)
        for name in (
            "iter_coherence_group_keys",
            "coherence_group_records",
            "clear_generated_coherence_audit",
            "append_coherence_group",
        )
    )
    if grouped_supported and _use_grouped_coherence(db, session_id):
        return _run_grouped_coherence_audit(
            db,
            session_id,
            force=force,
            progress_callback=progress_callback,
        )
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


def _use_grouped_coherence(db, session_id: str) -> bool:
    conn = getattr(db, "conn", None)
    if conn is None:
        return True
    try:
        row = conn.execute(
            """
            SELECT COUNT(*)
            FROM staging_records
            WHERE session_id = ?
              AND COALESCE(is_preserved, 0) = 0
              AND COALESCE(category, '') NOT IN ('Non-Audio Assets', 'Metadata')
              AND feature_vector IS NOT NULL
              AND COALESCE(CASE WHEN json_valid(COALESCE(evidence_json, ''))
                  THEN json_extract(evidence_json, '$.duplicate_shadow.is_shadow') ELSE 0 END, 0) != 1
            """,
            (session_id,),
        ).fetchone()
        return int(row[0] if row is not None else 0) >= GROUPED_COHERENCE_MIN_RECORDS
    except Exception:
        logging.debug("Could not size coherence input; using bounded grouped audit.", exc_info=True)
        return True


def _run_grouped_coherence_audit(
    db,
    session_id: str,
    *,
    force: bool,
    progress_callback: Callable[[dict], None] | None,
) -> CoherenceRunSummary:
    started_at = time.perf_counter()
    if hasattr(db, "ensure_verified_anchors_for_session"):
        db.ensure_verified_anchors_for_session(session_id)
    stats_rows = (
        row
        for batch in db.iter_coherence_staging_records(session_id, batch_size=1000)
        for row in batch
    )
    stats = stats_from_staging_rows(stats_rows)
    if not force and not stats.can_run:
        return CoherenceRunSummary(
            total_records=stats.total_records,
            eligible_records=stats.eligible_records,
            valid_vector_records=stats.valid_vector_records,
            coverage=stats.coverage,
            ran=False,
            reason="Coherence needs more indexed audio",
        )

    group_keys = list(db.iter_coherence_group_keys(session_id))
    engine = CoherenceEngine(verified_anchors=_verified_anchors(db, session_id))
    db.clear_generated_coherence_audit(session_id)
    conn = db.conn
    conn.execute("DROP TABLE IF EXISTS temp.coherence_refinement_work")
    conn.execute(
        """
        CREATE TEMP TABLE coherence_refinement_work (
            record_id TEXT PRIMARY KEY,
            context_json TEXT NOT NULL
        )
        """
    )
    cluster_profiles: list[dict] = []
    result_count = 0
    anchor_count = 0
    total_groups = max(1, len(group_keys))
    for index, group_key in enumerate(group_keys, 1):
        rows = db.coherence_group_records(session_id, group_key)
        records, _group_stats = records_from_staging_rows(rows)
        if not records:
            continue
        results, context, profiles = engine.audit_group(group_key, records)
        anchors = generate_anchor_candidates(records, results, engine.similarity_engine)
        db.append_coherence_group(session_id, results, anchors=anchors)
        result_count += len(results)
        anchor_count += len(anchors)
        cluster_profiles.extend(profiles)
        low_ids = {
            result.record_id
            for result in results
            if result.coherence_status == COHERENCE_STATUS_LOW
        }
        low_ids.update(record.record_id for record in records if record.category == "Uncategorized")
        conn.executemany(
            "INSERT OR REPLACE INTO coherence_refinement_work(record_id, context_json) VALUES (?, ?)",
            [
                (record_id, json.dumps(context.get(record_id, {}), separators=(",", ":")))
                for record_id in sorted(low_ids)
            ],
        )
        if progress_callback is not None:
            progress_callback(
                {
                    "phase": "Checking Library Coherence",
                    "message": f"Checking coherence group {index} of {len(group_keys)}...",
                    "current": index,
                    "total": total_groups,
                }
            )
        del rows, records, results, context, profiles, anchors

    if progress_callback is not None:
        progress_callback(
            {
                "phase": "Checking Library Coherence",
                "message": "Comparing coherence clusters...",
                "current": 0,
                "total": max(1, len(cluster_profiles)),
            }
        )
    _apply_group_adjacency(db, session_id, engine, cluster_profiles)
    candidate_count = _apply_streamed_refinements(db, session_id, engine, cluster_profiles, progress_callback)
    pending_count = db.count_refinement_candidates(session_id, state="pending")
    auto_staged_count = db.count_refinement_candidates(session_id, state=REFINEMENT_AUTO_STAGED)
    conn.execute("DROP TABLE IF EXISTS temp.coherence_refinement_work")
    logging.info(
        "Grouped coherence audit produced %s result(s), %s candidate(s), and %s anchor(s) in %.2fs.",
        result_count,
        candidate_count,
        anchor_count,
        time.perf_counter() - started_at,
    )
    return CoherenceRunSummary(
        total_records=stats.total_records,
        eligible_records=stats.eligible_records,
        valid_vector_records=stats.valid_vector_records,
        coverage=stats.coverage,
        ran=True,
        result_count=result_count,
        pending_candidate_count=pending_count,
        auto_staged_candidate_count=auto_staged_count,
        anchor_candidate_count=anchor_count,
    )


def _result_from_row(row) -> CoherenceResult:
    summary = row["nearest_neighbor_summary_json"]
    try:
        summary = json.loads(summary) if summary else {}
    except (TypeError, json.JSONDecodeError):
        summary = {}
    return CoherenceResult(
        record_id=str(row["record_id"]),
        category=str(row["category"] or ""),
        subcategory=str(row["subcategory"] or ""),
        coherence_status=str(row["coherence_status"] or ""),
        coherence_score=float(row["coherence_score"] or 0.0),
        cluster_id=row["cluster_id"],
        is_outlier=bool(row["is_outlier"]),
        review_reason=row["review_reason"],
        suggested_alternate_category=row["suggested_alternate_category"],
        suggested_alternate_subcategory=row["suggested_alternate_subcategory"],
        nearest_neighbor_summary=summary,
        anchor_fit_status=row["anchor_fit_status"],
    )


def _apply_group_adjacency(db, session_id: str, engine: CoherenceEngine, profiles: list[dict]) -> None:
    if len(profiles) < 2:
        return
    adjacency_by_cluster = engine.cluster_adjacency_index(profiles)
    for group_key in db.iter_coherence_group_keys(session_id):
        audio_type, category, subcategory = group_key
        cursor = db.conn.execute(
            """
            SELECT result.* FROM coherence_results AS result
            JOIN staging_records AS staging
              ON staging.session_id = result.session_id AND CAST(staging.row_id AS TEXT) = result.record_id
            WHERE result.session_id = ?
              AND COALESCE(staging.audio_type, '') = ?
              AND COALESCE(staging.category, '') = ?
              AND COALESCE(staging.subcategory, '') = ?
            ORDER BY staging.row_id
            """,
            (session_id, audio_type, category, subcategory),
        )
        while True:
            rows = cursor.fetchmany(1000)
            if not rows:
                break
            updated = engine.with_cluster_adjacency_summaries(
                [_result_from_row(row) for row in rows],
                profiles,
                adjacency_by_cluster=adjacency_by_cluster,
            )
            db.append_coherence_group(session_id, updated)


def _apply_streamed_refinements(
    db,
    session_id: str,
    engine: CoherenceEngine,
    profiles: list[dict],
    progress_callback: Callable[[dict], None] | None,
) -> int:
    total_rows = int(db.conn.execute("SELECT COUNT(*) FROM coherence_refinement_work").fetchone()[0])
    work_rows = db.conn.execute(
        """
        SELECT work.context_json,
               staging.row_id, staging.id, staging.source_path, staging.pack,
               staging.category, staging.subcategory, staging.audio_type,
               staging.confidence, staging.feature_vector, staging.evidence_json,
               staging.is_preserved,
               result.record_id, result.coherence_status, result.coherence_score,
               result.cluster_id, result.is_outlier, result.review_reason,
               result.suggested_alternate_category, result.suggested_alternate_subcategory,
               result.nearest_neighbor_summary_json, result.anchor_fit_status
        FROM coherence_refinement_work AS work
        JOIN staging_records AS staging
          ON staging.session_id = ? AND CAST(staging.row_id AS TEXT) = work.record_id
        JOIN coherence_results AS result
          ON result.session_id = staging.session_id AND result.record_id = work.record_id
        ORDER BY staging.row_id
        """,
        (session_id,),
    )
    global_index = None
    if total_rows and hasattr(db, "coherence_records_by_row_ids"):
        if progress_callback is not None:
            progress_callback(
                {
                    "phase": "Checking Library Coherence",
                    "message": "Indexing refinement candidates...",
                    "current": 0,
                    "total": max(1, total_rows),
                }
            )
        global_index = _build_global_refinement_index(db, session_id)
    candidate_count = 0
    total = max(1, total_rows)
    processed = 0
    while True:
        rows = work_rows.fetchmany(32)
        if not rows:
            break
        work: list[tuple[object, CoherenceResult, dict]] = []
        for row in rows:
            records, _stats = records_from_staging_rows([dict(row)])
            if not records:
                continue
            try:
                context = json.loads(row["context_json"] or "{}")
            except (TypeError, json.JSONDecodeError):
                context = {}
            work.append((records[0], _result_from_row(row), context))
        neighbors_by_id = _stream_exact_neighbors_for_records(
            db,
            session_id,
            engine,
            [record for record, _result, _context in work],
            limit=10,
            global_index=global_index,
        )
        for record, result, context in work:
            neighbors = neighbors_by_id.get(record.record_id, [])
            candidates = engine.refinement_candidates(
                [record, *neighbors],
                [result],
                {record.record_id: context},
                profiles,
            )
            if candidates:
                candidate = candidates[0]
                updated = CoherenceResult(
                    record_id=result.record_id,
                    category=result.category,
                    subcategory=result.subcategory,
                    coherence_status=COHERENCE_STATUS_MISCATEGORIZATION,
                    coherence_score=result.coherence_score,
                    cluster_id=result.cluster_id,
                    is_outlier=True,
                    review_reason=candidate.evidence,
                    suggested_alternate_category=candidate.suggested_category,
                    suggested_alternate_subcategory=candidate.suggested_subcategory,
                    nearest_neighbor_summary=result.nearest_neighbor_summary,
                    anchor_fit_status=result.anchor_fit_status,
                )
                db.append_coherence_group(session_id, [updated], candidates=candidates)
                candidate_count += 1
            processed += 1
            if progress_callback is not None and (processed == total_rows or processed % 25 == 0):
                progress_callback(
                    {
                        "phase": "Checking Library Coherence",
                        "message": f"Checking refinement candidate {processed} of {total_rows}...",
                        "current": processed,
                        "total": total,
                    }
                )
    return candidate_count


def _stream_exact_neighbors(db, session_id: str, engine: CoherenceEngine, record, *, limit: int):
    return _stream_exact_neighbors_for_records(
        db, session_id, engine, [record], limit=limit
    ).get(record.record_id, [])


def _stream_exact_neighbors_for_records(
    db,
    session_id: str,
    engine: CoherenceEngine,
    records: list,
    *,
    limit: int,
    global_index=None,
) -> dict[str, list]:
    states = {
        record.record_id: {"record": record, "nearest": [], "order": 0}
        for record in records
    }
    if not states:
        return {}
    if global_index is not None:
        return _indexed_neighbors_for_records(
            db,
            session_id,
            engine,
            records,
            global_index,
            limit=limit,
        )
    for batch in db.iter_coherence_staging_records(session_id, batch_size=1000):
        candidates, _stats = records_from_staging_rows(batch)
        if not candidates:
            continue
        state_values = list(states.values())
        distance_matrix = engine._distance_matrix_from_vectorized(
            [state["record"] for state in state_values],
            candidates,
        )
        for state_index, state in enumerate(state_values):
            record = state["record"]
            if distance_matrix is None:
                distances = engine._distances_from_vectorized(record.vector, candidates)
                if distances is None:
                    distances = [
                        engine.similarity_engine.calculate_distance(
                            record.vector, candidate.vector
                        )
                        for candidate in candidates
                    ]
            else:
                distances = distance_matrix[state_index]
            nearest = state["nearest"]
            order = int(state["order"])
            for candidate, distance in zip(candidates, distances):
                if candidate.record_id == record.record_id:
                    continue
                order += 1
                value = float(distance)
                if math.isfinite(value):
                    nearest.append((value, order, candidate))
            nearest.sort(key=lambda item: (item[0], item[1]))
            del nearest[limit:]
            state["order"] = order
    return {
        record_id: [candidate for _distance, _order, candidate in state["nearest"]]
        for record_id, state in states.items()
    }


def _build_global_refinement_index(db, session_id: str):
    vector_chunks: list[np.ndarray] = []
    row_id_chunks: list[np.ndarray] = []
    for batch in db.iter_coherence_staging_records(session_id, batch_size=2000):
        records, _stats = records_from_staging_rows(batch)
        if not records:
            continue
        try:
            vector_chunks.append(
                np.asarray(
                    [normalize_distance_vector(record.vector) for record in records],
                    dtype=np.float32,
                )
            )
            row_id_chunks.append(
                np.asarray([int(record.record_id) for record in records], dtype=np.int64)
            )
        except (TypeError, ValueError):
            return None
    if not vector_chunks:
        return None
    vectors = np.concatenate(vector_chunks, axis=0)
    row_ids = np.concatenate(row_id_chunks, axis=0)
    if len(row_ids) < 3000:
        return None
    try:
        from .spatial_index import SpatialIndex

        return SpatialIndex(vectors), row_ids
    except ModuleNotFoundError:
        return None


def _indexed_neighbors_for_records(
    db,
    session_id: str,
    engine: CoherenceEngine,
    records: list,
    global_index,
    *,
    limit: int,
) -> dict[str, list]:
    spatial_index, row_ids = global_index
    query_vectors = np.asarray(
        [normalize_distance_vector(record.vector) for record in records],
        dtype=np.float32,
    )
    candidate_count = min(max(100, limit * 2), len(row_ids))
    labels = np.asarray(
        [
            spatial_index.query(query_vector, k=candidate_count)[0][0]
            for query_vector in query_vectors
        ],
        dtype=np.int64,
    )
    wanted_ids = sorted(
        {
            int(row_ids[int(label)])
            for row_labels in labels
            for label in row_labels
            if int(label) >= 0
        }
    )
    candidate_rows = db.coherence_records_by_row_ids(session_id, wanted_ids)
    candidates, _stats = records_from_staging_rows(candidate_rows)
    candidate_by_id = {int(candidate.record_id): candidate for candidate in candidates}
    result: dict[str, list] = {}
    for record, row_labels in zip(records, labels):
        ordered_candidates = [
            candidate_by_id.get(int(row_ids[int(label)]))
            for label in row_labels
            if int(label) >= 0
        ]
        ordered_candidates = [
            candidate
            for candidate in ordered_candidates
            if candidate is not None and candidate.record_id != record.record_id
        ]
        exact_distances = engine._distances_from_vectorized(
            record.vector,
            ordered_candidates,
        )
        if exact_distances is None:
            exact_distances = [
                engine.similarity_engine.calculate_distance(
                    record.vector, candidate.vector
                )
                for candidate in ordered_candidates
            ]
        paired = [
            (candidate, float(distance))
            for candidate, distance in zip(ordered_candidates, exact_distances)
            if math.isfinite(float(distance))
        ]
        paired.sort(key=lambda item: item[1])
        result[record.record_id] = [candidate for candidate, _distance in paired[:limit]]
    return result


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
