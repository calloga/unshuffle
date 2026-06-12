"""Learning adjustment helpers for runtime execution."""

from typing import Any

from ..core.constants import TOKEN_ADJUSTMENT_STEP
from ..logic.classification import weighted_adjustment_tokens


def top_decision_token(record: Any, category: str) -> str | None:
    evidence = getattr(record, "evidence", None) or {}
    trace = evidence.get("trace") or {}
    components = trace.get("components") or {}
    candidates: list[tuple[float, str]] = []
    for component in components.values():
        try:
            component_weight = float(component.get("weight", 1.0) or 1.0)
        except (TypeError, ValueError, AttributeError):
            component_weight = 1.0
        for entry in component.get("token_trace") or []:
            if entry.get("status") != "matched":
                continue
            token = str(entry.get("token", "") or "").strip().lower()
            if not token:
                continue
            for match in entry.get("matches") or []:
                if str(match.get("category") or "") != category:
                    continue
                try:
                    contribution = float(match.get("contribution", match.get("weight", 0.0)) or 0.0)
                except (TypeError, ValueError):
                    contribution = 0.0
                score = contribution * component_weight
                if score > 0:
                    candidates.append((score, token))
    if not candidates:
        return None
    best_score = max(score for score, _token in candidates)
    best_tokens = {token for score, token in candidates if score == best_score}
    weighted = weighted_adjustment_tokens(best_tokens)
    if not weighted:
        return None
    return sorted(weighted)[0]


def user_category_adjustments_for(record: Any, old_category: str, new_category: str) -> set[tuple[str, str, float]]:
    old_category = str(old_category or "").strip()
    new_category = str(new_category or "").strip()
    if not old_category or not new_category or old_category == new_category:
        return set()
    token = top_decision_token(record, old_category)
    if not token:
        return set()
    return {
        (token, old_category, -TOKEN_ADJUSTMENT_STEP),
        (token, new_category, TOKEN_ADJUSTMENT_STEP),
    }


def learning_source_key(record: Any) -> str:
    file_hash = str(getattr(record, "hash", "") or "").strip()
    if file_hash:
        return f"hash:{file_hash.lower()}"
    source_path = str(getattr(record, "source_path", "") or "").strip().replace("\\", "/")
    while "//" in source_path:
        source_path = source_path.replace("//", "/")
    return f"path:{source_path.lower()}" if source_path else ""


def user_category_learning_events_for(record: Any, old_category: str, new_category: str) -> set[tuple[str, str, str, str, float, float]]:
    source_key = learning_source_key(record)
    if not source_key:
        return set()
    adjustments = user_category_adjustments_for(record, old_category, new_category)
    if len(adjustments) != 2:
        return set()
    by_delta = {delta: (token, category) for token, category, delta in adjustments}
    old_item = by_delta.get(-TOKEN_ADJUSTMENT_STEP)
    new_item = by_delta.get(TOKEN_ADJUSTMENT_STEP)
    if old_item is None or new_item is None or old_item[0] != new_item[0]:
        return set()
    token = old_item[0]
    return {(source_key, token, old_item[1], new_item[1], -TOKEN_ADJUSTMENT_STEP, TOKEN_ADJUSTMENT_STEP)}


def classification_adjustments_for(record: Any) -> set[tuple[str, str, float]]:
    return set()
