from __future__ import annotations

import json


def updated_recent_scan_sources(raw: object, path: str, *, limit: int) -> list[str]:
    path = (path or "").strip()
    if not path:
        return []
    recents: list[str] = []
    if raw:
        try:
            data = json.loads(str(raw))
            if isinstance(data, list):
                recents = [str(item).strip() for item in data if str(item).strip()]
        except (TypeError, json.JSONDecodeError):
            recents = []
    recents = [item for item in recents if item != path]
    recents.insert(0, path)
    return recents[:limit]
