from __future__ import annotations

ONESHOT_EXCLUDED_CATEGORY_OPTIONS = {"Full Drums"}
INVALID_CATEGORY_OPTIONS_BY_AUDIO_TYPE = {
    "Oneshots": ONESHOT_EXCLUDED_CATEGORY_OPTIONS,
}
FALLBACK_CATEGORY_BY_AUDIO_TYPE = {
    "Oneshots": "Uncategorized",
}


def category_is_valid_for_audio_type(category: str, audio_type: str) -> bool:
    category = (category or "").strip()
    audio_type = (audio_type or "").strip()
    if not category or not audio_type:
        return True
    return category not in INVALID_CATEGORY_OPTIONS_BY_AUDIO_TYPE.get(audio_type, set())


def fallback_category_for_audio_type(audio_type: str) -> str:
    return FALLBACK_CATEGORY_BY_AUDIO_TYPE.get((audio_type or "").strip(), "Uncategorized")


def category_options_for_audio_type(
    categories: list[str] | tuple[str, ...],
    audio_type: str,
) -> list[str]:
    return [
        category
        for category in categories
        if category_is_valid_for_audio_type(category, audio_type)
    ]


def category_options_for_type_state(
    categories: list[str] | tuple[str, ...],
    oneshots: bool,
    loops: bool,
    all_files: bool,
) -> list[tuple[str, str]]:
    if oneshots and not loops and not all_files:
        visible = category_options_for_audio_type(categories, "Oneshots")
    else:
        visible = list(categories)
    return [(cat, cat) for cat in visible]
