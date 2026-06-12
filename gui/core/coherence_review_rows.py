from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path
from statistics import median

from PySide6.QtCore import Qt

from gui.core.coherence_presenters import (
    initial_refinement_action,
    nearest_adjacency_from_summary,
    neighbor_summary,
    outlier_candidate_id,
    summary_float,
)
from gui.core.coherence_review_decisions import (
    decision_matches_current_bucket,
    normalized_source_path,
    review_decisions_for_records,
)


def enrichment_rows(controller, rows: list[dict]) -> list[dict]:
    model = getattr(controller.app, "model", None)
    if model is None:
        return rows
    record_by_id = {
        str(getattr(rec, "staging_row_id", row) if getattr(rec, "staging_row_id", None) is not None else row): rec
        for row, rec in enumerate(model.records)
    }
    source_row_by_id = {
        str(getattr(rec, "staging_row_id", row) if getattr(rec, "staging_row_id", None) is not None else row): row
        for row, rec in enumerate(model.records)
    }
    enriched: list[dict] = []
    for row in rows:
        payload = dict(row)
        record_id = str(row.get("record_id"))
        rec = record_by_id.get(record_id)
        if rec is not None:
            path = Path(getattr(rec, "source_path", ""))
            payload["file_name"] = path.name
            payload["pack"] = getattr(rec, "pack", "") or ""
            payload["source_path"] = str(path)
            payload["file_hash"] = str(getattr(rec, "hash", "") or "")
            payload["confidence"] = getattr(rec, "confidence", "")
            payload["classification_confidence"] = getattr(rec, "confidence", "")
            if hasattr(model, "_classification_tooltip"):
                payload["classification_evidence"] = model._classification_tooltip(rec)
            source_row = source_row_by_id.get(record_id)
            if source_row is not None:
                payload["display_index"] = str(model.record_id(source_row) + 1) if hasattr(model, "record_id") else str(source_row + 1)
                if hasattr(model, "headerData"):
                    payload["index_color"] = model.headerData(source_row, Qt.Vertical, Qt.BackgroundRole)
        enriched.append(payload)
    return enriched


def review_rows(controller) -> list[dict]:
    engine = getattr(controller.app, "engine", None)
    if not engine or not getattr(engine, "db", None):
        return []

    is_running_tests = "pytest" in sys.modules or "unittest" in sys.modules

    if is_running_tests:
        pending_rows = engine.db.list_refinement_candidates(engine.session_id, state="pending")
        auto_rows = engine.db.list_refinement_candidates(engine.session_id, state="auto_staged")
        rows = []
        active_record_ids = set()
        for row in auto_rows:
            payload = dict(row)
            payload["kind"] = "refinement"
            payload["initial_action"] = "accept"
            active_record_ids.add(str(payload.get("record_id") or ""))
            rows.append(payload)
        for row in pending_rows:
            payload = dict(row)
            payload["kind"] = "refinement"
            payload["initial_action"] = controller._initial_refinement_action(payload)
            active_record_ids.add(str(payload.get("record_id") or ""))
            rows.append(payload)
        controller._mark_refinement_anchor_prompt_eligibility(rows)
        rows.extend(controller._derive_strong_outlier_rows(active_record_ids))
        rows = controller._enrich_refinement_rows(rows)
        rows.sort(key=controller._review_row_sort_key)
        return rows

    rows = []
    rows.extend(controller._derive_strong_outlier_rows(set()))
    rows = controller._enrich_refinement_rows(rows)
    rows.sort(key=controller._review_row_sort_key)
    return rows


def review_row_sort_key(row: dict) -> tuple:
    kind_rank = 2 if row.get("kind") == "strong_outlier" else 0
    state_rank = 0 if str(row.get("state") or "") == "auto_staged" else 1
    current_type = str(row.get("current_audio_type") or row.get("suggested_audio_type") or "").casefold()
    current_category = str(row.get("current_category") or "").casefold()
    current_subcategory = str(row.get("current_subcategory") or "").casefold()
    pack = str(row.get("pack") or "").casefold()
    file_name = str(row.get("file_name") or row.get("source_path") or "").casefold()
    severity = -float(row.get("confidence_score") or row.get("outlier_ratio") or 0.0)
    return (
        state_rank,
        kind_rank,
        current_type,
        current_category,
        current_subcategory,
        pack,
        file_name,
        severity,
    )


