from __future__ import annotations

import json


def initial_refinement_action(row: dict) -> str:
    try:
        score = float(row.get("confidence_score") or 0.0)
    except (TypeError, ValueError):
        score = 0.0
    return "accept" if score >= 0.25 else "reject"


def neighbor_summary(result: dict) -> dict:
    summary = result.get("nearest_neighbor_summary")
    if isinstance(summary, dict):
        return summary
    raw = result.get("nearest_neighbor_summary_json")
    if isinstance(raw, str) and raw:
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                return data
        except (TypeError, json.JSONDecodeError):
            pass
    return {}


def nearest_adjacency_from_summary(summary: dict) -> str:
    adjacency = summary.get("nearest_adjacent_cluster")
    if not isinstance(adjacency, dict):
        return ""
    parts = [
        str(adjacency.get("audio_type") or "").strip(),
        str(adjacency.get("category") or "").strip(),
        str(adjacency.get("subcategory") or "").strip(),
    ]
    label = " / ".join(part for part in parts if part)
    if not label:
        return ""
    ratio = adjacency.get("adjacency_ratio")
    try:
        if ratio is None:
            raise TypeError
        ratio_text = f", {float(ratio):.2f}x separation"
    except (TypeError, ValueError):
        ratio_text = ""
    prefix = "close " if bool(adjacency.get("is_close")) else ""
    return f"{prefix}{label}{ratio_text}"


def summary_float(summary: dict, key: str) -> float:
    try:
        return float(summary.get(key) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def outlier_candidate_id(record_id: str, audio_type: str, category: str, subcategory: str) -> str:
    return f"outlier:{record_id}:{audio_type}:{category}:{subcategory}"


def coherence_complete_text(auto_count: int, anchor_count: int) -> str:
    text = "Coherence audit complete"
    if auto_count:
        text += f"; auto-staged {auto_count} refinement{'s' if auto_count != 1 else ''}"
    if anchor_count:
        text += f"; {anchor_count} anchor candidate{'s' if anchor_count != 1 else ''}."
    return text + "."


def ignored_strong_outlier_ids(rows: list[dict], ignored_ids: set[str], promoted_record_ids: set[str]) -> list[str]:
    return [
        str(row.get("candidate_id"))
        for row in rows
        if row.get("kind") == "strong_outlier"
        and row.get("candidate_id")
        and str(row.get("candidate_id")) in ignored_ids
        and str(row.get("record_id") or "") not in promoted_record_ids
    ]


def real_refinement_candidate_ids(candidate_ids: list[str]) -> list[str]:
    return [
        candidate_id
        for candidate_id in candidate_ids
        if not candidate_id.startswith("outlier:")
    ]


def remaining_review_message(remaining_count: int, promoted_anchor_count: int) -> str:
    message = f"{remaining_count} acoustic outlier{'s' if remaining_count != 1 else ''} to review."
    if promoted_anchor_count:
        message += " New anchor saved; it will be used after this review queue is clear."
    return message


def cached_review_state_message(review_count: int) -> str:
    return f"{review_count} library suggestion{'s' if review_count != 1 else ''} to review."
