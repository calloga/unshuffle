from __future__ import annotations

VIEW_MODES = ("table", "tree", "map")


def normalize_view_mode(mode, is_available, first_available) -> str:
    if isinstance(mode, bool):
        mode = "tree" if mode else "table"
    value = str(mode or "").strip().lower()
    if value in set(VIEW_MODES) and is_available(value):
        return value
    return str(first_available())


def next_view_mode(current: str, is_available) -> str:
    available = [mode for mode in VIEW_MODES if is_available(mode)]
    if not available:
        available = ["table"]
    try:
        index = available.index((current or "").strip().lower())
    except ValueError:
        index = -1
    return available[(index + 1) % len(available)]


def normalize_library_tab_mode(mode: str, fallback_index: int | None = None) -> str:
    value = (mode or "").strip().lower()
    if value in set(VIEW_MODES):
        return value
    if fallback_index is not None:
        return "tree" if fallback_index == 1 else "table"
    return "table"


def filtered_source_records(model, proxy_model) -> list[object]:
    records = []
    for row in range(proxy_model.rowCount()):
        proxy_index = proxy_model.index(row, 0)
        if not proxy_index.isValid():
            continue
        source_index = proxy_model.mapToSource(proxy_index)
        if not source_index.isValid():
            continue
        source_row = source_index.row()
        if source_row < 0:
            continue
        try:
            records.append(model.record(source_row))
        except (IndexError, ValueError):
            continue
    return records
