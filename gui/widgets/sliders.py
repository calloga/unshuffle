from PySide6.QtWidgets import QWidget, QSizePolicy
from PySide6.QtCore import Qt, QSize, QPoint, QRectF, Signal
from PySide6.QtGui import QPainter, QColor, QPen, QBrush, QLinearGradient
from gui.utils.constants import MODERN_KNOB_SIZE, RANGE_SLIDER_MIN_HEIGHT
from gui.utils.styles import ColorPalette, make_qcolor
from gui.utils.widget_helpers import apply_fixed_size, apply_minimum_height

class ModernKnob(QWidget):
    """
    Sleek, circular knob control with support for symmetric (mid-zero) 
    and linear ranges. Used for 'Vibe' exploration and parameter tuning.
    """
    valueChanged = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        apply_fixed_size(self, MODERN_KNOB_SIZE, MODERN_KNOB_SIZE)
        self._min = -100
        self._max = 100
        self._value = 0
        self._last_pos = None
        self._fine_tune_factor = 1.0
        self._symmetric = True
        
    def setSymmetric(self, s):
        self._symmetric = s
        self.update()

    def value(self):
        return self._value

    def setValue(self, val):
        new_val = max(self._min, min(self._max, val))
        if new_val != self._value:
            self._value = new_val
            self.update()
            self.valueChanged.emit(self._value)

    def setRange(self, min_val, max_val):
        self._min = min_val
        self._max = max_val
        self.setValue(self._value)

    def wheelEvent(self, event):
        delta = event.angleDelta().y() / 120.0
        self.setValue(self._value + (delta * 15))
        event.accept()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._last_pos = event.position()

    def mouseMoveEvent(self, event):
        if self._last_pos is not None:
            delta_y = self._last_pos.y() - event.position().y()
            mod = 0.5 if event.modifiers() & Qt.ShiftModifier else 1.0
            change = delta_y * self._fine_tune_factor * mod
            new_val = max(self._min, min(self._max, self._value + change))
            
            if self._symmetric and abs(new_val) < 2 and self._value != 0:
                new_val = 0
                
            if new_val != self._value:
                self._value = new_val
                self.update()
                self.valueChanged.emit(self._value)
            self._last_pos = event.position()

    def mouseReleaseEvent(self, event):
        self._last_pos = None

    def mouseDoubleClickEvent(self, event):
        reset_val = 0 if self._symmetric else self._min
        self.setValue(reset_val)

    def paintEvent(self, event):
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.Antialiasing)

            width = self.width()
            height = self.height()
            side = min(width, height) - 8
            painter.translate(width / 2, height / 2)

            painter.setPen(Qt.NoPen)
            painter.setBrush(make_qcolor(ColorPalette.BG_DARKER))
            painter.drawEllipse(QRectF(-side/2, -side/2, side, side))

            painter.setPen(QPen(make_qcolor(ColorPalette.BORDER), 1.5))
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(QRectF(-side/2 + 1, -side/2 + 1, side - 2, side - 2))

            val = self._value

            if self._symmetric:
                angle_span = 135 * (abs(val) / 100.0)
                painter.setPen(QPen(make_qcolor(ColorPalette.PRIMARY_LIGHT), 3.5, Qt.SolidLine, Qt.RoundCap))
                if val > 0:
                    painter.drawArc(QRectF(-side/2 + 4, -side/2 + 4, side - 8, side - 8), 90 * 16, int(-angle_span * 16))
                elif val < 0:
                    painter.drawArc(QRectF(-side/2 + 4, -side/2 + 4, side - 8, side - 8), 90 * 16, int(angle_span * 16))

                painter.setBrush(make_qcolor(ColorPalette.BORDER_INPUT))
                painter.setPen(Qt.NoPen)
                painter.drawEllipse(QPoint(0, int(-side / 2 - 2)), 2, 2)

                painter.rotate(135 * (val / 100.0))
            else:
                range_span = self._max - self._min
                if range_span == 0: range_span = 1
                percent = (val - self._min) / range_span

                draw_span = 270 * percent
                painter.setPen(QPen(make_qcolor(ColorPalette.PRIMARY_LIGHT), 3.5, Qt.SolidLine, Qt.RoundCap))
                painter.drawArc(QRectF(-side/2 + 4, -side/2 + 4, side - 8, side - 8), 225 * 16, int(-draw_span * 16))

                painter.rotate(-135 + (270 * percent))

            painter.setPen(QPen(make_qcolor(ColorPalette.TEXT_MAIN), 2, Qt.SolidLine, Qt.RoundCap))
            painter.drawLine(0, int(-side / 2 + 4), 0, int(-side / 2 + 10))
        finally:
            painter.end()


