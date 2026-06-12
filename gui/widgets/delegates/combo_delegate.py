from gui.utils.styles import ColorPalette, apply_style, combo_style, make_qcolor
from PySide6.QtWidgets import QComboBox, QLineEdit, QListView, QStyledItemDelegate, QStyle
from PySide6.QtCore import Qt, QRect, QTimer
from PySide6.QtGui import QColor, QPainter
from unshuffle.core.constants import CATEGORIES, SUB_TAXONOMY_MAP
from gui.utils.constants import LIB_TAB_COLUMN_MIN_WIDTH, StagingColumn
from gui.styles import CATEGORY_IDENTITY_MAP
from gui.utils.styles import identity_lane_color, scaled_px
from .table_editors import mark_table_editor


def _paint_row_separator(painter: QPainter, rect: QRect) -> None:
    line = make_qcolor(ColorPalette.BORDER_LIGHT)
    line.setAlpha(10 if make_qcolor(ColorPalette.BG_LIST).lightness() < 120 else 14)
    painter.setPen(line)
    painter.drawLine(rect.bottomLeft(), rect.bottomRight())


def _paint_column_separator(painter: QPainter, rect: QRect) -> None:
    line = make_qcolor(ColorPalette.BORDER_LIGHT)
    line.setAlpha(10 if make_qcolor(ColorPalette.BG_LIST).lightness() < 120 else 14)
    painter.setPen(line)
    painter.drawLine(rect.topRight(), rect.bottomRight())


def _paint_cell_separators(painter: QPainter, rect: QRect) -> None:
    _paint_row_separator(painter, rect)
    _paint_column_separator(painter, rect)


