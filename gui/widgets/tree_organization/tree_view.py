from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QPainter, QIcon
from PySide6.QtWidgets import QAbstractItemView, QApplication, QTreeView, QStyle, QStyledItemDelegate, QStyleOptionViewItem

from ...utils.styles import ColorPalette, make_qcolor, scaled_px
from .constants import ACTION_COLUMN, ACTION_KIND_ROLE, ADD_PARENT_ID_ROLE, NODE_ID_ROLE


class ActionColumnDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        opts = QStyleOptionViewItem(option)
        self.initStyleOption(opts, index)
        
        icon = opts.icon
        opts.icon = QIcon()  # type: ignore[assignment]
        
        
        super().paint(painter, opts, index)
        
        if icon and not icon.isNull():
            rect = option.rect
            icon_size = option.decorationSize
            
           
            padding = scaled_px(16)
            x = rect.right() - icon_size.width() - padding
            y = rect.top() + (rect.height() - icon_size.height()) // 2
            
            state = option.state
            mode = QIcon.Normal
            if not (state & QStyle.State_Enabled):
                mode = QIcon.Disabled
            elif state & QStyle.State_Selected:
                mode = QIcon.Selected
                
            icon.paint(painter, QRect(x, y, icon_size.width(), icon_size.height()), Qt.AlignCenter, mode)


