from __future__ import annotations

from collections import defaultdict
import json
from pathlib import Path
from typing import Any


REVIEW_DECISION_TARGET = "target"
REVIEW_DECISION_ACCEPTED_CURRENT = "accepted_current"


def normalized_source_path(value: Any) -> str:
    return Path(str(value or "")).as_posix()


def review_decisions_for_records(db, records: list) -> dict[tuple[str, str], dict]:
    if db is None or not hasattr(db, "list_coherence_review_decisions"):
        return {}
    source_paths = [normalized_source_path(getattr(record, "source_path", "")) for record in records]
    file_hashes = [str(getattr(record, "hash", "") or "") for record in records]
    rows = db.list_coherence_review_decisions(source_paths=source_paths, file_hashes=file_hashes)
    if not rows:
        return {}

    by_path = {normalized_source_path(row.get("source_path")): row for row in rows if row.get("source_path")}
    rows_by_hash: dict[str, list[dict]] = defaultdict(list)
    records_by_hash: dict[str, list] = defaultdict(list)
    for row in rows:
        file_hash = str(row.get("file_hash") or "")
        if file_hash:
            rows_by_hash[file_hash].append(row)
    for record in records:
        file_hash = str(getattr(record, "hash", "") or "")
        if file_hash:
            records_by_hash[file_hash].append(record)

    result: dict[tuple[str, str], dict] = {}
    for record in records:
        path = normalized_source_path(getattr(record, "source_path", ""))
        file_hash = str(getattr(record, "hash", "") or "")
        decision = by_path.get(path)
        if decision is None and file_hash and len(records_by_hash[file_hash]) == 1 and len(rows_by_hash[file_hash]) == 1:
            decision = rows_by_hash[file_hash][0]
        if decision is not None:
            result[(path, file_hash)] = decision
    return result


def apply_target_review_decisions(db, records: list) -> int:
    decisions = review_decisions_for_records(db, records)
    changed = 0
    for record in records:
        path = normalized_source_path(getattr(record, "source_path", ""))
        file_hash = str(getattr(record, "hash", "") or "")
        decision = decisions.get((path, file_hash))
        if not decision or str(decision.get("decision_type") or "") != REVIEW_DECISION_TARGET:
            continue
        target_audio_type = str(decision.get("target_audio_type") or "")
        target_category = str(decision.get("target_category") or "")
        target_subcategory = str(decision.get("target_subcategory") or "")
        if target_audio_type and str(getattr(record, "audio_type", "") or "") != target_audio_type:
            record.audio_type = target_audio_type
            changed += 1
        if target_category and str(getattr(record, "category", "") or "") != target_category:
            record.category = target_category
            changed += 1
        if str(getattr(record, "subcategory", "") or "") != target_subcategory:
            record.subcategory = target_subcategory
            changed += 1
    return changed


def decision_matches_current_bucket(decision: dict, record) -> bool:
    return (
        str(decision.get("target_audio_type") or "") == str(getattr(record, "audio_type", "") or "")
        and str(decision.get("target_category") or "") == str(getattr(record, "category", "") or "")
        and str(decision.get("target_subcategory") or "") == str(getattr(record, "subcategory", "") or "")
    )


def ignored_outlier_ids(app) -> set[str]:
    settings = getattr(app, "settings", None)
    engine = getattr(app, "engine", None)
    session_id = str(getattr(engine, "session_id", "") or "")
    if settings is None or not session_id:
        return set()
    raw = settings.value(f"coherence/ignored_outliers/{session_id}", "")
    if not raw:
        return set()
    try:
        data = json.loads(str(raw))
        return {str(item) for item in data if str(item)}
    except (TypeError, json.JSONDecodeError):
        return set()


def remember_ignored_outliers(app, outlier_ids: list[str]) -> None:
    settings = getattr(app, "settings", None)
    engine = getattr(app, "engine", None)
    session_id = str(getattr(engine, "session_id", "") or "")
    if settings is None or not session_id:
        return
    ignored = ignored_outlier_ids(app)
    ignored.update(item for item in outlier_ids if item)
    settings.setValue(f"coherence/ignored_outliers/{session_id}", json.dumps(sorted(ignored)))


def persisted_review_decisions(
    rows: list[dict],
    accepted_refinement_rows: list[dict],
    ignored_outlier_ids: list[str],
) -> list[dict]:
    row_by_candidate = {str(row.get("candidate_id") or ""): row for row in rows}
    decisions = []
    for row in accepted_refinement_rows:
        source_path = str(row.get("source_path") or "").strip()
        if not source_path:
            continue
        decisions.append(
            {
                "source_path": source_path,
                "file_hash": str(row.get("file_hash") or ""),
                "decision_type": REVIEW_DECISION_TARGET,
                "current_audio_type": str(row.get("current_audio_type") or ""),
                "current_category": str(row.get("current_category") or ""),
                "current_subcategory": str(row.get("current_subcategory") or ""),
                "target_audio_type": str(row.get("suggested_audio_type") or ""),
                "target_category": str(row.get("suggested_category") or ""),
                "target_subcategory": str(row.get("suggested_subcategory") or ""),
            }
        )
    for candidate_id in ignored_outlier_ids:
        row = row_by_candidate.get(candidate_id)
        if not row:
            continue
        source_path = str(row.get("source_path") or "").strip()
        if not source_path:
            continue
        current_audio_type = str(row.get("current_audio_type") or "")
        current_category = str(row.get("current_category") or "")
        current_subcategory = str(row.get("current_subcategory") or "")
        decisions.append(
            {
                "source_path": source_path,
                "file_hash": str(row.get("file_hash") or ""),
                "decision_type": REVIEW_DECISION_ACCEPTED_CURRENT,
                "current_audio_type": current_audio_type,
                "current_category": current_category,
                "current_subcategory": current_subcategory,
                "target_audio_type": current_audio_type,
                "target_category": current_category,
                "target_subcategory": current_subcategory,
            }
        )
    return decisions
