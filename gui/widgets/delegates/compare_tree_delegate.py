from PySide6.QtWidgets import QStyleOptionViewItem, QStyledItemDelegate
from gui.utils.styles import scaled_px

class CompareTreeDelegate(QStyledItemDelegate):
    """
    Adds horizontal and vertical padding to tree items in comparison views.
    """
    def __init__(self, horizontal_padding=10, vertical_padding=2, parent=None):
        super().__init__(parent)
        self.horizontal_padding = int(horizontal_padding)
        self.vertical_padding = int(vertical_padding)

    def paint(self, painter, option, index):
        padded = QStyleOptionViewItem(option)
        hpad = scaled_px(self.horizontal_padding)
        vpad = scaled_px(self.vertical_padding)
        padded.rect = option.rect.adjusted(
            hpad,
            vpad,
            -hpad,
            -vpad,
        )
        super().paint(painter, padded, index)

    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        size.setWidth(size.width() + (scaled_px(self.horizontal_padding) * 2))
        size.setHeight(size.height() + (scaled_px(self.vertical_padding) * 2))
        return size
