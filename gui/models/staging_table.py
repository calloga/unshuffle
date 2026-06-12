from __future__ import annotations
from contextlib import contextmanager
from pathlib import Path
from typing import List, Any, Callable, Dict, cast
from PySide6.QtCore import Qt, QAbstractTableModel, QModelIndex, QPersistentModelIndex
from PySide6.QtGui import QColor, QUndoCommand, QUndoStack
from unshuffle.core import PlanRecord, stable_record_identity
from unshuffle.core.constants import (
    DEFAULT_CLASSIFICATION_FLOOR,
    SUB_TAXONOMY_MAP,
)
from gui.utils.constants import DRAFT_IS_PRESERVED_FIELD, DRAFT_PRESERVED_ROOT_FIELD, StagingColumn, STAGING_HEADERS
from gui.utils.styles import ColorPalette, make_qcolor

class StagingTableModel(QAbstractTableModel):
    def __init__(
        self, 
        records: List[PlanRecord], 
        undo_stack: QUndoStack | None = None,
        sync_callback: Callable[[int, PlanRecord], None] | None = None,
        draft_edit_callback: Callable[[PlanRecord, int, Any], bool] | None = None,
        draft_bulk_callback: Callable[[List[tuple[PlanRecord, int, Any]], str], bool] | None = None,
        sub_taxonomy_map: Dict[str, Dict[str, str]] | None = None,
    ):
        super().__init__()
        self.records = records
        self.undo_stack = undo_stack
        self.sync_callback = sync_callback
        self.draft_edit_callback = draft_edit_callback
        self.draft_bulk_callback = draft_bulk_callback
        self.sub_taxonomy_map = sub_taxonomy_map or SUB_TAXONOMY_MAP
        self._sync_suspended = False
        self.group_column = StagingColumn.PACK
        self.headers = list(STAGING_HEADERS)
        self.scores: Dict[int, float] = {}
        self._val_to_color: Dict[str, QColor] = {}
        self.palette = self._build_palette()
        self._unique_values_cache: Dict[int, List[str]] = {}
        self._path_row_cache: Dict[Path, int] = {}
        self._normalized_path_cache: List[str] = []
        self._sort_key_cache: Dict[int, Dict[int, Any]] = {}
        self._rebuild_row_and_color_caches()

    def _build_palette(self) -> List[QColor]:
        values = list(ColorPalette.IDENTITY or ()) or list(ColorPalette.GROUPING_TABLE or ()) or [
            ColorPalette.SELECTION,
            ColorPalette.TABLE_SELECT,
            ColorPalette.PRIMARY,
            ColorPalette.TREE_PACK,
            ColorPalette.TREE_CATEGORY,
            ColorPalette.TREE_LOOP,
        ]
        palette = []
        for value in values:
            color = make_qcolor(value)
            palette.append(color)
        return palette

    def _precalculate_colors(self) -> None:
        self._val_to_color = {}
        for rec in self.records:
            val = self._group_value_for_record(rec)
            if val not in self._val_to_color:
                idx = len(self._val_to_color) % len(self.palette)
                self._val_to_color[val] = self.palette[idx]

    def _rebuild_path_row_cache(self) -> None:
        self._path_row_cache = {
            (rec.source_path if isinstance(rec.source_path, Path) else Path(rec.source_path)): row
            for row, rec in enumerate(self.records)
        }
        self._normalized_path_cache = [self._normalize_source_path(rec.source_path) for rec in self.records]

    def _rebuild_row_and_color_caches(self) -> None:
        n = len(self.records)
        path_row_cache: dict[Path, int] = {}
        normalized_path_cache: list[str] = [None] * n  # type: ignore[list-item]
        val_to_color: dict[str, QColor] = {}
        palette = self.palette
        palette_len = len(palette)
        for row, rec in enumerate(self.records):
            sp = rec.source_path
            path_row_cache[sp if isinstance(sp, Path) else Path(sp)] = row
            normalized_path_cache[row] = self._normalize_source_path(sp)
            val = self._group_value_for_record(rec)
            if val not in val_to_color:
                val_to_color[val] = palette[len(val_to_color) % palette_len]
        self._path_row_cache = path_row_cache
        self._normalized_path_cache = normalized_path_cache
        self._val_to_color = val_to_color

    def _normalize_source_path(self, path) -> str:
        if isinstance(path, Path):
            return path.as_posix().lower()
        return str(path).replace("\\", "/").lower()

    _GROUP_COLUMN_ATTR_MAP: dict = {
        StagingColumn.PACK: "pack",
        StagingColumn.FILENAME: "source_path",
        StagingColumn.CATEGORY: "category",
        StagingColumn.TAGS: "tags",
        StagingColumn.CONFIDENCE: "confidence",
        StagingColumn.PATH: "source_path",
        StagingColumn.TYPE: "audio_type",
        StagingColumn.SUBCATEGORY: "subcategory",
    }

    def _group_value_for_record(self, rec: PlanRecord) -> str:
        attr_name = self._GROUP_COLUMN_ATTR_MAP.get(self.group_column, "pack")
        value = getattr(rec, attr_name, "")
        if self.group_column == StagingColumn.SUBCATEGORY:
            return self._normalized_subcategory(value)
        return str(value)

    def record(self, row: int) -> PlanRecord:
        return self.records[row]

    def record_id(self, row: int) -> int:
        if not (0 <= row < len(self.records)):
            return row
        value = getattr(self.records[row], "staging_row_id", None)
        if value is None:
            return row
        try:
            return int(value)
        except (TypeError, ValueError):
            return row

    def find_record_by_source_path(self, path: Path) -> PlanRecord | None:
        row = self._path_row_cache.get(Path(path))
        if row is None:
            return None
        if 0 <= row < len(self.records):
            return self.records[row]
        return None

    def normalized_source_path(self, row: int) -> str:
        if 0 <= row < len(self._normalized_path_cache):
            return self._normalized_path_cache[row]
        return ""

    def get_unique_values(self, column: int) -> List[str]:
        cached = self._unique_values_cache.get(column)
        if cached is not None:
            return list(cached)

        try:
            col = StagingColumn(column)
        except (TypeError, ValueError):
            return []

        values = set()
        for row, rec in enumerate(self.records):
            if col == StagingColumn.PACK:
                val = rec.pack
            elif col == StagingColumn.FILENAME:
                val = rec.source_path.name
            elif col == StagingColumn.CATEGORY:
                val = rec.category
            elif col == StagingColumn.TAGS:
                val = str(rec.tags) if rec.tags else ""
            elif col == StagingColumn.CONFIDENCE:
                score = self.scores.get(row) or rec.confidence
                if score is not None:
                    try:
                        val = f"{int(float(score) * 100)}%"
                    except (ValueError, TypeError):
                        val = str(score)
                else:
                    val = ""
            elif col == StagingColumn.PATH:
                val = str(rec.source_path).replace("\\", "/").replace(rec.source_path.anchor.replace("\\", "/"), "/")
            elif col == StagingColumn.TYPE:
                val = rec.audio_type
            elif col == StagingColumn.SUBCATEGORY:
                val = self._normalized_subcategory(getattr(rec, "subcategory", ""))
            else:
                val = ""

            if val:
                values.add(val)

        ordered = sorted(values)
        self._unique_values_cache[column] = ordered
        return list(ordered)

    def clear_similarity_scores(self) -> None:
        self.scores.clear()

    def apply_similarity_ranking(self, ranked_row_ids: List[int]) -> None:
        self.scores.clear()
        for rank, row_id in enumerate(ranked_row_ids):
            self.scores[row_id] = 1.0 - (rank / max(1, len(ranked_row_ids)))
        self.set_group_column(StagingColumn.CONFIDENCE)

    def _invalidate_unique_values(self, column: int | None = None) -> None:
        if column is None:
            self._unique_values_cache.clear()
        else:
            self._unique_values_cache.pop(column, None)

    def _invalidate_sort_keys(self, column: int | None = None) -> None:
        if column is None:
            self._sort_key_cache.clear()
        else:
            self._sort_key_cache.pop(column, None)

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:
        return len(self.records)

    def columnCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:
        return len(self.headers)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            if 0 <= section < len(self.headers):
                return self.headers[section]
        if role == Qt.DisplayRole and orientation == Qt.Vertical:
            return str(self.record_id(section) + 1)
        if role == Qt.BackgroundRole and orientation == Qt.Vertical and 0 <= section < len(self.records):
            rec = self.records[section]
            val = self._group_value_for_record(rec)
            color = self._val_to_color.get(val)
            if color is None:
                idx = len(self._val_to_color) % len(self.palette)
                color = self.palette[idx]
                self._val_to_color[val] = color
            return color
        return None

    def data(self, index: QModelIndex | QPersistentModelIndex, role: int = Qt.DisplayRole) -> Any:
        if not index.isValid():
            return None
        row = index.row()
        if not (0 <= row < len(self.records)):
            return None
        rec = self.records[row]
        col = index.column()
        if role == Qt.DisplayRole:
            if col == StagingColumn.PACK: return rec.pack
            if col == StagingColumn.FILENAME: return rec.source_path.name
            if col == StagingColumn.CATEGORY: return rec.category
            if col == StagingColumn.TAGS: return rec.tags
            if col == StagingColumn.CONFIDENCE:
                score = self.scores.get(row) or rec.confidence
                if score is not None:
                    try: return f"{int(float(score) * 100)}%"
                    except (ValueError, TypeError): return str(score)
                return ""
            if col == StagingColumn.PATH:
                return str(rec.source_path).replace("\\", "/").replace(rec.source_path.anchor.replace("\\", "/"), "/")
            if col == StagingColumn.TYPE: return rec.audio_type
            if col == StagingColumn.SUBCATEGORY: return self._normalized_subcategory(getattr(rec, "subcategory", ""))
        if role == Qt.ToolTipRole:
            if col in (StagingColumn.PACK, StagingColumn.FILENAME, StagingColumn.CATEGORY):
                return self._classification_tooltip(rec)
        if role == Qt.EditRole:
            if col == StagingColumn.PACK: return rec.pack
            if col == StagingColumn.CATEGORY: return rec.category
            if col == StagingColumn.TAGS: return rec.tags
            if col == StagingColumn.CONFIDENCE: return self.scores.get(row)
            if col == StagingColumn.TYPE: return rec.audio_type
            if col == StagingColumn.SUBCATEGORY: return self._normalized_subcategory(getattr(rec, "subcategory", ""))
        if role == Qt.UserRole:
            if col == StagingColumn.CONFIDENCE:
                return self.scores.get(row) or rec.confidence
            return getattr(rec, "pack_candidates", None)
        return None

    def _classification_tooltip(self, rec: PlanRecord) -> str:
        lines: list[str] = [f"Category: {rec.category}"]
        subcategory = self._normalized_subcategory(getattr(rec, "subcategory", ""))
        if subcategory:
            lines[0] += f" / {subcategory}"
        evidence = rec.evidence or {}
        if evidence.get("reconstructed"):
            lines.append("- Classification evidence was not stored for this restored row.")
            return "\n".join(lines)
        trace = evidence.get("trace") or {}
        components = trace.get("components") or {}
        stage = str(evidence.get("stage", "") or "")
        stage_explained = False
        if stage == "key_fallback":
            lines.append("- A musical key was detected, so it fell back to Melodics.")
            stage_explained = True
        elif stage == "key_fallback_bass":
            lines.append("- A musical key and bass hint were detected, so it fell back to Bass.")
            stage_explained = True
        elif stage == "f_shortcircuit": lines.append("- The filename alone was strong enough to decide this quickly.")
        elif stage == "specificity": lines.append("- Several categories were close, so specificity rules broke the tie.")
        elif stage == "noise_floor": lines.append("- There were only weak signals, so this was a low-confidence guess.")
        token_adjustments = [
            adjustment
            for adjustment in trace.get("token_adjustments") or []
            if str(adjustment.get("category", "")) == rec.category
        ]
        if token_adjustments:
            tokens = []
            for adjustment in token_adjustments:
                token = str(adjustment.get("token", "")).strip()
                if token and token not in tokens:
                    tokens.append(token)
            if tokens:
                lines.append(f"- Learned Corrections boosted this category from {self._quoted_list(tokens)}.")
            else:
                lines.append("- Learned Corrections boosted this category.")
        global_boosts = [
            boost
            for boost in trace.get("global_boosts") or []
            if str(boost.get("category", "")) == rec.category and self._positive_offset(boost.get("offset"))
        ]
        if global_boosts:
            lines.append("- Library-wide frequency patterns added a small boost to this category.")
        file_hits = self._matched_tokens_for_component(components.get("filename"), rec.category)
        parent_hits = self._matched_tokens_for_component(components.get("parent"), rec.category)
        pack_hits = self._matched_tokens_for_component(components.get("pack"), rec.category)
        if pack_hits: lines.append(f'- Its pack name mentions {self._quoted_list(pack_hits)}.')
        if parent_hits: lines.append(f'- Its parent folder mentions {self._quoted_list(parent_hits)}.')
        if file_hits: lines.append(f'- Its file name mentions {self._quoted_list(file_hits)}.')
        if not (file_hits or parent_hits or pack_hits or token_adjustments or global_boosts or stage_explained):
            lines.append("- It did not have strong direct keywords for this category, so context carried more weight.")
        raw_scores = (rec.evidence or {}).get("raw") or {}
        top_lines = self._top_score_lines(raw_scores, rec.category)
        if top_lines:
            lines.append("- Score breakdown:")
            lines.extend(top_lines)
        return "\n".join(lines)

    def _matched_tokens_for_component(self, component_trace: Any, category: str) -> list[str]:
        if not component_trace:
            return []
        hits = []
        for entry in component_trace.get("token_trace") or []:
            if entry.get("status") != "matched":
                continue
            if any(str(match.get("category")) == category for match in entry.get("matches") or []):
                token = str(entry.get("token", "")).strip()
                if token and token not in hits:
                    hits.append(token)
        return hits

    def _quoted_list(self, values: list[str]) -> str:
        return ", ".join(f'"{value}"' for value in values[:5])

    def _positive_offset(self, value: Any) -> bool:
        try:
            return float(value or 0.0) > 0.0
        except (TypeError, ValueError):
            return False

    def _top_score_lines(self, raw_scores: Dict[str, float], selected_category: str) -> list[str]:
        if not raw_scores: return []
        sorted_scores = sorted(
            ((cat, score) for cat, score in raw_scores.items()),
            key=lambda item: item[1],
            reverse=True,
        )[:3]
        lines = []
        for cat, score in sorted_scores:
            prefix = "  - Selected" if cat == selected_category else "  - Also matched"
            lines.append(f"{prefix} {cat} ({score:.2f})")
        return lines

    def setData(self, index: QModelIndex | QPersistentModelIndex, value: Any, role: int = Qt.EditRole) -> bool:
        if not index.isValid() or role != Qt.EditRole: return False
        row = index.row()
        rec = self.records[row]
        col = index.column()
        old_val = self._get_record_value(rec, col)
        if old_val == value: return True
        if self.draft_edit_callback is not None:
            return self.draft_edit_callback(rec, col, value)
        if self.undo_stack:
            self.undo_stack.push(UndoCommand(self, index, old_val, value))
        else:
            self._set_record_value(rec, col, value)
            self._sync_record(row)
            self.dataChanged.emit(self.index(row, 0), self.index(row, self.columnCount() - 1))
        return True

    def _get_record_value(self, rec: PlanRecord, col: int) -> Any:
        if col == DRAFT_IS_PRESERVED_FIELD: return bool(getattr(rec, "is_preserved", False))
        if col == DRAFT_PRESERVED_ROOT_FIELD: return getattr(rec, "preserved_root", None)
        if col == StagingColumn.PACK: return rec.pack
        if col == StagingColumn.CATEGORY: return rec.category
        if col == StagingColumn.TAGS: return list(rec.tags) if isinstance(rec.tags, list) else rec.tags
        if col == StagingColumn.TYPE: return rec.audio_type
        if col == StagingColumn.SUBCATEGORY: return self._normalized_subcategory(getattr(rec, "subcategory", ""))
        if col == StagingColumn.FILENAME: return rec.source_path.name
        if col == StagingColumn.CONFIDENCE: return rec.confidence
        if col == StagingColumn.PATH: return str(rec.source_path)
        return None

    def _set_record_value(self, rec: PlanRecord, col: int, value: Any) -> None:
        if col == StagingColumn.PACK:
            rec.pack = str(value)
        elif col == StagingColumn.CATEGORY:
            if isinstance(value, tuple):
                category, subcategory = value
                rec.category = str(category or "").strip()
                rec.subcategory = self._normalized_subcategory(subcategory) or None
            else:
                old_category = str(getattr(rec, "category", "") or "")
                rec.category = str(value or "").strip()
                if rec.category != old_category:
                    valid_subs = {sub for sub in self.sub_taxonomy_map.get(rec.category, {}).values() if sub and sub != "no-sub"}
                    current_sub = self._normalized_subcategory(getattr(rec, "subcategory", ""))
                    if current_sub and current_sub not in valid_subs:
                        rec.subcategory = None
            rec.is_manual = True
        elif col == StagingColumn.TAGS:
            rec.tags = value
        elif col == StagingColumn.TYPE:
            rec.audio_type = str(value)
        elif col == StagingColumn.SUBCATEGORY:
            sub_value = self._normalized_subcategory(value)
            valid_subs = {
                sub
                for sub in self.sub_taxonomy_map.get(str(getattr(rec, "category", "") or ""), {}).values()
                if sub and sub != "no-sub"
            }
            rec.subcategory = sub_value if sub_value and sub_value in valid_subs else None
            rec.is_manual = True
        elif col == DRAFT_IS_PRESERVED_FIELD:
            rec.is_preserved = bool(value)
        elif col == DRAFT_PRESERVED_ROOT_FIELD:
            rec.preserved_root = Path(value) if value else None
        visible_col = self._visible_staging_column(col)
        if visible_col is not None:
            self._invalidate_unique_values(visible_col)
            self._invalidate_sort_keys(visible_col)

    def _sync_record(self, row: int) -> None:
        if self._sync_suspended: return
        if self.sync_callback:
            self.sync_callback(self.record_id(row), self.records[row])

    def _apply_bulk_values(self, updates: List[tuple[PlanRecord, int, Any]]) -> None:
        with self.suspended_sync():
            row_by_rec_id = {stable_record_identity(rec): row for row, rec in enumerate(self.records)}
            touched_rows = []
            touched_cols = set()
            for rec, col, value in updates:
                self._set_record_value(rec, col, value)
                row = row_by_rec_id.get(stable_record_identity(rec))
                if row is not None:
                    touched_rows.append(row)
                touched_cols.add(col)
            visible_cols = {col for col in (self._visible_staging_column(col) for col in touched_cols) if col is not None}
            for col in visible_cols:
                self._invalidate_unique_values(col)
                self._invalidate_sort_keys(col)
            if visible_cols & {StagingColumn.FILENAME, StagingColumn.PATH}:
                self._rebuild_path_row_cache()
            if touched_rows:
                min_row, max_row = min(touched_rows), max(touched_rows)
                self.dataChanged.emit(self.index(min_row, 0), self.index(max_row, self.columnCount() - 1))

    @staticmethod
    def _visible_staging_column(col) -> StagingColumn | None:
        try:
            return StagingColumn(col)
        except (TypeError, ValueError):
            return None

    def _sync_bulk_updates(self, updates: List[tuple[PlanRecord, int, Any]]) -> None:
        if not self.sync_callback:
            return
        row_by_rec_id = {stable_record_identity(rec): row for row, rec in enumerate(self.records)}
        synced_rows = set()
        for rec, _col, _value in updates:
            row = row_by_rec_id.get(stable_record_identity(rec))
            if row is None or row in synced_rows:
                continue
            synced_rows.add(row)
            self._sync_record(row)

    def apply_bulk_updates(self, updates: List[tuple[PlanRecord, int, Any]], text: str = "") -> bool:
        if not updates:
            return False
        if self.draft_bulk_callback is not None:
            return self.draft_bulk_callback(updates, text or "Bulk Edit")
        normalized = []
        for rec, col, new_val in updates:
            old_val = self._get_record_value(rec, col)
            if old_val == new_val:
                continue
            normalized.append((rec, col, old_val, new_val))
        if not normalized:
            return False
        if self.undo_stack:
            self.undo_stack.push(BulkEditCommand(self, normalized, text or "Bulk Edit"))
        else:
            self._apply_bulk_values([(rec, col, new_val) for rec, col, _old_val, new_val in normalized])
            self._sync_bulk_updates([(rec, col, new_val) for rec, col, _old_val, new_val in normalized])
        return True

    @contextmanager
    def suspended_sync(self):
        previous = self._sync_suspended
        self._sync_suspended = True
        try: yield
        finally: self._sync_suspended = previous

    def flags(self, index: QModelIndex | QPersistentModelIndex) -> Qt.ItemFlags:
        if not index.isValid(): return Qt.ItemFlag.ItemIsEnabled
        if index.column() in [StagingColumn.PACK, StagingColumn.CATEGORY, StagingColumn.SUBCATEGORY, StagingColumn.TAGS]:
            return Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEditable | Qt.ItemFlag.ItemIsEnabled
        return Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled

    def set_group_column(self, col: int) -> None:
        self.layoutAboutToBeChanged.emit()
        self.group_column = StagingColumn(col)
        old_scores = {}
        if self.scores:
            old_scores = {
                id(self.records[i]): score
                for i, score in self.scores.items()
                if i < len(self.records)
            }
        sort_data = []
        key_cache = self._sort_key_cache.setdefault(col, {})
        for i, rec in enumerate(self.records):
            rec_id = id(rec)
            if rec_id in key_cache:
                val = key_cache[rec_id]
            else:
                val = self._sort_value_for_record(rec, col)
                key_cache[rec_id] = val
            sort_data.append((val, rec))
        sort_data.sort(key=lambda x: x[0])
        self.records = [x[1] for x in sort_data]
        if old_scores:
            self.scores = {
                i: old_scores[id(rec)]
                for i, rec in enumerate(self.records)
                if id(rec) in old_scores
            }
        self._invalidate_unique_values()
        self._rebuild_row_and_color_caches()
        self.layoutChanged.emit()

    def _sort_value_for_record(self, rec: PlanRecord, col: int) -> Any:
        if col == StagingColumn.PACK:
            return rec.pack.lower()
        if col == StagingColumn.FILENAME:
            return rec.source_path.name.lower()
        if col == StagingColumn.CATEGORY:
            return rec.category.lower()
        if col == StagingColumn.TAGS:
            return ",".join(rec.tags).lower() if rec.tags else ""
        if col == StagingColumn.CONFIDENCE:
            try:
                return -float(rec.confidence)
            except (TypeError, ValueError):
                return 0.0
        if col == StagingColumn.PATH:
            return str(rec.source_path).lower()
        if col == StagingColumn.TYPE:
            return rec.audio_type.lower()
        if col == StagingColumn.SUBCATEGORY:
            return self._normalized_subcategory(getattr(rec, "subcategory", "")).lower()
        return ""

    def _normalized_subcategory(self, value: Any) -> str:
        text = str(value or "").strip()
        if text == "no-sub":
            return ""
        return text

    def refresh_theme_palette(self) -> None:
        self.palette = self._build_palette()
        self._val_to_color.clear()
        self._precalculate_colors()
        if self.rowCount() and self.columnCount():
            self.dataChanged.emit(self.index(0, 0), self.index(self.rowCount() - 1, self.columnCount() - 1))

