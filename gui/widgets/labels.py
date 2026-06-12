from PySide6.QtWidgets import QLabel
from PySide6.QtCore import QSize
from ..utils.styles import apply_style, section_label_style

def section_label(text):
    """Returns a styled QLabel for sidebar section headers."""
    label = QLabel(text.upper())
    label.setProperty("sectionLabel", True)
    apply_style(label, section_label_style())
    return label

class ElidingLabel(QLabel):
    """A QLabel that elides overflow text with an ellipsis.
    
    The widget reports a minimal width to stay compact in layouts.
    """
    def sizeHint(self):
        s = super().sizeHint()
        return QSize(10, s.height())

    def minimumSizeHint(self):
        s = super().minimumSizeHint()
        return QSize(10, s.height())

    def paintEvent(self, event):
        from PySide6.QtGui import QFontMetrics, QPainter, QPalette
        from PySide6.QtCore import Qt
        metrics = QFontMetrics(self.font())
        elided = metrics.elidedText(self.text(), Qt.ElideRight, self.width())
        painter = QPainter(self)
        try:
            self.style().drawItemText(
                painter,
                self.rect(),
                self.alignment(),
                self.palette(),
                self.isEnabled(),
                elided,
                QPalette.WindowText,
            )
        finally:
            painter.end()
