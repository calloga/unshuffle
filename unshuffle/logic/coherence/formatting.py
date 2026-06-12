from __future__ import annotations

import hashlib
from typing import Any

from ...core.constants import get_runtime_config_snapshot


def _slug(value: str) -> str:
    return "".join(ch.lower() if ch.isalnum() else "_" for ch in (value or "")).strip("_") or "none"


def _bucket_label(audio_type: str, category: str, subcategory: str) -> str:
    parts = [_bucket_label_part(part) for part in (audio_type, category, subcategory)]
    parts = [part for part in parts if part]
    return " / ".join(parts) if parts else "No bucket"


def _bucket_label_part(value: str) -> str:
    text = (value or "").strip()
    if text == "no-sub":
        return ""
    return text


def _profile_vector(value: object) -> list[float]:
    if not isinstance(value, list):
        return []
    try:
        return [float(item) for item in value]
    except (TypeError, ValueError):
        return []


def _profile_float(value: Any, *, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _refinement_subcategory_normalizer():
    runtime = get_runtime_config_snapshot()
    sub_taxonomy = runtime.get("sub_taxonomy_map", {})
    default_sub_map = runtime.get("default_sub_map", {})

    def real_subs_for(cat: str) -> set[str]:
        mapped = {
            str(value).strip()
            for value in dict(sub_taxonomy.get(cat, {})).values()
            if str(value or "").strip() and str(value).strip() != "no-sub"
        }
        default = str(default_sub_map.get(cat) or "").strip()
        if default and default != "no-sub":
            mapped.add(default)
        return mapped

    known_subs: set[str] = set()
    for cat in sub_taxonomy:
        known_subs.update(real_subs_for(str(cat)))
    for cat in default_sub_map:
        known_subs.update(real_subs_for(str(cat)))

    def normalize(category: str, subcategory: str) -> str:
        subcategory = (subcategory or "").strip()
        if not subcategory:
            return ""
        if subcategory == "no-sub":
            return ""
        if subcategory in real_subs_for(category):
            return subcategory
        if subcategory in known_subs:
            return ""
        return subcategory

    return normalize


def _anchor_matches_group(anchor: dict, audio_type: str, category: str, subcategory: str) -> bool:
    anchor_subcategory = _bucket_label_part(str(anchor.get("subcategory") or ""))
    group_subcategory = _bucket_label_part(subcategory)
    if anchor.get("category") != category or anchor_subcategory != group_subcategory:
        return False
    anchor_type = str(anchor.get("audio_type") or "").strip()
    return not anchor_type or anchor_type == (audio_type or "").strip()


def _candidate_id(record_id: str, audio_type: str, category: str, subcategory: str) -> str:
    raw = f"{record_id}|{audio_type}|{category}|{subcategory}"
    return "ref_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
