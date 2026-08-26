from pathlib import Path
from typing import Callable, Optional

from PySide6.QtWidgets import QPushButton
from PySide6.QtCore import Property, QPropertyAnimation, QEasingCurve, QSize, Qt
from PySide6.QtGui import QPainter, QColor, QPixmap, QBrush
from unshuffle.core.assets import asset_path
from ..utils.styles import ColorPalette, make_qcolor, scaled_px
from ..utils.widget_helpers import apply_fixed_size_q

def _resolve_icon_path(icon_path) -> str:
    if not icon_path:
        return ""
    path = Path(icon_path)
    if path.is_absolute():
        return str(path)
    return str(asset_path(*str(icon_path).replace("\\", "/").split("/")))


def sidebar_icon_default_color() -> QColor:
    return make_qcolor(ColorPalette.TEXT_LIGHT)


def sidebar_icon_active_color() -> QColor:
    return make_qcolor(ColorPalette.PRIMARY)


def sidebar_icon_hover_color() -> QColor:
    return make_qcolor(ColorPalette.TABLE_HOVER)


def sidebar_icon_text_active() -> QColor:
    return make_qcolor(ColorPalette.TEXT_INVERSE)


def sidebar_icon_text_inactive() -> QColor:
    return make_qcolor(ColorPalette.TEXT_MUTED)

class AnimatedIconButton(QPushButton):
    """
    Standard icon button with lift and opacity animations on hover.
    Optimized to reuse animation objects and cache rendered icons.
    """
    HOVER_DURATION = 150
    RESET_DURATION = 100
    LIFT_OFFSET = -2
    ENABLED_OPACITY = 1.0
    DISABLED_OPACITY = 0.5
    MAX_CACHE_SIZE = 100
    _shared_icon_cache = {}

    def __init__(self, icon_path, icon_size=QSize(24, 24), parent=None, color=None):
        super().__init__(parent)
        self.icon_path = _resolve_icon_path(icon_path)
        self.icon_size = icon_size
        self._uses_theme_color = color is None
        self.color = color or make_qcolor(ColorPalette.TEXT_GRAY)
        self._color_transform: Callable[[QColor], QColor] | None = None
        
        apply_fixed_size_q(self, icon_size + QSize(8, 8))
        self.setCursor(Qt.PointingHandCursor)
        self.setFlat(True)
        self.hover_offset = 0
        
        self.anim = QPropertyAnimation(self, b"hoverOffset")
        self.anim.setEasingCurve(QEasingCurve.OutQuad)
        

        self.scale = 1.0
        self.alpha_mult = self.ENABLED_OPACITY if self.isEnabled() else self.DISABLED_OPACITY

    def getHoverOffset(self) -> int:
        return self.hover_offset

    def setHoverOffset(self, val: int) -> None:
        self.hover_offset = val
        self.update()

    hoverOffset = Property(int, getHoverOffset, setHoverOffset)

    def enterEvent(self, event):
        self.anim.stop()
        self.anim.setDuration(self.HOVER_DURATION)
        self.anim.setEndValue(self.LIFT_OFFSET)
        self.anim.start()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.anim.stop()
        self.anim.setDuration(self.RESET_DURATION)
        self.anim.setEndValue(0)
        self.anim.start()
        super().leaveEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.Antialiasing)

            icon_pixmap = self._get_cached_icon()
            if not icon_pixmap:
                return

            painter.setOpacity(self.alpha_mult)

            target_rect = self.rect()
            x = (target_rect.width() - self.icon_size.width()) / 2
            y = (target_rect.height() - self.icon_size.height()) / 2 + self.hover_offset

            painter.drawPixmap(int(x), int(y), icon_pixmap)
        finally:
            painter.end()

    def _get_cached_icon(self) -> Optional[QPixmap]:
        """Returns a colorized version of the icon from cache or creates it."""
        has_dynamic_icon = not self.icon().isNull()
        icon_key = self.icon().cacheKey() if has_dynamic_icon else self.icon_path

        paint_color = self._paint_color(self.color)
        cache_key = (
            icon_key,
            self.icon_size.width(),
            self.icon_size.height(),
            paint_color.name(QColor.HexArgb) if paint_color else "default",
        )
        if cache_key in self._shared_icon_cache:
            return self._shared_icon_cache[cache_key]
        
        if len(self._shared_icon_cache) > self.MAX_CACHE_SIZE:
            self._shared_icon_cache.clear()

        if has_dynamic_icon:
            pixmap = self.icon().pixmap(self.icon_size)
        else:
            pixmap = QPixmap(self.icon_path)

        if pixmap.isNull():
            return None
            
        pixmap = pixmap.scaled(self.icon_size, Qt.KeepAspectRatio, Qt.SmoothTransformation).copy()
        
        if paint_color:
            image = pixmap.toImage()
            painter = QPainter(image)
            try:
                painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
                painter.fillRect(image.rect(), paint_color)
            finally:
                painter.end()
            pixmap = QPixmap.fromImage(image)
            
        self._shared_icon_cache[cache_key] = pixmap
        return pixmap

    def _paint_color(self, color: QColor) -> QColor:
        transform = self._color_transform
        return transform(color) if callable(transform) else QColor(color)

    def set_color_transform(self, transform: Callable[[QColor], QColor] | None) -> None:
        self._color_transform = transform
        self.update()

    def changeEvent(self, event):
        if event.type() == event.Type.EnabledChange:
            self.alpha_mult = self.ENABLED_OPACITY if self.isEnabled() else self.DISABLED_OPACITY
            self.update()
        super().changeEvent(event)

    def refresh_theme(self) -> None:
        if self._uses_theme_color:
            self.color = make_qcolor(ColorPalette.TEXT_GRAY)
        self.update()


