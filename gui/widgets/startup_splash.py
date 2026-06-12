from __future__ import annotations

import math
import random

from PySide6.QtCore import QElapsedTimer, QPointF, QRect, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPen, QPixmap, QRadialGradient, QPainterPath
from PySide6.QtWidgets import QApplication, QLabel, QWidget

from ..utils.app_icon import APP_ICON_PATH, apply_app_icon
from ..utils.styles import scaled_px


class StartupSplash(QWidget):
    """Launch splash shown while the first library session is restored and warmed."""

    STATUS_BANNER_OFFSET_FROM_BOTTOM = 84
    STATUS_BANNER_HEIGHT = 28

    def __init__(self):
        super().__init__(None, Qt.SplashScreen | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        apply_app_icon(self)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFixedSize(scaled_px(356), scaled_px(316))
        self._status_text = "Starting Unshuffle..."
        self._icon_pixmap = QPixmap(str(APP_ICON_PATH)) if APP_ICON_PATH.exists() else QPixmap()
        
        self._clock = QElapsedTimer()
        self._clock.start()
        self._motion_timer = QTimer(self)
        self._motion_timer.setInterval(33)
        self._motion_timer.timeout.connect(self.update)
        self._motion_timer.start()

        self.status_label = QLabel(self._status_text, self)
        self.status_label.setObjectName("StartupSplashStatus")
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setWordWrap(True)
        self.status_label.setFixedHeight(scaled_px(28))
        self.status_label.setStyleSheet(
            "QLabel#StartupSplashStatus {"
            "background: qlineargradient(x1:0, y1:0, x2:1, y2:0, "
            "stop:0 rgba(144, 48, 252, 235), "
            "stop:0.52 rgba(36, 108, 252, 235), "
            "stop:1 rgba(36, 240, 252, 235));"
            "color: #06121f;"
            "border: none;"
            "border-radius: 0px;"
            f"padding: {scaled_px(3)}px {scaled_px(10)}px;"
            f"font-size: {scaled_px(10)}px;"
            "font-weight: 600;"
            "}"
        )

    def set_status(self, text: str) -> None:
        self._status_text = (text or "Starting Unshuffle...")
        self.status_label.setText(self._status_text)
        app = QApplication.instance()
        if app is not None:
            app.processEvents()

    def show_centered(self) -> None:
        screen = QApplication.primaryScreen()
        if screen is not None:
            geo = screen.availableGeometry()
            self.move(geo.center().x() - self.width() // 2, geo.center().y() - self.height() // 2)
        self.show()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.status_label.setGeometry(
            0,
            self._status_banner_top(),
            self.width(),
            scaled_px(self.STATUS_BANNER_HEIGHT),
        )

    def _status_banner_top(self) -> int:
        return self.height() - scaled_px(self.STATUS_BANNER_OFFSET_FROM_BOTTOM)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)
        
        bg = QLinearGradient(0, 0, self.width(), self.height())
        bg.setColorAt(0.0, QColor(9, 13, 22, 248))
        bg.setColorAt(0.55, QColor(14, 18, 28, 246))
        bg.setColorAt(1.0, QColor(8, 11, 18, 250))
        painter.setBrush(bg)
        painter.drawRoundedRect(self.rect(), 8, 8)

        elapsed = self._clock.elapsed() / 1000.0 if self._clock.isValid() else 0.0
        center_x = self.width() / 2.0
        center_y = 126.0

        breath = 1.0 + 0.08 * math.sin(elapsed * 2.5)
        glow_layers = (
            (QColor(144, 48, 252, 24), 160 * breath),
            (QColor(36, 108, 252, 20), 120 * breath),
            (QColor(36, 240, 252, 14), 80 * breath),
        )
        for color, radius in glow_layers:
            glow = QRadialGradient(QPointF(center_x, center_y), radius)
            glow.setColorAt(0.0, color)
            color_edge = QColor(color)
            color_edge.setAlpha(0)
            glow.setColorAt(1.0, color_edge)
            painter.setBrush(glow)
            painter.drawEllipse(QPointF(center_x, center_y), radius, radius)

  
        status_banner_top = self._status_banner_top()
        bar_count = 36
        bar_width = 4
        spacing = (self.width() - bar_width) / (bar_count - 1)
        painter.setPen(Qt.NoPen)
        for i in range(bar_count):
            
            wave1 = math.sin(elapsed * 1.8 + i * 0.4)
            wave2 = math.cos(elapsed * 2.6 - i * 0.25)
            height_pct = (wave1 * 0.5 + wave2 * 0.5 + 1.0) / 2.0  
            bar_h = 4 + int(height_pct * 22)
            
            x = i * spacing
            y = status_banner_top - bar_h
            
         
            col_ratio = i / float(bar_count - 1)
            bar_color = QColor()
            bar_color.setRedF(144 / 255.0 * (1.0 - col_ratio) + 36 / 255.0 * col_ratio)
            bar_color.setGreenF(48 / 255.0 * (1.0 - col_ratio) + 200 / 255.0 * col_ratio)
            bar_color.setBlueF(252 / 255.0)
            bar_color.setAlpha(45 + int(height_pct * 75))
            
            painter.setBrush(bar_color)
            painter.drawRoundedRect(QRect(int(x), y, bar_width, bar_h), 2, 2)

       
        wave_layers = (
            (QColor(36, 240, 252, 22), 0.02, 1.4, 0.0, 14.0),
            (QColor(144, 48, 252, 18), 0.025, 1.9, 2.1, 10.0),
            (QColor(36, 108, 252, 15), 0.015, 1.1, 4.3, 16.0),
        )
        base_wave_y = status_banner_top
        for color, freq, speed, phase_offset, amp in wave_layers:
            path = QPainterPath()
            path.moveTo(0, self.height())
            phase = elapsed * speed + phase_offset
            for px in range(0, self.width() + 4, 4):
               
                wave_val = (math.sin(px * freq + phase) * 0.5 + 0.5)
                py = base_wave_y - wave_val * amp
                path.lineTo(px, py)
            path.lineTo(self.width(), self.height())
            path.closeSubpath()
            painter.setBrush(color)
            painter.drawPath(path)


        if not self._icon_pixmap.isNull():
            target = QRect(int(center_x - 46), int(center_y - 46), 92, 92)
            pixmap = self._icon_pixmap.scaled(
                target.size(),
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
            pix_rect = QRect(
                target.x() + (target.width() - pixmap.width()) // 2,
                target.y() + (target.height() - pixmap.height()) // 2,
                pixmap.width(),
                pixmap.height(),
            )
            painter.drawPixmap(pix_rect, pixmap)


        title_font = QFont(self.font())
        title_font.setPointSize(27)
        title_font.setWeight(QFont.Weight.DemiBold)
        title_font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 112)
        painter.setFont(title_font)
        
        title_gradient = QLinearGradient(86, self.height() - 36, self.width() - 86, self.height() - 36)
        title_gradient.setColorAt(0.0, QColor("#f8fbfd"))
        title_gradient.setColorAt(0.55, QColor("#dce8ff"))
        title_gradient.setColorAt(1.0, QColor("#d7fdff"))
        painter.setPen(QPen(title_gradient, 1))
        painter.drawText(QRect(0, self.height() - 46, self.width(), 38), Qt.AlignCenter, "UNSHUFFLE")

        painter.setPen(QPen(QColor(255, 255, 255, 30), 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(self.rect().adjusted(1, 1, -2, -2), 8, 8)
        super().paintEvent(event)
