from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import Qt, QAbstractTableModel, QModelIndex, QPersistentModelIndex
from PySide6.QtGui import QColor, QUndoStack

from gui.core.filter_query import normalize_source_path_key
from gui.core.staging_session_store import (
    DbRecordSequence,
    LruRecordCache,
    StagingQuery,
    StagingSessionStore,
)
from gui.models.staging_table import StagingTableModel
from gui.utils.constants import DRAFT_IS_PRESERVED_FIELD, DRAFT_PRESERVED_ROOT_FIELD, STAGING_HEADERS, StagingColumn
from gui.utils.styles import ColorPalette, make_qcolor
from unshuffle.core import PlanRecord, plan_record_from_staging_row, parse_tags
from unshuffle.core.constants import SUB_TAXONOMY_MAP


class DbBackedStagingTableModel(QAbstractTableModel):
    """Windowed staging table model backed by the active staging_records table."""

    CHUNK_SIZE = 1500
    MAX_HYDRATED_ROWS = 5000
    _GROUP_COLUMN_ATTR_MAP = StagingTableModel._GROUP_COLUMN_ATTR_MAP

    def __init__(
        self,
        store: StagingSessionStore,
        undo_stack: QUndoStack | None = None,
        *,
        sub_taxonomy_map: dict[str, dict[str, str]] | None = None,
        sync_callback: Callable[[int, PlanRecord], None] | None = None,
        draft_edit_callback: Callable[[PlanRecord, int, Any], bool] | None = None,
        draft_bulk_callback: Callable[[list[tuple[PlanRecord, int, Any]], str], bool] | None = None,
    ):
        super().__init__()
        self.store = store
        self.undo_stack = undo_stack
        self.sync_callback = sync_callback
        self.draft_edit_callback = draft_edit_callback
        self.draft_bulk_callback = draft_bulk_callback
        self.headers = list(STAGING_HEADERS)
        self.group_column = StagingColumn.PACK
        self.sort_order = Qt.AscendingOrder
        self.sub_taxonomy_map = sub_taxonomy_map or SUB_TAXONOMY_MAP
        self._sync_suspended = False
        self._row_ids: list[int] = []
        self._record_cache = LruRecordCache(self.MAX_HYDRATED_ROWS)
        self._unique_values_cache: dict[int, list[str]] = {}
        self._matched_ids: set[int] | None = None
        self.matched_ids: set[int] | None = None
        self.audio_types: set[str] | None = None
        self.show_non_audio_assets: bool = False
        self.path_filters: set[str] = set()
        self._norm_path_filters: list[str] = []
        self.confidence_min: float = 0.0
        self.confidence_max: float = 1.0
        self.column_filters: dict[int, set[str]] = {}
        self.similarity_active = False
        self.similarity_bias: float = 0.0
        self.similarity_distances: dict[int, float] = {}
        self.similarity_anchor_row = -1
        self._similarity_ranks: dict[int, float] = {}
        self._similarity_rows: set[int] | None = None
        self.scores: dict[int, float] = {}
        self.records = DbRecordSequence(store, self)
        self.refresh_index()

    @property
    def query(self) -> StagingQuery:
        return StagingQuery(
            matched_ids=frozenset(self.matched_ids) if self.matched_ids is not None else None,
            audio_types=frozenset(self.audio_types) if self.audio_types is not None else None,
            show_non_audio_assets=self.show_non_audio_assets,
            path_prefixes=tuple(self._norm_path_filters),
            confidence_min=self.confidence_min,
            confidence_max=self.confidence_max,
            column_filters=tuple(
                (int(col), tuple(sorted(str(value) for value in values)))
                for col, values in sorted(self.column_filters.items())
            ),
            similarity_rows=frozenset(self._similarity_rows) if self._similarity_rows is not None else None,
        )

    def sourceModel(self):
        return self

    def mapToSource(self, index):
        return index

    def invalidate(self):
        self.refresh_index()

    def sort(self, column: int, order: Qt.SortOrder = Qt.AscendingOrder):
        if column == -1:
            return
        self.sort_order = order
        self.set_group_column(column)

    def refresh_index(self) -> None:
        started = time.perf_counter()
        self.beginResetModel()
        self._row_ids = self.store.row_ids(
            self.query,
            self.group_column,
            descending=self.sort_order == Qt.DescendingOrder,
        )
        self._unique_values_cache.clear()
        self.endResetModel()
        if self._row_ids:
            self.headerDataChanged.emit(Qt.Vertical, 0, len(self._row_ids) - 1)
        elapsed_ms = (time.perf_counter() - started) * 1000
        logging.getLogger("unshuffle").debug(
            "DB staging index refresh: rows=%s sort=%s desc=%s query=%s elapsed=%.1fms",
            len(self._row_ids),
            int(self.group_column),
            self.sort_order == Qt.DescendingOrder,
            self.query,
            elapsed_ms,
        )

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._row_ids)

    def columnCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.headers)

    def record_id(self, row: int) -> int:
        if 0 <= row < len(self._row_ids):
            return int(self._row_ids[row])
        return int(row)

    def record(self, row: int) -> PlanRecord:
        if not (0 <= row < len(self._row_ids)):
            raise IndexError(row)
        row_id = self._row_ids[row]
        cached = self._record_cache.get(row_id)
        if cached is not None:
            return cached
        self._hydrate_window(row)
        cached = self._record_cache.get(row_id)
        if cached is None:
            record = self.store.record_by_row_id(row_id)
            if record is None:
                raise IndexError(row)
            self._record_cache.put_many({row_id: record})
            return record
        return cached

    def _hydrate_window(self, row: int) -> None:
        chunk = max(0, row // self.CHUNK_SIZE)
        start = chunk * self.CHUNK_SIZE
        end = min(len(self._row_ids), (chunk + 1) * self.CHUNK_SIZE)
        rows = self.store.lightweight_rows_by_ids(self._row_ids[start:end])
        records = {
            int(db_row["row_id"]): plan_record_from_staging_row(db_row, parse_tags)
            for db_row in rows
            if db_row.get("row_id") is not None
        }
        self._record_cache.put_many(records)

    def normalized_source_path(self, row: int) -> str:
        try:
            return normalize_source_path_key(self.record(row).source_path)
        except IndexError:
            return ""

    def get_unique_values(self, column: int) -> list[str]:
        cached = self._unique_values_cache.get(column)
        if cached is not None:
            return list(cached)
        values = self.store.distinct_values(column)
        self._unique_values_cache[column] = values
        return list(values)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal and 0 <= section < len(self.headers):
            return self.headers[section]
        if role == Qt.DisplayRole and orientation == Qt.Vertical:
            return str(section + 1)
        if role == Qt.BackgroundRole and orientation == Qt.Vertical:
            try:
                group = self._group_value_for_record(self.record(section))
            except IndexError:
                group = ""
            return self._group_color(group)
        return None

    def data(self, index: QModelIndex | QPersistentModelIndex, role: int = Qt.DisplayRole) -> Any:
        if not index.isValid():
            return None
        try:
            rec = self.record(index.row())
        except IndexError:
            return None
        col = index.column()
        if role == Qt.DisplayRole:
            if col == StagingColumn.PACK:
                return rec.pack
            if col == StagingColumn.FILENAME:
                return rec.source_path.name
            if col == StagingColumn.CATEGORY:
                return rec.category
            if col == StagingColumn.TAGS:
                return rec.tags
            if col == StagingColumn.CONFIDENCE:
                score = self.scores.get(self.record_id(index.row())) or rec.confidence
                if score is not None:
                    try:
                        return f"{int(float(score) * 100)}%"
                    except (ValueError, TypeError):
                        return str(score)
                return ""
            if col == StagingColumn.PATH:
                return str(rec.source_path).replace("\\", "/").replace(rec.source_path.anchor.replace("\\", "/"), "/")
            if col == StagingColumn.TYPE:
                return rec.audio_type
            if col == StagingColumn.SUBCATEGORY:
                return self._normalized_subcategory(getattr(rec, "subcategory", ""))
        if role == Qt.EditRole:
            return self._get_record_value(rec, col)
        if role == Qt.UserRole:
            if col == StagingColumn.CONFIDENCE:
                return self.scores.get(self.record_id(index.row())) or rec.confidence
            return getattr(rec, "pack_candidates", None)
        if role == Qt.ToolTipRole and col in (StagingColumn.PACK, StagingColumn.FILENAME, StagingColumn.CATEGORY):
            return StagingTableModel._classification_tooltip(self, rec)
        return None

    def setData(self, index: QModelIndex | QPersistentModelIndex, value: Any, role: int = Qt.EditRole) -> bool:
        if not index.isValid() or role != Qt.EditRole:
            return False
        row = index.row()
        try:
            rec = self.record(row)
        except IndexError:
            return False
        if getattr(rec, "is_duplicate_shadow", False) is True:
            return False
        col = index.column()
        old_val = self._get_record_value(rec, col)
        if old_val == value:
            return True
        if self.draft_edit_callback is not None:
            return self.draft_edit_callback(rec, col, value)
        self._set_record_value(rec, col, value)
        row_id = self.record_id(row)
        self.store.update_record(row_id, rec)
        if not self._sync_suspended and self.sync_callback is not None:
            self.sync_callback(row_id, rec)
        self._record_cache.put_many({row_id: rec})
        self.dataChanged.emit(self.index(row, 0), self.index(row, self.columnCount() - 1))
        return True

    def apply_bulk_updates(self, updates: list[tuple[PlanRecord, int, Any]], text: str = "") -> bool:
        updates = [(rec, col, value) for rec, col, value in updates if getattr(rec, "is_duplicate_shadow", False) is not True]
        if not updates:
            return False
        if self.draft_bulk_callback is not None:
            return self.draft_bulk_callback(updates, text or "Bulk Edit")
        changed = False
        for rec, col, value in updates:
            row_id = getattr(rec, "staging_row_id", None)
            if row_id is None:
                continue
            if self._get_record_value(rec, col) == value:
                continue
            self._set_record_value(rec, col, value)
            self.store.update_record(int(row_id), rec)
            if not self._sync_suspended and self.sync_callback is not None:
                self.sync_callback(int(row_id), rec)
            self._record_cache.put_many({int(row_id): rec})
            changed = True
        if changed:
            self._unique_values_cache.clear()
            self.dataChanged.emit(self.index(0, 0), self.index(max(0, self.rowCount() - 1), self.columnCount() - 1))
        return changed

    def flags(self, index: QModelIndex | QPersistentModelIndex) -> Qt.ItemFlags:
        if not index.isValid():
            return Qt.ItemFlag.ItemIsEnabled
        try:
            rec = self.record(index.row())
        except IndexError:
            return Qt.ItemFlag.ItemIsEnabled
        if getattr(rec, "is_duplicate_shadow", False) is True:
            return Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled
        if index.column() in [StagingColumn.PACK, StagingColumn.CATEGORY, StagingColumn.SUBCATEGORY, StagingColumn.TAGS]:
            return Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEditable | Qt.ItemFlag.ItemIsEnabled
        return Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled

    def set_group_column(self, col: int) -> None:
        try:
            self.group_column = StagingColumn(col)
        except (TypeError, ValueError):
            self.group_column = StagingColumn.PACK
        self.refresh_index()

    def set_column_filters(self, col: int, values: set | None):
        if values:
            self.column_filters[int(col)] = {str(value) for value in values}
        else:
            self.column_filters.pop(int(col), None)
        self.refresh_index()

    def set_matched_ids(self, ids: set[int] | None):
        self.matched_ids = set(ids) if ids is not None else None
        self.refresh_index()

    def set_audio_types(self, types: set[str] | None):
        self.audio_types = set(types) if types is not None else None
        self.refresh_index()

    def set_show_non_audio_assets(self, show: bool):
        self.show_non_audio_assets = bool(show)
        self.refresh_index()

    def set_path_filter(self, root_path: str, is_active: bool):
        if is_active:
            self.path_filters.add(root_path)
        else:
            self.path_filters.discard(root_path)
        self._norm_path_filters = [_normalize_source_path_filter(path) for path in self.path_filters]
        self.refresh_index()

    def set_confidence_range(self, min_val: float, max_val: float):
        self.confidence_min = float(min_val)
        self.confidence_max = float(max_val)
        self.refresh_index()

    def set_similarity_data(self, distances: dict[int, float], avg_dist: float, anchor_row: int = -1):
        self.similarity_active = True
        self.similarity_distances = dict(distances)
        self.similarity_anchor_row = int(anchor_row)
        self._rebuild_similarity_window()
        ranked = sorted(distances.items(), key=lambda item: item[1])
        denom = max(1, len(ranked) - 1)
        self.scores = {int(row_id): 1.0 - (rank / denom) for rank, (row_id, _dist) in enumerate(ranked)}
        self.refresh_index()

    def set_similarity_bias(self, bias: int):
        new_bias = float(bias)
        if self.similarity_bias == new_bias:
            return
        self.similarity_bias = new_bias
        self._rebuild_similarity_window()
        self.refresh_index()

    def clear_similarity(self):
        self.similarity_active = False
        self.similarity_bias = 0.0
        self.similarity_distances = {}
        self._similarity_ranks = {}
        self._similarity_rows = None
        self.scores.clear()
        self.refresh_index()

    def clear_similarity_scores(self) -> None:
        self.scores.clear()

    def apply_similarity_ranking(self, ranked_row_ids: list[int]) -> None:
        denom = max(1, len(ranked_row_ids))
        self.scores = {int(row_id): 1.0 - (rank / denom) for rank, row_id in enumerate(ranked_row_ids)}
        self._similarity_rows = set(int(row_id) for row_id in ranked_row_ids)
        self.refresh_index()

    def _rebuild_similarity_window(self) -> None:
        if not self.similarity_active:
            self._similarity_ranks = {}
            self._similarity_rows = None
            return
        ranked = sorted(
            ((row_id, distance) for row_id, distance in self.similarity_distances.items() if row_id != self.similarity_anchor_row),
            key=lambda item: item[1],
        )
        if not ranked:
            self._similarity_ranks = {}
            self._similarity_rows = {self.similarity_anchor_row} if self.similarity_anchor_row >= 0 else set()
            return
        denominator = max(1, len(ranked) - 1)
        self._similarity_ranks = {
            int(row_id): index / denominator
            for index, (row_id, _distance) in enumerate(ranked)
        }
        allowed = set(self._similarity_ranks)
        if self.similarity_anchor_row >= 0:
            allowed.add(self.similarity_anchor_row)
        if self.similarity_bias:
            cutoff = max(0.0, 1.0 - abs(self.similarity_bias) / 100.0)
            allowed = {
                row_id
                for row_id, rank in self._similarity_ranks.items()
                if (rank <= cutoff if self.similarity_bias > 0 else rank >= 1.0 - cutoff)
            }
            if self.similarity_anchor_row >= 0:
                allowed.add(self.similarity_anchor_row)
        self._similarity_rows = allowed

    def find_record_by_source_path(self, path: Path) -> PlanRecord | None:
        text = str(path).replace("\\", "/")
        cursor = self.store.conn.execute(
            """
            SELECT row_id FROM staging_records
            WHERE session_id = ? AND REPLACE(source_path, '\\', '/') = ?
            LIMIT 1
            """,
            (self.store.session_id, text),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return self.store.record_by_row_id(int(row[0]))

    def _get_record_value(self, rec: PlanRecord, col: int) -> Any:
        return StagingTableModel._get_record_value(self, rec, col)

    def _set_record_value(self, rec: PlanRecord, col: int, value: Any) -> None:
        StagingTableModel._set_record_value(self, rec, col, value)

    def _group_value_for_record(self, rec: PlanRecord) -> str:
        return StagingTableModel._group_value_for_record(self, rec)

    def _normalized_subcategory(self, value: Any) -> str:
        return StagingTableModel._normalized_subcategory(self, value)

    def _matched_tokens_for_component(self, component_trace: Any, category: str) -> list[str]:
        return StagingTableModel._matched_tokens_for_component(self, component_trace, category)

    def _quoted_list(self, values: list[str]) -> str:
        return StagingTableModel._quoted_list(self, values)

    def _positive_offset(self, value: Any) -> bool:
        return StagingTableModel._positive_offset(self, value)

    def _top_score_lines(self, raw_scores: dict[str, float], selected_category: str) -> list[str]:
        return StagingTableModel._top_score_lines(self, raw_scores, selected_category)

    def _visible_staging_column(self, col) -> StagingColumn | None:
        return StagingTableModel._visible_staging_column(col)

    def _invalidate_unique_values(self, column: int | None = None) -> None:
        if column is None:
            self._unique_values_cache.clear()
        else:
            self._unique_values_cache.pop(column, None)

    def _invalidate_sort_keys(self, column: int | None = None) -> None:
        return

    def _group_color(self, value: str) -> QColor:
        palette = list(ColorPalette.IDENTITY or ()) or list(ColorPalette.GROUPING_TABLE or ()) or [ColorPalette.SELECTION]
        index = abs(hash(value or "")) % len(palette)
        return make_qcolor(palette[index])

    def suspended_sync(self):
        return StagingTableModel.suspended_sync(self)


def _normalize_source_path_filter(path: str) -> str:
    value = normalize_source_path_key(path).rstrip("/")
    return value + "/" if value else ""
