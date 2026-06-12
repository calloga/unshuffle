from __future__ import annotations


def matching_anchor_promotions(record_ids: list[str], records: list, coherence_results: list[dict], anchor_rows: list[dict]) -> tuple[list[str], set[str]]:
    target_ids = {item for item in record_ids}
    records_by_id = {
        str(getattr(rec, "staging_row_id", row) if getattr(rec, "staging_row_id", None) is not None else row): rec
        for row, rec in enumerate(records)
    }
    results_by_id = {str(row.get("record_id") or ""): row for row in coherence_results}
    promote_ids = []
    promoted_record_ids: set[str] = set()
    for record_id in target_ids:
        rec = records_by_id.get(record_id)
        result = results_by_id.get(record_id)
        if rec is None or result is None:
            continue
        cluster_id = str(result.get("cluster_id") or "")
        audio_type = str(getattr(rec, "audio_type", "") or "")
        category = str(getattr(rec, "category", "") or "")
        subcategory = str(getattr(rec, "subcategory", "") or "")
        for anchor in anchor_rows:
            if str(anchor.get("cluster_id") or "") != cluster_id:
                continue
            if str(anchor.get("audio_type") or "") != audio_type:
                continue
            if str(anchor.get("category") or "") != category:
                continue
            if str(anchor.get("subcategory") or "") != subcategory:
                continue
            promote_ids.append(str(anchor.get("anchor_id") or ""))
            promoted_record_ids.add(record_id)
            break
    return sorted({item for item in promote_ids if item}), promoted_record_ids

