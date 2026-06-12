from __future__ import annotations

LIBRARY_VIEW_MODES = ("table", "tree", "map")


def normalize_available_view_modes(modes) -> set[str]:
    normalized = {
        str(mode or "").strip().lower()
        for mode in (modes or [])
        if str(mode or "").strip().lower() in LIBRARY_VIEW_MODES
    }
    return normalized or {"table"}


def is_view_mode_available(mode: str, available_modes: set[str]) -> bool:
    return (mode or "").strip().lower() in available_modes


def first_available_view_mode(available_modes: set[str]) -> str:
    for mode in LIBRARY_VIEW_MODES:
        if mode in available_modes:
            return mode
    return "table"


def normalize_view_mode(mode, available_modes: set[str]) -> str:
    if isinstance(mode, bool):
        mode = "tree" if mode else "table"
    value = str(mode or "").strip().lower()
    if value in set(LIBRARY_VIEW_MODES):
        return value if is_view_mode_available(value, available_modes) else first_available_view_mode(available_modes)
    return "table"
