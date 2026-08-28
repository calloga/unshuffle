from PySide6.QtWidgets import QHeaderView, QTableView, QAbstractItemView, QStyle, QApplication
from PySide6.QtCore import Qt, QRect, QUrl, QMimeData, QPoint, Signal, QSortFilterProxyModel, QModelIndex, QItemSelectionModel
from PySide6.QtGui import QPainter, QColor, QBrush, QPen, QDrag, QPolygon
from gui.utils.constants import StagingColumn, DRAG_HANDLE_SIZE
from gui.utils.styles import ColorPalette, apply_style, make_qcolor, staging_table_view_style


class GroupedRowHeader(QHeaderView):
    """Vertical row header that paints sort-group color lanes in visible row order."""

    def __init__(self, parent=None):
        super().__init__(Qt.Vertical, parent)

    def paintSection(self, painter, rect, logicalIndex):
        if not rect.isValid():
            return
        model = self.model()
        color_source_model = model
        color_section = logicalIndex
        if isinstance(model, QSortFilterProxyModel):
            source_index = model.mapToSource(model.index(logicalIndex, 0))
            if source_index.isValid():
                color_source_model = model.sourceModel()
                color_section = source_index.row()
        color = (
            color_source_model.headerData(color_section, Qt.Vertical, Qt.BackgroundRole)
            if color_source_model is not None
            else None
        )
        if not isinstance(color, QColor):
            color = make_qcolor(ColorPalette.BG_HOVER)
        text = str(model.headerData(logicalIndex, Qt.Vertical, Qt.DisplayRole) or "") if model is not None else ""
        painter.save()
        painter.setBrush(color)
        painter.setPen(Qt.NoPen)
        painter.fillRect(rect, color)
        line = make_qcolor(ColorPalette.BORDER_LIGHT)
        line.setAlpha(10 if make_qcolor(ColorPalette.BG_LIST).lightness() < 120 else 14)
        painter.setPen(line)
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())
        line.setAlpha(10 if make_qcolor(ColorPalette.BG_LIST).lightness() < 120 else 14)
        painter.setPen(line)
        painter.drawLine(rect.topRight(), rect.bottomRight())
        painter.setPen(Qt.black)
        painter.drawText(rect.adjusted(2, 0, -2, 0), Qt.AlignCenter, text)
        painter.restore()


class OffsetSortHeader(QHeaderView):
    """Horizontal header with a lower, quieter custom sort indicator."""

    sectionResizeFinished = Signal(int, int, int)

    def __init__(self, parent=None):
        super().__init__(Qt.Horizontal, parent)
        self.setSortIndicatorShown(True)
        self._user_resize_section = -1
        self._user_resize_start = 0

    @property
    def user_resize_active(self) -> bool:
        return self._user_resize_section >= 0

    def mousePressEvent(self, event):
        self._user_resize_section = self._section_edge_at(int(event.position().x()))
        if self._user_resize_section >= 0:
            self._user_resize_start = self.sectionSize(self._user_resize_section)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        section = self._user_resize_section
        start_size = self._user_resize_start
        super().mouseReleaseEvent(event)
        self._user_resize_section = -1
        self._user_resize_start = 0
        if section >= 0:
            end_size = self.sectionSize(section)
            if end_size != start_size:
                self.sectionResizeFinished.emit(section, start_size, end_size)

    def _section_edge_at(self, x: int) -> int:
        tolerance = 5
        for logical in range(self.count()):
            if self.isSectionHidden(logical):
                continue
            edge = self.sectionViewportPosition(logical) + self.sectionSize(logical)
            if abs(x - edge) <= tolerance:
                return logical
        return -1

    def paintSection(self, painter, rect, logicalIndex):
        super().paintSection(painter, rect, logicalIndex)
        if not rect.isValid() or logicalIndex != self.sortIndicatorSection():
            return
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(make_qcolor(ColorPalette.TEXT_HEADER))
        cx = rect.center().x()
        top = rect.top() + 7
        if self.sortIndicatorOrder() == Qt.AscendingOrder:
            points = [(cx - 4, top + 5), (cx + 4, top + 5), (cx, top)]
        else:
            points = [(cx - 4, top), (cx + 4, top), (cx, top + 5)]
        painter.drawPolygon(QPolygon([QPoint(x, y) for x, y in points]))
        painter.restore()


