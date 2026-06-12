from PySide6.QtCore import Qt, QSortFilterProxyModel
import logging
from gui.utils.constants import StagingColumn
from gui.core.filter_query import normalize_source_path_key

class MultiFilterProxyModel(QSortFilterProxyModel):
    """Proxy model supporting a global FTS5 ID filter and per‑column value filters."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.column_filters: dict[int, set] = {}
        self.matched_ids: set[int] | None = None
        self.audio_types: set[str] | None = None
        self.show_non_audio_assets: bool = False
        self.path_filters: set[str] = set()
        self._norm_path_filters: list[str] = []
        self.similarity_bias: float = 0
        self.similarity_avg: float = 0.0
        self.similarity_distances: dict[int, float] = {}
        self.similarity_anchor_row: int = -1
        self.similarity_active: bool = False
        self._similarity_ranks: dict[int, float] = {}
        self.confidence_min: float = 0.0
        self.confidence_max: float = 1.0

    def _refresh_filter(self):
        if hasattr(self, "beginFilterChange") and hasattr(self, "endFilterChange"):
            self.beginFilterChange()
            self.endFilterChange(QSortFilterProxyModel.Direction.Rows)
            return
        self.invalidate()

    def set_column_filters(self, col: int, values: set | None):
        new_values = set(values) if values else None
        old_values = self.column_filters.get(col)
        if new_values is None:
            if col not in self.column_filters:
                return
            self.column_filters.pop(col, None)
        else:
            if old_values == new_values:
                return
            self.column_filters[col] = new_values
        self._refresh_filter()

    def set_matched_ids(self, ids: set[int] | None):
        if ids is None and self.matched_ids is None:
            return
        new_ids = set(ids) if ids is not None else None
        if new_ids is not None and self.matched_ids == new_ids:
            return
        self.matched_ids = new_ids
        self._refresh_filter()

    def set_audio_types(self, types: set[str] | None):
        new_types = set(types) if types is not None else None
        if self.audio_types == new_types:
            return
        self.audio_types = new_types
        self._refresh_filter()

    def set_show_non_audio_assets(self, show: bool):
        if self.show_non_audio_assets == show:
            return
        self.show_non_audio_assets = show
        self._refresh_filter()

    def set_path_filter(self, root_path: str, is_active: bool):
        old_filters = set(self.path_filters)
        if is_active: self.path_filters.add(root_path)
        else: self.path_filters.discard(root_path)
        if self.path_filters == old_filters:
            return
        self._norm_path_filters = []
        for root in self.path_filters:
            r = normalize_source_path_key(root)
            if not r.endswith("/"): r += "/"
            self._norm_path_filters.append(r)
        self._refresh_filter()

    def set_similarity_bias(self, bias: int):
        new_bias = float(bias)
        if self.similarity_bias == new_bias:
            return
        self.similarity_bias = new_bias
        self._rebuild_similarity_window()
        self._refresh_filter()

    def set_similarity_data(self, distances: dict[int, float], avg_dist: float, anchor_row: int = -1):
        new_distances = dict(distances)
        new_avg = avg_dist
        new_anchor = anchor_row
        if (
            self.similarity_active
            and self.similarity_distances == new_distances
            and self.similarity_avg == new_avg
            and self.similarity_anchor_row == new_anchor
        ):
            return
        self.similarity_distances = new_distances
        self.similarity_avg = new_avg
        self.similarity_anchor_row = new_anchor
        self.similarity_active = True
        self._rebuild_similarity_window()
        self._refresh_filter()

    def clear_similarity(self):
        if not self.similarity_active and not self.similarity_distances and not self._similarity_ranks:
            return
        self.similarity_active = False
        self.similarity_distances = {}
        self._similarity_ranks = {}
        self._refresh_filter()

    def set_confidence_range(self, min_val: float, max_val: float):
        min_val = min_val
        max_val = max_val
        if self.confidence_min == min_val and self.confidence_max == max_val:
            return
        self.confidence_min = min_val
        self.confidence_max = max_val
        self._refresh_filter()

    def _rebuild_similarity_window(self):
        if not self.similarity_active:
            self._similarity_ranks = {}
            return
        ranked = sorted(
            ((row, dist) for row, dist in self.similarity_distances.items() if row != self.similarity_anchor_row),
            key=lambda item: item[1],
        )
        if not ranked:
            self._similarity_ranks = {}
            return
        denom = max(1, len(ranked) - 1)
        self._similarity_ranks = {row: idx / denom for idx, (row, _dist) in enumerate(ranked)}

    def filterAcceptsRow(self, source_row: int, source_parent) -> bool:
        model = self.sourceModel()
        rec = None
        has_record = hasattr(model, "record")

        def record():
            nonlocal rec
            if rec is None and has_record:
                rec = model.record(source_row)
            return rec

        if self.matched_ids is not None:
            row_id = model.record_id(source_row) if hasattr(model, "record_id") else source_row
            if row_id not in self.matched_ids:
                return False
        if self.audio_types is not None:
            if has_record:
                rec = record()
                if rec and rec.audio_type not in self.audio_types: return False
        if not self.show_non_audio_assets:
            if has_record:
                rec = record()
                if rec and str(getattr(rec, "audio_type", "")) == "Non-Audio Assets":
                    return False
        if self._norm_path_filters:
            if has_record:
                rec = record()
                if rec:
                    if hasattr(model, "normalized_source_path"):
                        val = model.normalized_source_path(source_row)
                    else:
                        val = normalize_source_path_key(rec.source_path)
                    if not any(val.startswith(r) for r in self._norm_path_filters): return False
        if self.confidence_min > 0 or self.confidence_max < 1.0:
            if has_record:
                rec = record()
                if rec and not rec.is_manual and rec.confidence is not None:
                    try:
                        conf = float(rec.confidence)
                        if conf < self.confidence_min or conf > self.confidence_max: return False
                    except (ValueError, TypeError): pass
        if self.similarity_active:
            is_anchor = source_row == self.similarity_anchor_row
            rank = self._similarity_ranks.get(source_row)
            if not is_anchor and rank is None:
                return False
            if self.similarity_bias != 0 and not is_anchor and rank is not None:
                cutoff = max(0.0, 1.0 - abs(self.similarity_bias) / 100.0)
                if self.similarity_bias > 0:
                    if rank > cutoff: return False
                else:
                    if rank < (1.0 - cutoff): return False
        for col, allowed in self.column_filters.items():
            idx = model.index(source_row, col, source_parent)
            val = str(model.data(idx, Qt.DisplayRole))
            if val not in allowed: return False
        return True

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole):
        if orientation == Qt.Vertical and section >= 0:
            proxy_index = self.index(section, 0)
            if proxy_index.isValid():
                source_index = self.mapToSource(proxy_index)
                if source_index.isValid():
                    model = self.sourceModel()
                    if model is not None:
                        return model.headerData(source_index.row(), orientation, role)
        return super().headerData(section, orientation, role)

    def lessThan(self, left, right):
        if left.column() == StagingColumn.CONFIDENCE:
            left_data = self.sourceModel().data(left, Qt.EditRole)
            right_data = self.sourceModel().data(right, Qt.EditRole)
            try:
                return float(left_data) < float(right_data)
            except (ValueError, TypeError, AttributeError): pass
        return super().lessThan(left, right)