class UndoCommand(QUndoCommand):
    def __init__(self, model: StagingTableModel, index: QModelIndex | QPersistentModelIndex, old_val: Any, new_val: Any):
        col = index.column()
        header = model.headers[col] if col < len(model.headers) else StagingColumn(col).name.capitalize()
        super().__init__(f"Change {header}")
        self.model, self.record, self.column = model, model.records[index.row()], col
        self.old_val, self.new_val = old_val, new_val
    def _resolve_row(self) -> int:
        try: return self.model.records.index(self.record)
        except ValueError: return -1
    def undo(self) -> None:
        row = self._resolve_row()
        if row < 0: return
        self.model._set_record_value(self.record, self.column, self.old_val)
        self.model._sync_record(row)
        self.model.dataChanged.emit(self.model.index(row, 0), self.model.index(row, self.model.columnCount() - 1))
    def redo(self) -> None:
        row = self._resolve_row()
        if row < 0: return
        self.model._set_record_value(self.record, self.column, self.new_val)
        self.model._sync_record(row)
        self.model.dataChanged.emit(self.model.index(row, 0), self.model.index(row, self.model.columnCount() - 1))

class BulkEditCommand(QUndoCommand):
    def __init__(self, model: StagingTableModel, updates: List[tuple[PlanRecord, int, Any, Any]], text: str):
        super().__init__(text)
        self.model, self.updates = model, updates
    def undo(self) -> None:
        applied = [(rec, col, old_val) for rec, col, old_val, _new_val in self.updates]
        self.model._apply_bulk_values(applied)
        self.model._sync_bulk_updates(applied)
    def redo(self) -> None:
        applied = [(rec, col, new_val) for rec, col, _old_val, new_val in self.updates]
        self.model._apply_bulk_values(applied)
        self.model._sync_bulk_updates(applied)
