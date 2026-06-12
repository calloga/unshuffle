from __future__ import annotations

import hashlib
import json
from pathlib import Path

from unshuffle.logic.tagging import POSSIBLE_DUPLICATE_TAG


class AcousticSessionState:
    """Shared cheap state key for acoustic-derived background work."""

    def __init__(self, app=None):
        self.app = app
        self._last_key = ""

    def current_key(self) -> str:
        app = self.app
        model = getattr(app, "model", None)
        engine = getattr(app, "engine", None)
        records = list(getattr(model, "records", []) or [])
        if not records:
            self._last_key = ""
            return ""
        payload = {
            "session_id": str(getattr(engine, "session_id", "") or ""),
            "count": len(records),
            "records": [self._record_token(rec, row) for row, rec in enumerate(records)],
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        self._last_key = hashlib.sha1(raw).hexdigest()
        return self._last_key

    def last_key(self) -> str:
        return self._last_key or self.current_key()

    def cached_tagging_state(self) -> dict | None:
        settings = getattr(self.app, "settings", None)
        prefix = self._tagging_cache_prefix()
        key = self.current_key()
        if not settings or not prefix or not key:
            return None
        if str(settings.value(f"{prefix}/state_key", "") or "") != key:
            return None
        return {"duplicate_count": int(settings.value(f"{prefix}/duplicate_count", 0) or 0)}

    def store_tagging_state(self, duplicate_count: int) -> None:
        settings = getattr(self.app, "settings", None)
        prefix = self._tagging_cache_prefix()
        key = self.last_key()
        if not settings or not prefix or not key:
            return
        settings.setValue(f"{prefix}/state_key", key)
        settings.setValue(f"{prefix}/duplicate_count", duplicate_count)

    def tagged_duplicate_count(self) -> int:
        model = getattr(self.app, "model", None)
        records = list(getattr(model, "records", []) or [])
        count = 0
        for rec in records:
            tags = {str(tag).lower() for tag in (getattr(rec, "tags", []) or [])}
            if POSSIBLE_DUPLICATE_TAG in tags:
                count += 1
        return count

    def staging_record_ids(self) -> set[str]:
        model = getattr(self.app, "model", None)
        records = list(getattr(model, "records", []) or [])
        ids = set()
        for row, rec in enumerate(records):
            value = getattr(rec, "staging_row_id", row)
            ids.add(str(value if value is not None else row))
        return ids

    def _tagging_cache_prefix(self) -> str:
        engine = getattr(self.app, "engine", None)
        session_id = str(getattr(engine, "session_id", "") or "").strip()
        return f"tagging_pass/{session_id}" if session_id else ""

    @staticmethod
    def _record_token(rec, row: int) -> dict:
        source_path = Path(getattr(rec, "source_path", ""))
        vector = getattr(rec, "acoustic_vector", None)
        vector_len = len(vector) if isinstance(vector, (bytes, bytearray, memoryview)) else 0
        return {
            "row": getattr(rec, "staging_row_id", row),
            "path": str(source_path).replace("\\", "/"),
            "hash": str(getattr(rec, "hash", "") or ""),
            "duration": round(float(getattr(rec, "duration", 0.0) or 0.0), 6),
            "type": str(getattr(rec, "audio_type", "") or ""),
            "category": str(getattr(rec, "category", "") or ""),
            "subcategory": str(getattr(rec, "subcategory", "") or ""),
            "vector_len": vector_len,
        }