class SidebarIconButton(AnimatedIconButton):
    """
    Styled version of AnimatedIconButton specifically for sidebar toggles.
    Supports checked states and text-only labels.
    """
    OVERLAY_RADIUS = 3
    DEFAULT_TEXT_POINTSIZE = 10
    SHORT_TEXT_POINTSIZE = 11

    def __init__(self, icon_path=None, icon_size=QSize(16, 16), button_size=QSize(24, 24), text="", parent=None, checkable=True):
        super().__init__(icon_path or "", icon_size, parent, sidebar_icon_default_color())
        apply_fixed_size_q(self, button_size)
        self.setIconSize(icon_size)
        self.setCheckable(checkable)
        self.text_label = text
        self._overlay = make_qcolor(ColorPalette.TEXT_LIGHT)
        self._overlay.setAlpha(0)
        self._active = sidebar_icon_active_color()
        self._adaptive_active: QColor | None = None
        self._adaptive_hover: QColor | None = None
        self._adaptive_checked_icon: QColor | None = None
        self._adaptive_unchecked_icon: QColor | None = None

    def set_adaptive_colors(
        self,
        active: str | QColor | None,
        hover: str | QColor | None,
        checked_icon: str | QColor | None = None,
        unchecked_icon: str | QColor | None = None,
    ) -> None:
        self._adaptive_active = QColor(active) if active is not None else None
        self._adaptive_hover = QColor(hover) if hover is not None else None
        self._adaptive_checked_icon = QColor(checked_icon) if checked_icon is not None else None
        self._adaptive_unchecked_icon = QColor(unchecked_icon) if unchecked_icon is not None else None
        self.update()

    def paintEvent(self, event):
        paint_icon = False
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.Antialiasing)
            radius = self.OVERLAY_RADIUS
            rect = self.rect()

            if self.isChecked():
                active = self._adaptive_active or self._paint_color(self._active)
                painter.setBrush(QBrush(active))
                painter.setPen(Qt.NoPen)
                painter.drawRoundedRect(rect, radius, radius)
            elif self.underMouse():
                hover = self._adaptive_hover or self._paint_color(sidebar_icon_hover_color())
                painter.setBrush(QBrush(hover))
                painter.setPen(Qt.NoPen)
                painter.drawRoundedRect(rect, radius, radius)
            elif self._overlay.alpha() > 0:
                painter.setBrush(QBrush(self._paint_color(self._overlay)))
                painter.setPen(Qt.NoPen)
                painter.drawRoundedRect(rect, radius, radius)

            if self.icon() and not self.icon().isNull() or self.icon_path:
                if self.isChecked() and self._adaptive_checked_icon is not None:
                    target_color = self._adaptive_checked_icon
                elif not self.isChecked() and self._adaptive_unchecked_icon is not None:
                    target_color = self._adaptive_unchecked_icon
                else:
                    target_color = sidebar_icon_text_active() if self.isChecked() else sidebar_icon_default_color()
                if self.color != target_color:
                    self.color = target_color
                paint_icon = True
            elif self.text_label:
                painter.save()
                painter.translate(self.width() / 2, self.height() / 2 + self.hover_offset)
                painter.scale(self.scale, self.scale)
                font = painter.font()
                point_size = scaled_px(
                    self.SHORT_TEXT_POINTSIZE if len(self.text_label) < 3 else self.DEFAULT_TEXT_POINTSIZE
                )
                font.setPointSize(max(1, point_size))
                font.setBold(True)
                painter.setFont(font)
                painter.setPen(
                    self._paint_color(
                        sidebar_icon_text_active() if self.isChecked() else sidebar_icon_text_inactive()
                    )
                )
                tr = painter.fontMetrics().boundingRect(self.text_label)
                painter.drawText(int(-tr.width() / 2), int(tr.height() / 4), self.text_label)
                painter.restore()
        finally:
            painter.end()
        if paint_icon:
            super().paintEvent(event)

    def refresh_theme(self) -> None:
        self.color = sidebar_icon_default_color()
        self._active = sidebar_icon_active_color()
        self.update()