class ComboDelegate(QStyledItemDelegate):
    """
    Delegate providing dropdown editors for specific columns.
    
    * Column PACK – Pack (editable, shows candidate packs)
    * Column CATEGORY – Category-only picker
    * Column SUBCATEGORY – Sub-category picker scoped to the row category
    """

    def _is_selected(self, option, index) -> bool:
        if option.state & QStyle.State_Selected:
            return True
        view = self.parent()
        selection_model = getattr(view, "selectionModel", lambda: None)()
        return bool(selection_model and selection_model.isSelected(index))

    def createEditor(self, parent, option, index):
        col = index.column()
        if col in [StagingColumn.PACK, StagingColumn.CATEGORY, StagingColumn.SUBCATEGORY]:
            cb = QComboBox(parent)
            cb.setView(QListView())
            cb.setEditable(col == StagingColumn.PACK)
            apply_style(cb, combo_style())
            mark_table_editor(cb)
            
            if col == StagingColumn.PACK:
                candidates = index.data(Qt.UserRole)
                if candidates:
                    for c in candidates:
                        cb.addItem(f"{c[0]} ({int(c[1]*100)}%)", c[0])
                else:
                    cb.addItem(index.data(Qt.DisplayRole), index.data(Qt.DisplayRole))
            elif col == StagingColumn.CATEGORY:
                for cat in CATEGORIES:
                    cb.addItem(cat, cat)
            elif col == StagingColumn.SUBCATEGORY:
                category = str(index.siblingAtColumn(StagingColumn.CATEGORY).data(Qt.DisplayRole) or "").strip()
                cb.addItem("", "")
                if category in SUB_TAXONOMY_MAP:
                    subs = sorted(
                        {
                            sub
                            for sub in SUB_TAXONOMY_MAP[category].values()
                            if sub and sub != "no-sub"
                        }
                    )
                    for sub in subs:
                        cb.addItem(sub, sub)
            QTimer.singleShot(0, cb.showPopup)
            return cb
        editor = super().createEditor(parent, option, index)
        if isinstance(editor, QLineEdit):
            mark_table_editor(editor)
        return editor

    def updateEditorGeometry(self, editor, option, index):
        editor.setGeometry(option.rect.adjusted(0, 0, -1, -1))

    def setEditorData(self, editor, index):
        if isinstance(editor, QComboBox):
            val = index.data(Qt.EditRole)
            col = index.column()
            
            idx = -1
            if col == StagingColumn.PACK:
                idx = editor.findData(val)
            else:
                idx = editor.findData(val)
                if idx < 0:
                    idx = editor.findText(str(val))
            
            if idx >= 0:
                editor.setCurrentIndex(idx)
            else:
                editor.setCurrentText(str(val))
        else:
            super().setEditorData(editor, index)

    def setModelData(self, editor, model, index):
        if isinstance(editor, QComboBox):
            col = index.column()
            if col == StagingColumn.PACK:
                text = editor.currentText().strip()
                data = editor.currentData()
                selected_label = editor.itemText(editor.currentIndex()).strip() if editor.currentIndex() >= 0 else ""
                if data is not None and text == selected_label:
                    val = data
                else:
                    val = text or data
            else:
                val = editor.currentData() if editor.currentData() is not None else editor.currentText()
            model.setData(index, val, Qt.EditRole)
        else:
            super().setModelData(editor, model, index)

    def paint(self, painter, option, index):
        self.initStyleOption(option, index)
        base_color = index.data(Qt.BackgroundRole)
        if isinstance(base_color, QColor):
            painter.fillRect(option.rect, base_color)
        else:
            painter.fillRect(option.rect, make_qcolor(ColorPalette.BG_LIST))

        is_selected = self._is_selected(option, index)
        is_hovered = bool(option.state & QStyle.State_MouseOver)
        if is_selected:
            painter.fillRect(option.rect, make_qcolor(ColorPalette.TABLE_SELECT))
        elif is_hovered:
            painter.fillRect(option.rect, make_qcolor(ColorPalette.TABLE_HOVER))

        col = index.column()
        if col in (StagingColumn.CATEGORY, StagingColumn.SUBCATEGORY):
            self._paint_taxonomy_pill(painter, option, str(index.data(Qt.DisplayRole) or ""), index)
            painter.save()
            _paint_cell_separators(painter, option.rect)
            painter.restore()
            return
        if col == StagingColumn.CONFIDENCE:
            self._paint_confidence_bar(painter, option, index)
            painter.save()
            _paint_cell_separators(painter, option.rect)
            painter.restore()
            return

        painter.save()
        if is_selected:
            painter.setPen(make_qcolor(ColorPalette.TEXT_MAIN))
        else:
            painter.setPen(make_qcolor(ColorPalette.TEXT_LIGHT))

        text_rect = option.rect.adjusted(ColorPalette.TABLE_MARGIN, 0, -ColorPalette.TABLE_MARGIN, 0)
        text = option.fontMetrics.elidedText(option.text, Qt.ElideRight, max(1, text_rect.width()))
        painter.drawText(text_rect, option.displayAlignment, text)

        if option.state & QStyle.State_HasFocus:
            focus_pen = make_qcolor(ColorPalette.PRIMARY)
            painter.setPen(focus_pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(option.rect.adjusted(0, 0, -1, -1))
        _paint_cell_separators(painter, option.rect)
        painter.restore()

    def _lane_for_category(self, category: str) -> int | None:
        role = CATEGORY_IDENTITY_MAP.get(category, "identity.neutral")
        if role == "identity.neutral":
            return None
        try:
            return max(0, int(role.rsplit(".", 1)[1]) - 1)
        except (IndexError, ValueError):
            return None

    def _pill_fill(self, lane: int | None, *, soft_variant: bool = False) -> QColor:
        if lane is None:
            fill = make_qcolor(ColorPalette.IDENTITY_SOFT_NEUTRAL)
        else:
            fill = make_qcolor(identity_lane_color(lane, soft=True))
        if make_qcolor(ColorPalette.BG_LIST).lightness() < 120:
            if lane is None:
                fill = make_qcolor(ColorPalette.IDENTITY_NEUTRAL)
            else:
                fill = make_qcolor(ColorPalette.IDENTITY[lane % len(ColorPalette.IDENTITY)])
            fill.setAlpha(30 if soft_variant else 122)
        elif soft_variant:
            fill.setAlpha(86)
        return fill

    def _paint_taxonomy_pill(self, painter: QPainter, option, text: str, index=None) -> None:
        if not text.strip():
            return
        category = text
        is_subcategory = index is not None and index.column() == StagingColumn.SUBCATEGORY
        if is_subcategory:
            category = str(index.siblingAtColumn(StagingColumn.CATEGORY).data(Qt.DisplayRole) or "")
        lane = self._lane_for_category(category)
        rect = QRect(option.rect)
        target_width = scaled_px((LIB_TAB_COLUMN_MIN_WIDTH - 28) if is_subcategory else (LIB_TAB_COLUMN_MIN_WIDTH - 8))
        rect.setWidth(min(target_width, max(1, option.rect.width() - scaled_px(14))))
        rect.setHeight(min(scaled_px(20), max(1, option.rect.height() - scaled_px(8))))
        rect.moveLeft(option.rect.left() + max(0, (option.rect.width() - rect.width()) // 2))
        rect.moveTop(option.rect.top() + max(0, (option.rect.height() - rect.height()) // 2))
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(self._pill_fill(lane, soft_variant=is_subcategory))
        painter.drawRoundedRect(rect, scaled_px(3), scaled_px(3))
        painter.setPen(make_qcolor(ColorPalette.TEXT_MUTED if is_subcategory else ColorPalette.TEXT_MAIN))
        painter.drawText(rect.adjusted(scaled_px(8), 0, -scaled_px(8), 0), Qt.AlignCenter, option.fontMetrics.elidedText(text, Qt.ElideRight, rect.width() - scaled_px(12)))
        painter.restore()

    def _paint_confidence_bar(self, painter: QPainter, option, index) -> None:
        value = index.data(Qt.UserRole)
        if value is None:
            value = index.data(Qt.EditRole)
        try:
            pct = max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            display = str(index.data(Qt.DisplayRole) or "").strip().rstrip("%")
            try:
                pct = max(0.0, min(1.0, float(display) / 100.0))
            except (TypeError, ValueError):
                pct = 0.0
        track = QRect(0, 0, min(scaled_px(92), max(1, option.rect.width() - scaled_px(44))), scaled_px(4))
        track.moveCenter(option.rect.center())
        fill = QRect(track)
        fill.setWidth(int(track.width() * pct))
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(make_qcolor(ColorPalette.BG_MED))
        painter.drawRoundedRect(track, scaled_px(2), scaled_px(2))
        painter.setBrush(make_qcolor(ColorPalette.PRIMARY))
        painter.drawRoundedRect(fill, scaled_px(2), scaled_px(2))
        painter.restore()