class ModernRangeSlider(QWidget):
    """
    Dual-handle horizontal slider for range selection (e.g. confidence thresholds).
    Values are normalized 0-100.
    """
    valuesChanged = Signal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.min_val = 0
        self.max_val = 100
        self.handle_radius = 6
        self.track_height = 4
        self.active_handle = None 
        apply_minimum_height(self, RANGE_SLIDER_MIN_HEIGHT)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setCursor(Qt.PointingHandCursor)

    def _val_to_pos(self, val):
        width = self.width() - 2 * self.handle_radius
        return self.handle_radius + (val / 100.0) * width

    def _pos_to_val(self, pos):
        width = self.width() - 2 * self.handle_radius
        if width <= 0: return 0
        val = ((pos - self.handle_radius) / width) * 100
        return max(0, min(100, int(round(val))))

    def paintEvent(self, event):
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.Antialiasing)

            w = self.width()
            h = self.height()
            cy = h // 2

            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(make_qcolor(ColorPalette.BORDER)))
            painter.drawRoundedRect(self.handle_radius, cy - self.track_height // 2, w - 2 * self.handle_radius, self.track_height, 2, 2)

            x_min = self._val_to_pos(self.min_val)
            x_max = self._val_to_pos(self.max_val)

            gradient = QLinearGradient(x_min, 0, x_max, 0)
            gradient.setColorAt(0, make_qcolor(ColorPalette.PRIMARY))
            gradient.setColorAt(1, make_qcolor(ColorPalette.PRIMARY_LIGHT))

            painter.setBrush(QBrush(gradient))
            painter.drawRoundedRect(int(x_min), cy - self.track_height // 2, int(x_max - x_min), self.track_height, 2, 2)

            for val in (self.min_val, self.max_val):
                x = self._val_to_pos(val)
                painter.setBrush(QBrush(make_qcolor(ColorPalette.BORDER_LIGHT)))
                painter.drawEllipse(QPoint(int(x), cy), self.handle_radius + 1, self.handle_radius + 1)
                painter.setBrush(QBrush(make_qcolor(ColorPalette.TEXT_HEADER)))
                painter.drawEllipse(QPoint(int(x), cy), self.handle_radius - 1, self.handle_radius - 1)
        finally:
            painter.end()

    def mousePressEvent(self, event):
        pos = event.position().x()
        dist_min = abs(pos - self._val_to_pos(self.min_val))
        dist_max = abs(pos - self._val_to_pos(self.max_val))

        if dist_min < dist_max and dist_min < 20:
            self.active_handle = 'min'
        elif dist_max < 20:
            self.active_handle = 'max'
        else:
            self.active_handle = None
        
        if self.active_handle:
            self._update_val(pos)

    def mouseMoveEvent(self, event):
        if self.active_handle:
            self._update_val(event.position().x())

    def mouseReleaseEvent(self, event):
        self.active_handle = None

    def _update_val(self, pos):
        val = self._pos_to_val(pos)
        if self.active_handle == 'min':
            if val < self.max_val:
                self.min_val = val
            else:
                self.min_val = self.max_val - 1
        elif self.active_handle == 'max':
            if val > self.min_val:
                self.max_val = val
            else:
                self.max_val = self.min_val + 1
        
        self.update()
        self.valuesChanged.emit(self.min_val, self.max_val)

    def setValues(self, min_val, max_val):
        self.min_val = max(0, min(100, min_val))
        self.max_val = max(0, min(100, max_val))
        self.update()

    def value(self):
        return self.min_val
