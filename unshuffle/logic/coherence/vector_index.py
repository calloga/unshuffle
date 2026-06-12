from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable

from ...core.features import (
    CURRENT_FEATURE_SPACE_VERSION,
    CURRENT_FEATURE_VECTOR_SIZE,
    vector_from_blob,
)
from .models import CoherenceRecord


EXCLUDED_CATEGORIES = {"Non-Audio Assets", "Metadata"}


@dataclass(frozen=True)
class VectorIndexStats:
    total_records: int
    eligible_records: int
    valid_vector_records: int
    coverage: float
    has_minimum_group: bool

    @property
    def can_run(self) -> bool:
        return self.coverage >= 0.5 and self.has_minimum_group


def valid_coherence_vector(value: Any) -> list[float] | None:
    vector = vector_from_blob(value)
    if not vector or len(vector) != CURRENT_FEATURE_VECTOR_SIZE:
        return None
    try:
        result = [item for item in vector]
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(item) for item in result):
        return None
    return result


def eligible_staging_row(row: dict[str, Any]) -> bool:
    if bool(row.get("is_preserved")):
        return False
    category = str(row.get("category") or "").strip()
    if category in EXCLUDED_CATEGORIES:
        return False
    return bool(str(row.get("source_path") or "").strip())


def records_from_staging_rows(rows: Iterable[dict[str, Any]]) -> tuple[list[CoherenceRecord], VectorIndexStats]:
    row_list = list(rows)
    eligible_rows = [row for row in row_list if eligible_staging_row(row)]
    records: list[CoherenceRecord] = []
    group_counts: dict[tuple[str, str, str], int] = {}

    for row in eligible_rows:
        vector = valid_coherence_vector(row.get("feature_vector", row.get("acoustic_vector")))
        if vector is None:
            continue
        category = str(row.get("category") or "").strip()
        subcategory = str(row.get("subcategory") or "").strip()
        audio_type = str(row.get("audio_type") or "").strip()
        try:
            confidence_value = row.get("confidence")
            if confidence_value is None:
                raise TypeError
            confidence = float(confidence_value)
        except (TypeError, ValueError):
            confidence = None
        record_id = str(row.get("row_id") if row.get("row_id") is not None else row.get("id"))
        records.append(
            CoherenceRecord(
                record_id=record_id,
                category=category,
                subcategory=subcategory,
                vector=vector,
                classification_confidence=confidence,
                audio_type=audio_type,
                source_path=str(row.get("source_path") or ""),
                pack=str(row.get("pack") or ""),
            )
        )
        group_key = (audio_type, category, subcategory)
        group_counts[group_key] = group_counts.get(group_key, 0) + 1

    eligible_count = len(eligible_rows)
    valid_count = len(records)
    coverage = (valid_count / eligible_count) if eligible_count else 0.0
    stats = VectorIndexStats(
        total_records=len(row_list),
        eligible_records=eligible_count,
        valid_vector_records=valid_count,
        coverage=coverage,
        has_minimum_group=any(count >= 8 for count in group_counts.values()),
    )
    return records, stats


def current_feature_space_version() -> str:
    return CURRENT_FEATURE_SPACE_VERSION
