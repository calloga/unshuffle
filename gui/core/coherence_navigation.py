from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QItemSelection, QItemSelectionModel


def source_rows_for_record_ids(model, record_ids: list[str]) -> list[int]:
    if not record_ids or model is None:
        return []
    rows = []
    target_ids = set(item for item in record_ids)
    for row, rec in enumerate(model.records):
        rec_id = str(getattr(rec, "staging_row_id", row) if getattr(rec, "staging_row_id", None) is not None else row)
        if rec_id in target_ids:
            rows.append(row)
    return rows


def select_library_path(app, source_path: Path) -> None:
    model = getattr(app, "model", None)
    proxy = getattr(app, "proxy_model", None)
    view = getattr(getattr(app, "library_tab", None), "view_table", None)
    if model is None or proxy is None or view is None:
        return
    target = source_path.resolve().as_posix().lower()
    source_row = None
    for row, rec in enumerate(getattr(model, "records", []) or []):
        try:
            rec_path = Path(getattr(rec, "source_path", "")).resolve().as_posix().lower()
        except (TypeError, OSError):
            rec_path = str(getattr(rec, "source_path", "") or "").replace("\\", "/").lower()
        if rec_path == target:
            source_row = row
            break
    if source_row is None:
        return
    select_source_rows(app, [source_row])


def select_source_rows(app, source_rows: list[int]) -> None:
    model = getattr(app, "model", None)
    view = getattr(getattr(app, "library_tab", None), "view_table", None)
    proxy = getattr(app, "proxy_model", None)
    if model is None or view is None or proxy is None:
        return
    selection_model = view.selectionModel()
    if selection_model is None:
        return
    selection = QItemSelection()
    first_proxy_index = None
    for source_row in source_rows:
        source_index = model.index(source_row, 0)
        proxy_index = proxy.mapFromSource(source_index)
        if not proxy_index.isValid():
            continue
        if first_proxy_index is None:
            first_proxy_index = proxy_index
        row_selection = QItemSelection(proxy.index(proxy_index.row(), 0), proxy.index(proxy_index.row(), proxy.columnCount() - 1))
        selection.merge(row_selection, QItemSelectionModel.Select)
    if not selection.isEmpty():
        selection_model.clearSelection()
        selection_model.select(selection, QItemSelectionModel.Select | QItemSelectionModel.Rows)
    if first_proxy_index is not None:
        view.scrollTo(first_proxy_index)
        view.setCurrentIndex(first_proxy_index)

