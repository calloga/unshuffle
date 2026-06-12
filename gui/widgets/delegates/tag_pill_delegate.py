from PySide6.QtCore import Qt, QRect, QSize
from PySide6.QtGui import QColor, QFontMetrics, QPainter
from PySide6.QtWidgets import QLineEdit, QStyle, QStyledItemDelegate

from gui.utils.constants import StagingColumn
from gui.utils.styles import ColorPalette, make_qcolor, scaled_px
from .table_editors import mark_table_editor
from unshuffle.core.tags import parse_tags


class TagPillDelegate(QStyledItemDelegate):
    """Delegate that renders tags as display-only pills."""

    BASE_PILL_HEIGHT = 18
    BASE_SPACING_H = 6
    BASE_SPACING_V = 8
    BASE_PADDING_H = 8
    BASE_MARGIN = 8

    def __init__(self, parent=None):
        super().__init__(parent)

    @property
    def pill_height(self) -> int:
        return scaled_px(self.BASE_PILL_HEIGHT)

    @property
    def spacing_h(self) -> int:
        return scaled_px(self.BASE_SPACING_H)

    @property
    def spacing_v(self) -> int:
        return scaled_px(self.BASE_SPACING_V)

    @property
    def padding_h(self) -> int:
        return scaled_px(self.BASE_PADDING_H)

    @property
    def margin(self) -> int:
        return scaled_px(self.BASE_MARGIN)

    def _calculate_layout(self, tags, width, font_metrics, cell_height=None):
        """Calculate a single clipped row of tag pill rectangles within *width*."""
        layout = []
        margin_v = scaled_px(4)
        margin_h = self.margin
        x_start = margin_h
        curr_x = x_start
        curr_y = margin_v
        max_col_width = max(50, width - 12)
        inner_max_width = max_col_width - margin_h
        for tag in tags:
            text_width = font_metrics.horizontalAdvance(tag)
            pill_width = text_width + (self.padding_h * 2)
            if curr_x > x_start and curr_x + pill_width > max_col_width:
                break
            final_pill_width = min(pill_width, inner_max_width)
            rect = QRect(curr_x, curr_y, final_pill_width, self.pill_height)
            layout.append((tag, rect, pill_width > inner_max_width))
            curr_x += final_pill_width + self.spacing_h
        total_content_height = curr_y + self.pill_height + margin_v
        if cell_height and cell_height > total_content_height:
            offset_y = (cell_height - total_content_height) // 2
            return [(tag, rect.translated(0, offset_y), elided) for tag, rect, elided in layout], total_content_height
        return layout, total_content_height

    def _paint_row_separator(self, painter: QPainter, rect: QRect) -> None:
        line = make_qcolor(ColorPalette.BORDER_LIGHT)
        line.setAlpha(10 if make_qcolor(ColorPalette.BG_LIST).lightness() < 120 else 14)
        painter.setPen(line)
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())

    def _paint_column_separator(self, painter: QPainter, rect: QRect) -> None:
        line = make_qcolor(ColorPalette.BORDER_LIGHT)
        line.setAlpha(10 if make_qcolor(ColorPalette.BG_LIST).lightness() < 120 else 14)
        painter.setPen(line)
        painter.drawLine(rect.topRight(), rect.bottomRight())

    def _paint_cell_separators(self, painter: QPainter, rect: QRect) -> None:
        self._paint_row_separator(painter, rect)
        self._paint_column_separator(painter, rect)

    def paint(self, painter, option, index):
        self.initStyleOption(option, index)
        base_color = index.data(Qt.BackgroundRole)
        if isinstance(base_color, QColor):
            painter.fillRect(option.rect, base_color)
        else:
            painter.fillRect(option.rect, make_qcolor(ColorPalette.BG_LIST))

        if option.state & QStyle.State_Selected:
            painter.fillRect(option.rect, make_qcolor(ColorPalette.TABLE_SELECT))
        elif option.state & QStyle.State_MouseOver:
            painter.fillRect(option.rect, make_qcolor(ColorPalette.TABLE_HOVER))

        tags = index.data(Qt.DisplayRole)
        if not (isinstance(tags, list) and tags):
            painter.save()
            self._paint_cell_separators(painter, option.rect)
            painter.restore()
            return

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setClipRect(option.rect)
        metrics = QFontMetrics(option.font)
        layout, _ = self._calculate_layout(tags, option.rect.width(), metrics, cell_height=option.rect.height())
        for tag, pill_rect, is_elided in layout:
            draw_rect = pill_rect.translated(option.rect.topLeft())
            shade = self._tag_shade(tag)
            painter.setBrush(shade)
            painter.setPen(Qt.NoPen)
            radius = scaled_px(3)
            painter.drawRoundedRect(draw_rect, radius, radius)
            painter.setPen(Qt.white if shade.lightness() < 135 else make_qcolor(ColorPalette.TEXT_MAIN))
            text_rect = draw_rect.adjusted(self.padding_h, 0, -self.padding_h, 0)
            display_text = metrics.elidedText(tag, Qt.ElideRight, text_rect.width()) if is_elided else tag
            painter.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft, display_text)
        self._paint_cell_separators(painter, option.rect)
        if option.state & QStyle.State_HasFocus:
            painter.setPen(make_qcolor(ColorPalette.PRIMARY))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(option.rect.adjusted(0, 0, -1, -1))
        painter.restore()

    def _tag_shade(self, tag: str) -> QColor:
        base = sum(ord(c) for c in tag)
        if make_qcolor(ColorPalette.BG_LIST).lightness() < 120:
            level = 84 + (base % 34)
            return QColor(level, level + 6, level + 4, 220)
        level = 210 - (base % 28)
        return QColor(level, level + 2, level + 1, 230)

    def createEditor(self, parent, option, index):
        if index.column() == StagingColumn.TAGS:
            editor = QLineEdit(parent)
            mark_table_editor(editor)
            editor.editingFinished.connect(lambda: self.setModelData(editor, index.model(), index))
            return editor
        return super().createEditor(parent, option, index)

    def updateEditorGeometry(self, editor, option, index):
        editor.setGeometry(option.rect.adjusted(0, 0, -1, -1))

    def setEditorData(self, editor, index):
        if isinstance(editor, QLineEdit):
            tags = index.data(Qt.DisplayRole)
            editor.setText(", ".join(tags) if isinstance(tags, list) else "")
        else:
            super().setEditorData(editor, index)

    def setModelData(self, editor, model, index):
        if isinstance(editor, QLineEdit):
            model.setData(index, parse_tags(editor.text()), Qt.EditRole)
        else:
            super().setModelData(editor, model, index)

    def sizeHint(self, option, index):
        return QSize(option.rect.width() if option.rect.isValid() else scaled_px(100), scaled_px(34))
