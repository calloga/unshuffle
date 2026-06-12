from __future__ import annotations

from gui.utils.constants import StagingColumn
from unshuffle.core.constants import TOKEN_ADJUSTMENT_STEP
from unshuffle.logic.classification import tokenize, weighted_adjustment_tokens


def refinement_updates(model, rows: list[dict]) -> list[tuple]:
    if model is None:
        return []
    updates = []
    row_by_id = {
        str(getattr(rec, "staging_row_id", row) if getattr(rec, "staging_row_id", None) is not None else row): rec
        for row, rec in enumerate(model.records)
    }
    for row in rows:
        rec = row_by_id.get(str(row.get("record_id")))
        if rec is None:
            continue
        suggested_audio_type = str(row.get("suggested_audio_type") or "")
        suggested_category = str(row.get("suggested_category") or "")
        suggested_subcategory = str(row.get("suggested_subcategory") or "")
        if suggested_audio_type and str(getattr(rec, "audio_type", "") or "") != suggested_audio_type:
            updates.append((rec, StagingColumn.TYPE, suggested_audio_type))
        if suggested_category:
            current = (str(getattr(rec, "category", "") or ""), str(getattr(rec, "subcategory", "") or ""))
            new_value = (suggested_category, suggested_subcategory)
            if current != new_value:
                updates.append((rec, StagingColumn.CATEGORY, new_value))
    return updates


def learning_adjustments_for_updates(updates: list[tuple]) -> list[tuple[str, str, float]]:
    adjustments = set()
    for rec, col, new_value in updates:
        if col != StagingColumn.CATEGORY:
            continue
        old_category = str(getattr(rec, "category", "") or "").strip()
        if isinstance(new_value, tuple):
            new_category = str(new_value[0] or "").strip()
        else:
            new_category = str(new_value or "").strip()
        if not old_category or not new_category or old_category == new_category:
            continue
        source_path = getattr(rec, "source_path", "")
        source_name = source_path.name if hasattr(source_path, "name") else str(source_path)
        for token in weighted_adjustment_tokens(tokenize(source_name)):
            adjustments.add((token, old_category, -TOKEN_ADJUSTMENT_STEP))
            adjustments.add((token, new_category, TOKEN_ADJUSTMENT_STEP))
    return list(adjustments)

