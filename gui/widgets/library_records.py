from __future__ import annotations

from typing import Sequence


def records_for_source_rows(model, source_rows: list[int]) -> list[object]:
    if model is None or not hasattr(model, "record"):
        return []
    records = []
    for row in sorted(set(source_rows)):
        try:
            records.append(model.record(row))
        except (IndexError, AttributeError):
            continue
    return records


def paths_for_source_rows(model, source_rows: list[int]) -> list[str]:
    paths = []
    for rec in records_for_source_rows(model, source_rows):
        path = getattr(rec, "source_path", None)
        if path is not None:
            paths.append(str(path))
    return paths


def opposite_audio_type_for_records(records: Sequence[object]) -> str:
    types = {str(getattr(record, "audio_type", "") or "").strip() for record in records}
    if types == {"Loops"}:
        return "Oneshots"
    if types == {"Oneshots"}:
        return "Loops"
    return ""


def tab_separated_selection_text(indexes, data_role=None) -> str:
    rows_data = {}
    for idx in indexes:
        row, column = idx.row(), idx.column()
        if row not in rows_data:
            rows_data[row] = {}
        rows_data[row][column] = idx.data(data_role) if data_role is not None else idx.data()

    text_lines = []
    for row in sorted(rows_data.keys()):
        line = "\t".join(str(rows_data[row][column]) for column in sorted(rows_data[row].keys()))
        text_lines.append(line)
    return "\n".join(text_lines)