class StagingTableView(QTableView):
    """
    Custom table view with drag‑fill, row‑resize, and tag‑pill support.
    
    Provides specialized interactions for the unshuffle staging table.
    """
    quickFilterRequested = Signal(object, str)
    focusSearchRequested = Signal()
    playRequested = Signal()
    resized = Signal()
    sortColumnRequested = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHorizontalHeader(OffsetSortHeader(self))
        self.setVerticalHeader(GroupedRowHeader(self))
        self.setMouseTracking(True)
        self.is_filling = False
        self.fill_start_idx: QModelIndex | None = None
        self.handle_size = DRAG_HANDLE_SIZE
        self.current_drag_row = -1
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setViewportMargins(0, 0, 0, 0)
        self.setShowGrid(False)
        self.setSelectionBehavior(QAbstractItemView.SelectItems)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.drag_start_pos = QPoint()
        
        self.setDragEnabled(False)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self._preserve_selection_for_drag = False
        self._drag_started = False
        self._selection_drag_anchor: QModelIndex | None = None
        self._fill_limits = {-1: -1, 1: -1}
        self._fill_blocked: dict[int, int] = {}
        self.setSortingEnabled(False)
        self.horizontalHeader().setSectionsClickable(True)
        self.horizontalHeader().setSectionsMovable(True)
        self.horizontalHeader().sectionDoubleClicked.connect(self.sortColumnRequested.emit)
        self.refresh_theme()

    def _get_handle_rect(self, index):
        """Calculate the rectangle for the drag-fill handle in the bottom-right of a cell."""
        rect = self.visualRect(index)
        return QRect(rect.right() - self.handle_size, rect.bottom() - self.handle_size, self.handle_size, self.handle_size)

    def _source_model(self):
        model = self.model()
        return model.sourceModel() if isinstance(model, QSortFilterProxyModel) else model

    @staticmethod
    def _fillable_columns():
        return {
            StagingColumn.PACK,
            StagingColumn.CATEGORY,
            StagingColumn.SUBCATEGORY,
            StagingColumn.TAGS,
            StagingColumn.TYPE,
        }

    def _to_source_index(self, index):
        model = self.model()
        if isinstance(model, QSortFilterProxyModel):
            return model.mapToSource(index)
        return index

    def keyPressEvent(self, event):
        """Handle keyboard shortcuts like Space for play preview."""
        if event.key() == Qt.Key_Space:
            self.playRequested.emit()
            event.accept()
            return
        if event.key() == Qt.Key_F and event.modifiers() & Qt.ControlModifier:
            if event.modifiers() & Qt.ShiftModifier:
                idx = self.currentIndex()
                if idx.isValid():
                    self.quickFilterRequested.emit(idx, self._compose_mode(event.modifiers()))
            else:
                self.focusSearchRequested.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def _compose_mode(self, modifiers):
        if modifiers & Qt.ShiftModifier:
            return "or"
        if modifiers & Qt.ControlModifier:
            return "and"
        return "replace"

    def startDrag(self, supportedActions):
        """Override startDrag to package PlanRecord paths as file URLs."""
        if not (QApplication.keyboardModifiers() & Qt.ControlModifier):
            return

        proxy = self.model()
        if proxy is None:
            return
        source_model = proxy.sourceModel()

        snapshot = getattr(self, "_ctrl_drag_proxy_rows", [])
        if snapshot:
            selected_proxy_rows = list(snapshot)
        else:
            selected_rows: set[int] = set()
            selection_model = self.selectionModel()
            if selection_model is not None:
                selected_rows.update(index.row() for index in selection_model.selection().indexes())
            if not selected_rows:
                selected_rows.update(index.row() for index in self.selectedIndexes())
            if not selected_rows:
                current = self.currentIndex()
                if current.isValid():
                    selected_rows.add(current.row())
            selected_proxy_rows = sorted(selected_rows)

        if not selected_proxy_rows:
            return
        rows = sorted({proxy.mapToSource(proxy.index(row, 0)).row() for row in selected_proxy_rows})

        urls = []
        for r in rows:
            if r < 0:
                continue
            record = source_model.record(r)
            urls.append(QUrl.fromLocalFile(str(record.source_path.absolute())))

        if not urls:
            return

        mime = QMimeData()
        mime.setUrls(urls)

        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.CopyAction | Qt.MoveAction, Qt.CopyAction)


    def mouseMoveEvent(self, event):
        """Handle drag-fill preview and cursor changes over handles."""
        if self.is_filling:
            if self.fill_start_idx is None:
                self.is_filling = False
                return
            pos = event.position().toPoint()
            curr_idx = self.indexAt(pos)
            if curr_idx.isValid() and curr_idx.column() == self.fill_start_idx.column():
                val = self.model().data(self.fill_start_idx, Qt.EditRole)
                self.current_drag_row = self._fill_target_row(curr_idx.row(), val)
            self.viewport().update()
            return
            
        pos = event.position().toPoint()
        idx = self.indexAt(pos)
        fillable_cols = self._fillable_columns()
        
        if idx.isValid() and idx.column() in fillable_cols:
            if self._get_handle_rect(idx).contains(pos):
                self.setCursor(Qt.CursorShape.CrossCursor)
                return
        self.setCursor(Qt.CursorShape.ArrowCursor)
        is_left_drag = bool(event.buttons() & Qt.LeftButton)
        is_ctrl_drag = bool(QApplication.keyboardModifiers() & Qt.ControlModifier)
        has_dragged = (event.position().toPoint() - self.drag_start_pos).manhattanLength() > QApplication.startDragDistance()
        if (
            not self.is_filling
            and is_left_drag
            and has_dragged
            and self._selection_drag_anchor is not None
            and self._selection_drag_anchor.isValid()
            and idx.isValid()
        ):
            if is_ctrl_drag and self._preserve_selection_for_drag:
                self._drag_started = True
                self.startDrag(Qt.CopyAction)
                self._preserve_selection_for_drag = False
                event.accept()
                return
            self._select_drag_range(self._selection_drag_anchor, idx, additive=is_ctrl_drag)
            event.accept()
            return

        super().mouseMoveEvent(event)

    def mousePressEvent(self, event):
        """Initiate drag-fill if clicking on a handle."""
        pos = event.position().toPoint()
        idx = self.indexAt(pos)
        self._drag_started = False
        fillable_cols = self._fillable_columns()
        
        if idx.isValid() and idx.column() in fillable_cols and self._get_handle_rect(idx).contains(pos):
            self.is_filling = True
            self.fill_start_idx = idx
            self.current_drag_row = idx.row()
            self._fill_limits = {-1: idx.row(), 1: idx.row()}
            self._fill_blocked.clear()
            self._selection_drag_anchor = None
            return
        
        self.drag_start_pos = pos
        self._selection_drag_anchor = idx if event.button() == Qt.LeftButton and idx.isValid() else None
        if (
            event.button() == Qt.LeftButton
            and idx.isValid()
            and bool(QApplication.keyboardModifiers() & Qt.ControlModifier)
        ):
          
            sel_model = self.selectionModel()
            if sel_model is not None:
                self._ctrl_drag_proxy_rows = sorted({i.row() for i in sel_model.selectedIndexes()})
            else:
                self._ctrl_drag_proxy_rows = []

            self._preserve_selection_for_drag = (
                sel_model is not None and sel_model.isSelected(idx)
            )
            if self._preserve_selection_for_drag:
                return
        else:
            self._ctrl_drag_proxy_rows = []
            self._preserve_selection_for_drag = False
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        """Execute drag-fill operation on release."""
        if self.is_filling:
            if self.fill_start_idx is None:
                self.is_filling = False
                return
            self.is_filling = False
            start_row = min(self.fill_start_idx.row(), self.current_drag_row)
            end_row = max(self.fill_start_idx.row(), self.current_drag_row)
            val = self.model().data(self.fill_start_idx, Qt.EditRole) or ""
            col = self.fill_start_idx.column()
            source_model = self._source_model()
            updates = []
            if source_model:
                source_rows = []
                for r in range(start_row, end_row + 1):
                    proxy_index = self.model().index(r, col)
                    if not proxy_index.isValid():
                        continue
                    source_index = self._to_source_index(proxy_index)
                    if not source_index.isValid():
                        continue
                    source_rows.append(source_index.row())
                if hasattr(source_model, "records_for_rows"):
                    records = source_model.records_for_rows(source_rows)
                else:
                    records = [
                        source_model.record(row)
                        for row in source_rows
                        if hasattr(source_model, "record")
                    ]
                updates.extend((rec, col, val) for rec in records if rec is not None)
                if hasattr(source_model, "apply_bulk_updates"):
                    source_model.apply_bulk_updates(updates, f"Fill {val}")
            self.viewport().update()
            self.setCursor(Qt.CursorShape.ArrowCursor)
            self._fill_blocked.clear()
            return
        self._preserve_selection_for_drag = False
        self._drag_started = False
        self._selection_drag_anchor = None
        super().mouseReleaseEvent(event)

    def _fill_target_row(self, requested_row: int, value) -> int:
        if self.fill_start_idx is None:
            return requested_row
        start_row = self.fill_start_idx.row()
        step = 1 if requested_row >= start_row else -1
        blocked_row = self._fill_blocked.get(step)
        if blocked_row is not None and (requested_row - blocked_row) * step >= 0:
            return blocked_row - step

        validated_row = self._fill_limits.get(step, start_row)
        if (requested_row - validated_row) * step <= 0:
            return requested_row

        for row in range(validated_row + step, requested_row + step, step):
            if row < 0 or row >= self.model().rowCount():
                break
            if not self._fill_value_is_valid(row, self.fill_start_idx.column(), value):
                self._fill_blocked[step] = row
                return row - step
            self._fill_limits[step] = row
        return self._fill_limits[step]

    def _fill_value_is_valid(self, proxy_row: int, column: int, value) -> bool:
        """Return whether a classification value is meaningful for one row."""
        proxy_index = self.model().index(proxy_row, column)
        source_index = self._to_source_index(proxy_index)
        source_model = self._source_model()
        if source_model is None or not source_index.isValid() or not hasattr(source_model, "record"):
            return True
        record = source_model.record(source_index.row())

        if column == StagingColumn.PACK:
            candidates = getattr(record, "pack_candidates", None)
            return not candidates or value in {
                candidate[0] if isinstance(candidate, (tuple, list)) else candidate
                for candidate in candidates
            }
        if column == StagingColumn.SUBCATEGORY:
            from unshuffle.core.constants import SUB_TAXONOMY_MAP

            category = str(getattr(record, "category", "") or "")
            valid = {
                candidate
                for candidate in SUB_TAXONOMY_MAP.get(category, {}).values()
                if candidate and candidate != "no-sub"
            }
            return not value or value in valid
        if column in {StagingColumn.CATEGORY, StagingColumn.TYPE}:
            from gui.widgets.library_filters import category_is_valid_for_audio_type

            category = str(value if column == StagingColumn.CATEGORY else getattr(record, "category", "") or "")
            audio_type = str(value if column == StagingColumn.TYPE else getattr(record, "audio_type", "") or "")
            return not category or not audio_type or category_is_valid_for_audio_type(category, audio_type)
        return True

    def _select_drag_range(self, start: QModelIndex, end: QModelIndex, *, additive: bool = False) -> None:
        selection_model = self.selectionModel()
        model = self.model()
        if selection_model is None or model is None:
            return
        top = min(start.row(), end.row())
        bottom = max(start.row(), end.row())
        left = min(start.column(), end.column())
        right = max(start.column(), end.column())
        if not additive:
            selection_model.clearSelection()
        for row in range(top, bottom + 1):
            for column in range(left, right + 1):
                if self.isColumnHidden(column):
                    continue
                index = model.index(row, column)
                if index.isValid():
                    selection_model.select(index, QItemSelectionModel.Select)
        selection_model.setCurrentIndex(end, QItemSelectionModel.NoUpdate)

    def paintEvent(self, event):
        """Render the drag-fill handle and preview rectangle."""
        super().paintEvent(event)
        if not self.selectionModel():
            return

        selection = self.selectionModel().currentIndex()
        fillable_cols = self._fillable_columns()

        painter = QPainter(self.viewport())
        try:
            if selection.isValid() and selection.column() in fillable_cols:
                handle = self._get_handle_rect(selection)
                painter.setBrush(QBrush(make_qcolor(ColorPalette.PRIMARY)))
                painter.setPen(QPen(Qt.white, 1))
                painter.drawRect(handle)

            if self.is_filling:
                if self.fill_start_idx is None:
                    return
                target_idx = self.model().index(self.current_drag_row, self.fill_start_idx.column())
                if target_idx.isValid():
                    rect = self.visualRect(self.fill_start_idx).united(self.visualRect(target_idx))
                    preview_color = make_qcolor(ColorPalette.PRIMARY)
                    preview_color.setAlpha(80)
                    painter.setBrush(QBrush(preview_color))
                    painter.setPen(QPen(Qt.white, 1, Qt.DashLine))
                    painter.drawRect(rect)
        finally:
            painter.end()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.resized.emit()

    def refresh_theme(self) -> None:
        apply_style(self, staging_table_view_style())
        self.viewport().update()
