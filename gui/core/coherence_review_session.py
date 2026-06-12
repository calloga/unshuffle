from __future__ import annotations

import json


def review_session_settings_key(app) -> str:
    engine = getattr(app, "engine", None)
    session_id = str(getattr(engine, "session_id", "") or "").strip()
    return f"coherence/review_session/{session_id}" if session_id else ""


def review_session_state_key(app) -> str:
    session_state = getattr(app, "acoustic_session_state", None)
    if session_state is not None and hasattr(session_state, "current_key"):
        return str(session_state.current_key() or "")
    return ""


def json_safe_review_row(row: dict) -> dict:
    safe = {}
    for key, value in row.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            safe[key] = value
    return safe


def store_manual_review_session(app, rows: list[dict]) -> None:
    settings = getattr(app, "settings", None)
    key = review_session_settings_key(app)
    if not settings or not key:
        return
    if not rows:
        settings.setValue(key, "")
        return
    payload = {
        "state_key": review_session_state_key(app),
        "rows": [json_safe_review_row(row) for row in rows],
    }
    settings.setValue(key, json.dumps(payload, sort_keys=True, separators=(",", ":")))


def load_manual_review_session(app) -> list[dict]:
    settings = getattr(app, "settings", None)
    key = review_session_settings_key(app)
    if not settings or not key:
        return []
    raw = str(settings.value(key, "") or "")
    if not raw:
        return []
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if not isinstance(payload, dict):
        return []
    state_key = str(payload.get("state_key") or "")
    current_key = review_session_state_key(app)
    if state_key and current_key and state_key != current_key:
        return []
    rows = payload.get("rows")
    if not isinstance(rows, list):
        return []
    restored = []
    for row in rows:
        if isinstance(row, dict) and str(row.get("candidate_id") or ""):
            restored.append(dict(row))
    return restored
