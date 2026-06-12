from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Iterable


SUGGESTION_FIELD_SPECS = (
    ("type", "audio_type"),
    ("category", "category"),
    ("subcategory", "subcategory"),
    ("packname", "pack"),
    ("tag", "tags"),
    ("name", "source_path"),
)


def saved_filter_queries(filters: Iterable[dict] | None) -> list[str]:
    queries: list[str] = []
    for item in filters or []:
        if not isinstance(item, dict):
            continue
        query = str(item.get("query") or "").strip()
        if query:
            queries.append(query)
    return list(dict.fromkeys(queries))


def build_filter_suggestions(records: Iterable[object], saved_filters: Iterable[str] | None = None) -> list[str]:
    suggestions: list[str] = [item.strip() for item in saved_filters or [] if item.strip()]
    values_by_field: dict[str, set[str]] = defaultdict(set)
    for record in records or []:
        for prefix, attr in SUGGESTION_FIELD_SPECS:
            if attr == "tags":
                for tag in getattr(record, "tags", []) or []:
                    if str(tag).strip():
                        values_by_field[prefix].add(str(tag).strip())
                continue
            value = getattr(record, attr, "") or ""
            if attr == "source_path":
                value = Path(value).name if not hasattr(value, "name") else value.name
            if str(value).strip():
                values_by_field[prefix].add(str(value).strip())
    for prefix, values in values_by_field.items():
        for value in sorted(values, key=str.lower)[:500]:
            escaped = value.replace('"', '\\"')
            suggestions.append(f'{prefix}:"{escaped}"')
    return list(dict.fromkeys(suggestions))
