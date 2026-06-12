from __future__ import annotations

import json

from ..utils.constants import StagingColumn
from ..core.settings_controller import create_app_settings

TABLE_COLUMN_ORDER_KEY = "table_column_order_json"
ALWAYS_HIDDEN_TABLE_COLUMNS = set()
DEFAULT_HIDDEN_TABLE_COLUMNS = {StagingColumn.TYPE, StagingColumn.PATH}


def _visibility_key(column: StagingColumn) -> str:
    return f"table_column_visible_{column.name}"


def _visibility_user_set_key(column: StagingColumn) -> str:
    return f"table_column_visible_user_set_{column.name}"


def save_column_visibility(column: StagingColumn, visible: bool) -> None:
    settings = create_app_settings()
    settings.setValue(_visibility_key(column), visible)
    settings.setValue(_visibility_user_set_key(column), True)


def load_column_visibility(column: StagingColumn) -> bool:
    settings = create_app_settings()
    val = settings.value(_visibility_key(column))
    if val is None:
        return column not in DEFAULT_HIDDEN_TABLE_COLUMNS
    if column in DEFAULT_HIDDEN_TABLE_COLUMNS and settings.value(_visibility_user_set_key(column)) is None:
        return False
    if isinstance(val, str):
        return val.lower() == "true"
    return bool(val)


DEFAULT_COLUMN_WIDTH_BASE = {
    StagingColumn.PACK: 0.19,
    StagingColumn.FILENAME: 0.23,
    StagingColumn.CATEGORY: 0.16,
    StagingColumn.SUBCATEGORY: 0.13,
    StagingColumn.TAGS: 0.16,
    StagingColumn.CONFIDENCE: 0.13,
    StagingColumn.TYPE: 0.08,
    StagingColumn.PATH: 0.18,
}


def visible_table_columns(header_count: int, is_column_hidden) -> list[StagingColumn]:
    cols: list[StagingColumn] = []
    for col in range(header_count):
        try:
            staging_column = StagingColumn(col)
        except ValueError:
            continue
        if staging_column not in ALWAYS_HIDDEN_TABLE_COLUMNS and not is_column_hidden(staging_column):
            cols.append(staging_column)
    return cols


def encode_column_order(header) -> str:
    order = [int(header.logicalIndex(visual)) for visual in range(header.count())]
    return json.dumps(order)


def decode_column_order(raw: object, header_count: int) -> list[int] | None:
    if not raw:
        return None
    try:
        order = [int(item) for item in json.loads(str(raw))]
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    valid = [col for col in order if 0 <= col < header_count]
    valid.extend(col for col in range(header_count) if col not in valid)
    if len(valid) != header_count:
        return None
    return valid


def default_column_width_ratios(cols: list[StagingColumn]) -> dict[StagingColumn, float]:
    total = sum(DEFAULT_COLUMN_WIDTH_BASE.get(col, 0.10) for col in cols) or 1.0
    return {col: DEFAULT_COLUMN_WIDTH_BASE.get(col, 0.10) / total for col in cols}


def captured_column_width_ratios(cols: list[StagingColumn], column_width) -> dict[StagingColumn, float]:
    total_width = sum(max(1, column_width(col)) for col in cols)
    if total_width <= 0:
        return {}
    ratios = {col: max(1, column_width(col)) / total_width for col in cols}
    ratio_sum = sum(ratios.values()) or 1.0
    return {col: ratio / ratio_sum for col, ratio in ratios.items()}


def proportional_column_widths(
    cols: list[StagingColumn],
    ratios: dict[StagingColumn, float],
    total_width: int,
) -> dict[StagingColumn, int]:
    total_width = max(1, total_width)
    floor = min(44, max(1, total_width // max(1, len(cols) * 3)))
    widths = {
        col: max(floor, int(total_width * ratios.get(col, 0.0)))
        for col in cols
    }
    used = sum(widths.values())
    if used > total_width and cols:
        deficit = used - total_width
        shrinkable = [col for col in cols if widths[col] > floor]
        while deficit > 0 and shrinkable:
            per_col = max(1, deficit // len(shrinkable))
            next_shrinkable = []
            for col in shrinkable:
                room = widths[col] - floor
                delta = min(room, per_col)
                widths[col] -= delta
                deficit -= delta
                if widths[col] > floor:
                    next_shrinkable.append(col)
                if deficit <= 0:
                    break
            shrinkable = next_shrinkable
        used = sum(widths.values())
    if used != total_width and cols:
        widths[cols[-1]] = max(floor, widths[cols[-1]] + (total_width - used))
    return widths
