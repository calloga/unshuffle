from __future__ import annotations


def refinement_file_count_text(count: int) -> str:
    return f"{count} file{'s' if count != 1 else ''}"


def target_differs_from_current(row: dict, audio_type: str, category: str, subcategory: str) -> bool:
    return (
        (audio_type or ""),
        (category or ""),
        (subcategory or ""),
    ) != (
        str(row.get("current_audio_type") or ""),
        str(row.get("current_category") or ""),
        str(row.get("current_subcategory") or ""),
    )


def refinement_payload_for_target(row: dict, audio_type: str, category: str, subcategory: str) -> dict:
    payload = dict(row)
    payload["suggested_audio_type"] = audio_type
    payload["suggested_category"] = category
    payload["suggested_subcategory"] = subcategory
    return payload
