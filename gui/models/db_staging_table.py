from __future__ import annotations

import logging
import json
import time
from copy import copy
from pathlib import Path
from typing import Any, Callable, cast

from PySide6.QtCore import Qt, QAbstractTableModel, QModelIndex, QPersistentModelIndex
from PySide6.QtGui import QColor, QUndoStack

from gui.core.filter_query import normalize_source_path_key
from gui.core.staging_session_store import (
    DbRecordSequence,
    LruRecordCache,
    StagingQuery,
    StagingSessionStore,
)
from gui.core.tree_filter_options import (
    EffectiveTaxonomyContext,
    effective_taxonomy_label,
)
from gui.models.staging_table import StagingTableModel
from gui.utils.constants import DRAFT_IS_PRESERVED_FIELD, DRAFT_PRESERVED_ROOT_FIELD, STAGING_HEADERS, StagingColumn
from gui.utils.styles import ColorPalette, make_qcolor
from unshuffle.core import PlanRecord, plan_record_from_staging_row, parse_tags
from unshuffle.core.constants import SUB_TAXONOMY_MAP
from unshuffle.logic.tree_organization.filter_evaluator import FilterEvaluator


class DbBackedStagingTableModel(QAbstractTableModel):
    """Windowed staging table model backed by the active staging_records table."""

    CHUNK_SIZE = 256
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
        self._row_positions: dict[int, int] = {}
        self._record_cache = LruRecordCache(self.MAX_HYDRATED_ROWS)
        self._duplicate_shadow_ids = self.store.duplicate_shadow_row_ids()
        self._unique_values_cache: dict[int, list[str]] = {}
        self._effective_taxonomy_context: EffectiveTaxonomyContext | None = None
        self._effective_taxonomy_cache: dict[int, dict[str, str]] = {}
        self._effective_column_filter_ids: dict[int, set[int]] = {}
        self._matched_ids: set[int] | None = None
        self.matched_ids: set[int] | None = None
        self.audio_types: set[str] | None = None
        self.show_non_audio_assets: bool = False
        self.show_duplicates: bool = True
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
        matched_ids = set(self.matched_ids) if self.matched_ids is not None else None
        for allowed_ids in self._effective_column_filter_ids.values():
            matched_ids = set(allowed_ids) if matched_ids is None else matched_ids & allowed_ids
        return StagingQuery(
            matched_ids=frozenset(matched_ids) if matched_ids is not None else None,
            audio_types=frozenset(self.audio_types) if self.audio_types is not None else None,
            show_non_audio_assets=self.show_non_audio_assets,
            show_duplicates=self.show_duplicates,
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
        row_ids = self.store.row_ids(
            self.query,
            self.group_column,
            descending=self.sort_order == Qt.DescendingOrder,
            effective_taxonomy=self._effective_taxonomy_context,
        )
        self.beginResetModel()
        try:
            self._row_ids = row_ids
            self._row_positions = {row_id: row for row, row_id in enumerate(row_ids)}
            self._unique_values_cache.clear()
        finally:
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
            self._cache_effective_taxonomy([row_id])
            self._record_cache.put_many({row_id: record})
            return record
        return cached

    def set_effective_taxonomy_context(self, context: EffectiveTaxonomyContext | None) -> None:
        if context == self._effective_taxonomy_context:
            return
        self._effective_taxonomy_context = context
        self._effective_taxonomy_cache.clear()
        self._effective_column_filter_ids.clear()
        self._unique_values_cache.clear()
        if self.rowCount():
            self.dataChanged.emit(
                self.index(0, StagingColumn.CATEGORY),
                self.index(self.rowCount() - 1, StagingColumn.SUBCATEGORY),
            )

    def _cache_effective_taxonomy(self, row_ids: list[int]) -> None:
        context = self._effective_taxonomy_context
        missing = [row_id for row_id in row_ids if row_id not in self._effective_taxonomy_cache]
        if context is None or not missing:
            return
        overlays = self.store.effective_taxonomy_for_ids(missing, context)
        for row_id in missing:
            self._effective_taxonomy_cache[row_id] = overlays.get(row_id, {})

    def effective_taxonomy_value(self, row: int, placement: str) -> str:
        rec = self.record(row)
        row_id = self.record_id(row)
        self._cache_effective_taxonomy([row_id])
        overlay = self._effective_taxonomy_cache.get(row_id, {}).get(placement, "")
        canonical = rec.category if placement == "category" else (rec.subcategory or "")
        if placement == "subcategory" and overlay:
            canonical = canonical or "Other"
        return effective_taxonomy_label(overlay, canonical)

    def taxonomy_options_for_index(self, index: QModelIndex, column: StagingColumn) -> list[tuple[str, str]]:
        rec = self.record(index.row())
        context = self._effective_taxonomy_context
        if column == StagingColumn.CATEGORY:
            canonical_values = set(self.store.distinct_values(StagingColumn.CATEGORY))
            canonical_values.update(self.sub_taxonomy_map)
        else:
            canonical_values = {
                value
                for value in self.sub_taxonomy_map.get(rec.category, {}).values()
                if value and value != "no-sub"
            }
            canonical_values.update(self.store.distinct_subcategories(rec.category))
            canonical_values.add("")
        options = list(context.options_for("category" if column == StagingColumn.CATEGORY else "subcategory")) if context else []
        evaluator = FilterEvaluator()
        result: list[tuple[str, str]] = []
        seen: set[str] = set()
        for canonical in sorted(canonical_values, key=lambda value: (not bool(value), str(value).casefold())):
            candidate = copy(rec)
            if column == StagingColumn.CATEGORY:
                candidate.category = str(canonical)
                if candidate.category != rec.category:
                    candidate.subcategory = None
            else:
                candidate.subcategory = str(canonical) or None
            overlay = next((option.label for option in options if evaluator.matches(candidate, option.query)), "")
            display_canonical = str(canonical) or "Other"
            label = effective_taxonomy_label(overlay, display_canonical)
            key = label.casefold()
            if key in seen:
                continue
            seen.add(key)
            result.append((label, str(canonical)))
        return result

    def records_for_rows(self, rows) -> list[PlanRecord]:
        """Hydrate a group of logical rows with one batched store read."""
        positions = [int(row) for row in rows if 0 <= int(row) < len(self._row_ids)]
        row_ids = [self._row_ids[row] for row in positions]
        records_by_id: dict[int, PlanRecord] = {}
        missing_ids = []
        for row_id in row_ids:
            cached = self._record_cache.get(row_id)
            if cached is None:
                missing_ids.append(row_id)
            else:
                records_by_id[row_id] = cached
        if missing_ids:
            hydrated = {
                int(db_row["row_id"]): plan_record_from_staging_row(db_row, parse_tags)
                for db_row in self.store.lightweight_rows_by_ids(missing_ids)
                if db_row.get("row_id") is not None
            }
            records_by_id.update(hydrated)
            self._cache_effective_taxonomy(list(hydrated))
            self._record_cache.put_many(hydrated)
        return [records_by_id[row_id] for row_id in row_ids if row_id in records_by_id]

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
        self._cache_effective_taxonomy(list(records))
        self._record_cache.put_many(records)

    def prewarm_initial_window(self) -> None:
        if self._row_ids:
            self._hydrate_window(0)

    def refresh_duplicate_shadow_ids(self) -> None:
        self._duplicate_shadow_ids = self.store.duplicate_shadow_row_ids()

    def normalized_source_path(self, row: int) -> str:
        try:
            return normalize_source_path_key(self.record(row).source_path)
        except IndexError:
            return ""

    def get_unique_values(self, column: int) -> list[str]:
        cached = self._unique_values_cache.get(column)
        if cached is not None:
            return list(cached)
        if self._effective_taxonomy_context is not None and column in {
            StagingColumn.CATEGORY,
            StagingColumn.SUBCATEGORY,
        }:
            field = "category" if column == StagingColumn.CATEGORY else "subcategory"
            values = sorted(
                {
                    str(row.get(field) or "")
                    for row in self.store.effective_taxonomy_group_counts(self._effective_taxonomy_context)
                    if str(row.get(field) or "")
                },
                key=str.casefold,
            )
        else:
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
                return self.effective_taxonomy_value(index.row(), "category")
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
                effective = self.effective_taxonomy_value(index.row(), "subcategory")
                return effective if effective else self._normalized_subcategory(getattr(rec, "subcategory", ""))
        if role == Qt.EditRole:
            return self._get_record_value(rec, col)
        if role == Qt.UserRole:
            if col == StagingColumn.CONFIDENCE:
                return self.scores.get(self.record_id(index.row())) or rec.confidence
            return getattr(rec, "pack_candidates", None)
        if role == Qt.ToolTipRole and col in (StagingColumn.PACK, StagingColumn.FILENAME, StagingColumn.CATEGORY):
            return StagingTableModel._classification_tooltip(cast(Any, self), rec)
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
        normalized = [
            (rec, col, value)
            for rec, col, value in updates
            if self._get_record_value(rec, col) != value
        ]
        if not normalized:
            return False
        self._apply_bulk_values(normalized)
        if not self._sync_suspended and self.sync_callback is not None:
            synced = set()
            for rec, _col, _value in normalized:
                row_id = getattr(rec, "staging_row_id", None)
                if row_id is None or int(row_id) in synced:
                    continue
                synced.add(int(row_id))
                self.sync_callback(int(row_id), rec)
        return True

    def _apply_bulk_values(self, updates: list[tuple[PlanRecord, int, Any]], *, progress_callback=None) -> None:
        """Apply draft values without re-entering the draft callback."""
        prepared = self._prepare_bulk_values(updates)
        if prepared is None:
            return
        self._write_prepared_bulk_values(prepared, progress_callback=progress_callback)
        self._finalize_prepared_bulk_values(prepared)

    def _prepare_bulk_values(self, updates: list[tuple[PlanRecord, int, Any]]):
        updates = [
            (rec, col, value)
            for rec, col, value in updates
            if getattr(rec, "is_duplicate_shadow", False) is not True
        ]
        if not updates:
            return None

        semantic_columns = {
            StagingColumn.PACK,
            StagingColumn.CATEGORY,
            StagingColumn.SUBCATEGORY,
            StagingColumn.TYPE,
        }
        updates_by_record: dict[int, list[tuple[int, Any]]] = {}
        canonical_records: dict[int, PlanRecord] = {}
        for rec, col, value in updates:
            if col not in semantic_columns:
                continue
            key = int(getattr(rec, "staging_row_id", id(rec)))
            canonical_records[key] = rec
            updates_by_record.setdefault(key, []).append((col, value))
        for canonical, shadow in self.store.duplicate_shadow_records_for(canonical_records.values()):
            key = int(getattr(canonical, "staging_row_id", id(canonical)))
            updates.extend((shadow, col, value) for col, value in updates_by_record.get(key, ()))

        touched: dict[int, PlanRecord] = {}
        columns_by_row: dict[int, set[int]] = {}
        snapshots: dict[int, tuple[PlanRecord, dict[str, Any]]] = {}
        for rec, col, value in updates:
            row_id = getattr(rec, "staging_row_id", None)
            if row_id is None:
                continue
            normalized_row_id = int(row_id)
            if normalized_row_id not in snapshots:
                snapshots[normalized_row_id] = (
                    rec,
                    {
                        "pack": rec.pack,
                        "category": rec.category,
                        "subcategory": rec.subcategory,
                        "audio_type": rec.audio_type,
                        "tags": list(rec.tags or []),
                        "is_preserved": rec.is_preserved,
                        "preserved_root": rec.preserved_root,
                        "is_manual": rec.is_manual,
                    },
                )
            self._set_record_value(rec, col, value)
            touched[normalized_row_id] = rec
            columns_by_row.setdefault(normalized_row_id, set()).add(col)
        if not touched:
            return None

        db_updates: list[tuple[int, dict[str, Any]]] = []
        for row_id, rec in touched.items():
            fields: dict[str, Any] = {}
            record_columns = columns_by_row.get(row_id, set())
            if StagingColumn.PACK in record_columns:
                fields["pack"] = rec.pack
            if StagingColumn.CATEGORY in record_columns:
                fields["category"] = rec.category
                fields["subcategory"] = rec.subcategory or ""
            if StagingColumn.SUBCATEGORY in record_columns:
                fields["subcategory"] = rec.subcategory or ""
            if StagingColumn.TAGS in record_columns:
                fields["tags"] = json.dumps(list(rec.tags or []))
            if StagingColumn.TYPE in record_columns:
                fields["audio_type"] = rec.audio_type
            if DRAFT_IS_PRESERVED_FIELD in record_columns:
                fields["is_preserved"] = 1 if rec.is_preserved else 0
            if DRAFT_PRESERVED_ROOT_FIELD in record_columns:
                fields["preserved_root"] = str(rec.preserved_root) if rec.preserved_root else None
            if fields:
                db_updates.append((row_id, fields))

        return {
            "db_updates": db_updates,
            "touched": touched,
            "columns_by_row": columns_by_row,
            "snapshots": snapshots,
        }

    def _write_prepared_bulk_values(self, prepared, *, progress_callback=None) -> None:
        db_updates = list(prepared.get("db_updates") or [])
        if progress_callback is None:
            self.store.update_rows(db_updates)
        else:
            self.store.update_rows(db_updates, progress_callback=progress_callback)

    def _finalize_prepared_bulk_values(self, prepared) -> None:
        touched = dict(prepared.get("touched") or {})
        columns_by_row = dict(prepared.get("columns_by_row") or {})

        self._record_cache.put_many(touched)
        touched_columns = {column for columns in columns_by_row.values() for column in columns}
        for column in touched_columns:
            self._unique_values_cache.pop(int(column), None)
        if self._bulk_update_requires_index_refresh(touched_columns):
            self.refresh_index()
            return

        visible_rows = sorted(
            self._row_positions[row_id]
            for row_id in touched
            if row_id in self._row_positions
        )
        if visible_rows:
            self.dataChanged.emit(
                self.index(visible_rows[0], 0),
                self.index(visible_rows[-1], self.columnCount() - 1),
            )

    def _rollback_prepared_bulk_values(self, prepared) -> None:
        snapshots = dict(prepared.get("snapshots") or {})
        restored: dict[int, PlanRecord] = {}
        for row_id, (rec, state) in snapshots.items():
            for attr, value in state.items():
                setattr(rec, attr, list(value) if attr == "tags" else value)
            restored[int(row_id)] = rec
        self._record_cache.put_many(restored)
        visible_rows = sorted(
            self._row_positions[row_id]
            for row_id in restored
            if row_id in self._row_positions
        )
        if visible_rows:
            self.dataChanged.emit(
                self.index(visible_rows[0], 0),
                self.index(visible_rows[-1], self.columnCount() - 1),
            )

    def _bulk_update_requires_index_refresh(self, columns: set[int]) -> bool:
        if int(self.group_column) in columns:
            return True
        if any(int(column) in self.column_filters for column in columns):
            return True
        if StagingColumn.TYPE in columns and (
            self.audio_types is not None or not self.show_non_audio_assets
        ):
            return True
        if StagingColumn.TAGS in columns and not self.show_duplicates:
            return True
        return False

    def flags(self, index: QModelIndex | QPersistentModelIndex) -> Qt.ItemFlags:
        if not index.isValid():
            return Qt.ItemFlag.ItemIsEnabled
        if self.record_id(index.row()) in self._duplicate_shadow_ids:
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
        if self._effective_taxonomy_context is not None and col in {
            StagingColumn.CATEGORY,
            StagingColumn.SUBCATEGORY,
        }:
            self.column_filters.pop(int(col), None)
            if values:
                placement = "category" if col == StagingColumn.CATEGORY else "subcategory"
                matched: set[int] = set()
                for value in values:
                    matched.update(
                        self.store.effective_taxonomy_match_ids(
                            placement,
                            str(value),
                            self._effective_taxonomy_context,
                        )
                    )
                self._effective_column_filter_ids[int(col)] = matched
            else:
                self._effective_column_filter_ids.pop(int(col), None)
            self.refresh_index()
            return
        self._effective_column_filter_ids.pop(int(col), None)
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
        show = bool(show)
        if self.show_non_audio_assets == show:
            return
        self.show_non_audio_assets = show
        self.refresh_index()

    def set_show_duplicates(self, show: bool):
        show = bool(show)
        if self.show_duplicates == show:
            return
        self.show_duplicates = show
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
        return StagingTableModel._get_record_value(cast(Any, self), rec, col)

    def _set_record_value(self, rec: PlanRecord, col: int, value: Any) -> None:
        StagingTableModel._set_record_value(cast(Any, self), rec, col, value)

    def _group_value_for_record(self, rec: PlanRecord) -> str:
        return StagingTableModel._group_value_for_record(cast(Any, self), rec)

    def _normalized_subcategory(self, value: Any) -> str:
        return StagingTableModel._normalized_subcategory(cast(Any, self), value)

    def _matched_tokens_for_component(self, component_trace: Any, category: str) -> list[str]:
        return StagingTableModel._matched_tokens_for_component(cast(Any, self), component_trace, category)

    def _quoted_list(self, values: list[str]) -> str:
        return StagingTableModel._quoted_list(cast(Any, self), values)

    def _positive_offset(self, value: Any) -> bool:
        return StagingTableModel._positive_offset(cast(Any, self), value)

    def _top_score_lines(self, raw_scores: dict[str, float], selected_category: str) -> list[str]:
        return StagingTableModel._top_score_lines(cast(Any, self), raw_scores, selected_category)

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
        return StagingTableModel.suspended_sync(cast(Any, self))


def _normalize_source_path_filter(path: str) -> str:
    value = normalize_source_path_key(path).rstrip("/")
    return value + "/" if value else ""