class TreeOrganizationTreeView(QTreeView):
    addChildRequested = Signal(str)
    moveNodeRequested = Signal(str, str, int)
    focusDetailsRequested = Signal(str)
    deleteNodeRequested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._drag_node_id: str | None = None
        self._press_node_id: str | None = None
        self._press_pos: QPoint | None = None
        self._dragging_internal = False
        self._drop_target: tuple[str, int] | None = None
        self._forbidden_drop_parent_ids: set[str] | None = None
        self.canDropNode = None
        self.forbiddenDropParentsForNode = None
        self.itemForNode = None
        self.setAcceptDrops(False)
        self.setDragEnabled(False)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.NoDragDrop)
        self.setAutoExpandDelay(500)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setExpandsOnDoubleClick(False)
        self.doubleClicked.connect(self._on_double_clicked)
        self.setItemDelegateForColumn(ACTION_COLUMN, ActionColumnDelegate(self))

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            point = event.position().toPoint()
            index = self.indexAt(point)
            add_parent_id = self._add_parent_id(index.siblingAtColumn(0))
            if add_parent_id:
                QTimer.singleShot(0, lambda node_id=add_parent_id: self.addChildRequested.emit(node_id))
                event.accept()
                return
            if index.isValid() and index.column() == ACTION_COLUMN:
                node_id = self._node_id(index.siblingAtColumn(0))
                action_kind = index.data(ACTION_KIND_ROLE)
                if node_id and action_kind == "delete":
                    rect = self.visualRect(index)
                    if node_id != "root" and point.x() >= rect.left():
                        QTimer.singleShot(0, lambda node_id=node_id: self.deleteNodeRequested.emit(node_id))
                    event.accept()
                    return
            index = index.siblingAtColumn(0)
            self._press_node_id = self._node_id(index)
            self._press_pos = event.position().toPoint()
            self._dragging_internal = False
            self._drop_target = None
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._press_node_id and self._press_pos is not None and event.buttons() & Qt.LeftButton:
            point = event.position().toPoint()
            distance = (point - self._press_pos).manhattanLength()
            threshold = QApplication.startDragDistance()
            if self._dragging_internal or distance >= threshold:
                self._dragging_internal = True
                self._drag_node_id = self._press_node_id
                if self._forbidden_drop_parent_ids is None and self._drag_node_id:
                    if self.forbiddenDropParentsForNode is not None:
                        self._forbidden_drop_parent_ids = set(self.forbiddenDropParentsForNode(self._drag_node_id))
                    else:
                        self._forbidden_drop_parent_ids = {self._drag_node_id}
                self._set_drop_target(self._drop_plan(point))
                event.accept()
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._dragging_internal:
            if self._drag_node_id and self._drop_target is not None:
                parent_id, row = self._drop_target
                self.moveNodeRequested.emit(self._drag_node_id, parent_id, row)
            self._reset_internal_drag()
            event.accept()
            return
        self._reset_internal_drag()
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self._dragging_internal or self._drop_target is None:
            return
        target_rect = self._drop_indicator_rect(self._drop_target)
        if target_rect is None:
            return
        painter = QPainter(self.viewport())
        painter.setPen(make_qcolor(ColorPalette.PRIMARY_BRIGHT))
        if self._drop_target[1] < 0:
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(target_rect, scaled_px(4), scaled_px(4))
        else:
            painter.setBrush(make_qcolor(ColorPalette.PRIMARY_BRIGHT))
            painter.drawRoundedRect(target_rect, scaled_px(2), scaled_px(2))
        painter.end()

    def keyPressEvent(self, event):
        index = self.currentIndex().siblingAtColumn(0)
        if self._add_parent_id(index):
            if event.key() in {Qt.Key_Return, Qt.Key_Enter}:
                self.addChildRequested.emit(str(self._add_parent_id(index)))
                event.accept()
                return
            return super().keyPressEvent(event)
        node_id = self._node_id(index)
        if event.key() in {Qt.Key_Return, Qt.Key_Enter} and node_id:
            self.focusDetailsRequested.emit(node_id)
            event.accept()
            return
        if event.key() == Qt.Key_Delete and node_id:
            self.deleteNodeRequested.emit(node_id)
            event.accept()
            return
        super().keyPressEvent(event)

    def _on_double_clicked(self, index) -> None:
        node_id = self._node_id(index.siblingAtColumn(0))
        if node_id:
            self.focusDetailsRequested.emit(node_id)

    def _node_id(self, index) -> str | None:
        if not index.isValid():
            return None
        value = index.data(NODE_ID_ROLE)
        return str(value) if value else None

    def _add_parent_id(self, index) -> str | None:
        if not index.isValid():
            return None
        value = index.data(ADD_PARENT_ID_ROLE)
        return str(value) if value else None

    def _drop_plan(self, point: QPoint) -> tuple[str, int] | None:
        node_id = self._drag_node_id
        if not node_id:
            return None
        target = self.indexAt(point).siblingAtColumn(0)
        if not target.isValid():
            parent_id = "root"
            row = self._append_row_for_parent(parent_id)
            return (parent_id, row) if self._can_drop(node_id, parent_id) else None
        target_id = self._node_id(target)
        add_parent_id = self._add_parent_id(target)
        if add_parent_id:
            return (add_parent_id, -1) if self._can_drop(node_id, add_parent_id) else None
        if not target_id:
            return None
        rect = self.visualRect(target)
        band = max(scaled_px(5), min(scaled_px(10), rect.height() // 3))
        if point.y() < rect.top() + band:
            parent_id = self._node_id(target.parent().siblingAtColumn(0)) or "root"
            row = target.row()
        elif point.y() > rect.bottom() - band:
            parent_id = self._node_id(target.parent().siblingAtColumn(0)) or "root"
            row = target.row() + 1
        else:
            parent_id = target_id
            row = -1
        return (parent_id, row) if self._can_drop(node_id, parent_id) else None

    def _can_drop(self, node_id: str, parent_id: str) -> bool:
        if self._forbidden_drop_parent_ids is not None and parent_id in self._forbidden_drop_parent_ids:
            return False
        if self.canDropNode is None:
            return True
        return bool(self.canDropNode(node_id, parent_id))

    def _append_row_for_parent(self, parent_id: str) -> int:
        parent_item = self._item_for_node(parent_id)
        if parent_item is None:
            return 0
        count = 0
        for row in range(parent_item.rowCount()):
            child = parent_item.child(row, 0)
            if child is not None and child.data(ADD_PARENT_ID_ROLE):
                continue
            count += 1
        return count

    def _set_drop_target(self, target: tuple[str, int] | None) -> None:
        if target == self._drop_target:
            return
        old_rect = self._drop_indicator_rect(self._drop_target) if self._drop_target is not None else None
        self._drop_target = target
        new_rect = self._drop_indicator_rect(self._drop_target) if self._drop_target is not None else None
        dirty = old_rect
        if new_rect is not None:
            dirty = new_rect if dirty is None else dirty.united(new_rect)
        if dirty is None:
            self.viewport().update()
        else:
            self.viewport().update(dirty.adjusted(-scaled_px(6), -scaled_px(6), scaled_px(6), scaled_px(6)))

    def _reset_internal_drag(self) -> None:
        old_rect = self._drop_indicator_rect(self._drop_target) if self._drop_target is not None else None
        self._drag_node_id = None
        self._press_node_id = None
        self._press_pos = None
        self._dragging_internal = False
        self._drop_target = None
        self._forbidden_drop_parent_ids = None
        if old_rect is None:
            self.viewport().update()
        else:
            self.viewport().update(old_rect.adjusted(-scaled_px(6), -scaled_px(6), scaled_px(6), scaled_px(6)))

    def _drop_indicator_rect(self, plan: tuple[str, int]):
        parent_id, row = plan
        if row < 0:
            item = self._item_for_node(parent_id)
            if item is None:
                return None
            rect = self.visualRect(self.model().indexFromItem(item))
            return rect.adjusted(scaled_px(3), scaled_px(3), -scaled_px(3), -scaled_px(3))
        parent_item = self._item_for_node(parent_id)
        parent_index = self.rootIndex() if parent_item is None else self.model().indexFromItem(parent_item)
        child_count = self.model().rowCount(parent_index)
        if child_count <= 0:
            rect = self.visualRect(parent_index) if parent_index.isValid() else self.viewport().rect()
            y = rect.bottom() + scaled_px(4) if parent_index.isValid() else scaled_px(8)
        else:
            anchor_row = min(max(row, 0), child_count - 1)
            anchor = self.model().index(anchor_row, 0, parent_index)
            rect = self.visualRect(anchor)
            y = rect.top() if row <= anchor_row else rect.bottom()
        return QRect(scaled_px(12), y, max(scaled_px(80), self.viewport().width() - scaled_px(24)), scaled_px(3))

    def _item_for_node(self, node_id: str):
        if self.itemForNode is not None:
            return self.itemForNode(node_id)
        return None