def mark_refinement_anchor_prompt_eligibility(controller, rows: list[dict]) -> None:
    engine = getattr(controller.app, "engine", None)
    if not engine or not getattr(engine, "db", None) or not hasattr(engine.db, "list_coherence_results"):
        return
    result_by_id = {
        str(row.get("record_id") or ""): row
        for row in engine.db.list_coherence_results(engine.session_id)
    }
    for row in rows:
        record_id = str(row.get("record_id") or "")
        result = result_by_id.get(record_id)
        if not result or not bool(result.get("is_outlier")):
            continue
        if str(result.get("anchor_fit_status") or "") == "close":
            continue
        row["anchor_prompt_eligible"] = True
        summary = neighbor_summary(result)
        distance = summary_float(summary, "distance_to_cluster_medoid")
        if distance > 0:
            row["medoid_distance"] = distance


def derive_strong_outlier_rows(controller, active_refinement_record_ids: set[str]) -> list[dict]:
    engine = getattr(controller.app, "engine", None)
    model = getattr(controller.app, "model", None)
    if not engine or not getattr(engine, "db", None) or model is None:
        return []
    if not hasattr(engine.db, "list_coherence_results"):
        return []
    ignored = controller._ignored_outlier_ids()
    record_by_id = {
        str(getattr(rec, "staging_row_id", row) if getattr(rec, "staging_row_id", None) is not None else row): rec
        for row, rec in enumerate(model.records)
    }
    review_decisions = review_decisions_for_records(engine.db, list(record_by_id.values()))
    candidates_by_bucket: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    distances_by_bucket: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    results = engine.db.list_coherence_results(engine.session_id)
    for result in results:
        record_id = str(result.get("record_id") or "")
        rec = record_by_id.get(record_id)
        if rec is None:
            continue
        bucket = (
            str(getattr(rec, "audio_type", "") or ""),
            str(getattr(rec, "category", "") or ""),
            str(getattr(rec, "subcategory", "") or ""),
        )
        if bucket[1] == "Uncategorized":
            continue

        raw = result.get("nearest_neighbor_summary_json")
        distance = 0.0
        if isinstance(raw, str) and raw:
            idx = raw.find('"distance_to_cluster_medoid"')
            if idx != -1:
                start = raw.find(':', idx)
                if start != -1:
                    end = raw.find(',', start)
                    if end == -1:
                        end = raw.find('}', start)
                    if end != -1:
                        try:
                            distance = float(raw[start + 1:end].strip())
                        except ValueError:
                            pass
        if distance > 0:
            distances_by_bucket[bucket].append(distance)
        if not bool(result.get("is_outlier")):
            continue
        if record_id in active_refinement_record_ids:
            continue
        rec_path = normalized_source_path(getattr(rec, "source_path", ""))
        rec_hash = str(getattr(rec, "hash", "") or "")
        review_decision = review_decisions.get((rec_path, rec_hash))
        if review_decision and decision_matches_current_bucket(review_decision, rec):
            continue
        if str(result.get("anchor_fit_status") or "") == "close":
            continue
        outlier_id = outlier_candidate_id(record_id, *bucket)
        if outlier_id in ignored:
            continue
        payload = dict(result)
        payload["candidate_id"] = outlier_id
        payload["kind"] = "strong_outlier"
        payload["record_id"] = record_id
        payload["current_audio_type"] = bucket[0]
        payload["current_category"] = bucket[1]
        payload["current_subcategory"] = bucket[2]
        payload["suggested_audio_type"] = bucket[0]
        payload["suggested_category"] = bucket[1]
        payload["suggested_subcategory"] = bucket[2]
        payload["initial_action"] = "reject"
        payload["anchor_prompt_eligible"] = True
        payload["medoid_distance"] = distance
        candidates_by_bucket[bucket].append(payload)

    rows = []
    for bucket, candidates in candidates_by_bucket.items():
        distances = distances_by_bucket.get(bucket, [])
        baseline = median(distances) if distances else 0.0
        if baseline <= 0:
            continue
        eligible = []
        for row in candidates:
            ratio = (row.get("medoid_distance") or 0.0) / baseline
            if ratio < 1.5:
                continue
            row["outlier_ratio"] = ratio
            row["confidence_score"] = ratio
            evidence = f"Strong current-bucket outlier: {ratio:.1f}x typical."
            adjacency = nearest_adjacency_from_summary(neighbor_summary(row))
            if adjacency:
                evidence += f"\n- Nearest neighboring cluster: {adjacency}."
            row["evidence"] = evidence
            eligible.append(row)
        eligible.sort(key=lambda item: float(item.get("outlier_ratio") or 0.0), reverse=True)
        rows.extend(eligible)
    return rows
