from __future__ import annotations

import json
import logging
import os
from collections import Counter
from dataclasses import asdict, dataclass
from math import cbrt
from typing import cast

from PySide6.QtCore import QEvent, QObject, QRect, QTimer
from PySide6.QtGui import QColor, QGuiApplication, QImage
from PySide6.QtWidgets import QMessageBox

from gui.styles import THEMES


logger = logging.getLogger("unshuffle")


DOCK_MATCH_HOST_KEY = "dock_match_host"
DOCK_MATCH_HOST_CONSENT_KEY = "dock_match_host_consent"
DOCK_MATCH_HOST_PALETTE_KEY = "dock_match_host_palette"


@dataclass(frozen=True)
class DockAdaptivePalette:
    base: str
    darker: str
    panel: str
    raised: str
    hover: str
    border: str
    accent: str
    accent_hover: str
    text: str
    muted: str
    selection: str
    scrollbar: str
    scrollbar_handle: str
    source_theme: str

    @classmethod
    def from_json(cls, value: object) -> "DockAdaptivePalette | None":
        try:
            payload = json.loads(str(value or ""))
            if "darker" not in payload:
                payload.update({
                    "darker": payload["base"],
                    "panel": payload["raised"],
                    "hover": payload["border"],
                    "accent_hover": payload["accent"],
                    "selection": payload["accent"],
                    "scrollbar": payload["base"],
                    "scrollbar_handle": payload["border"],
                    "source_theme": "legacy",
                })
            return cls(**{key: str(payload[key]) for key in cls.__annotations__})
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None


def _linear_channel(value: float) -> float:
    return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4


def _srgb_channel(value: float) -> float:
    value = max(0.0, min(1.0, value))
    return 12.92 * value if value <= 0.0031308 else 1.055 * (value ** (1 / 2.4)) - 0.055


def _oklab(color: QColor) -> tuple[float, float, float]:
    rgba = cast(tuple[float, float, float, float], color.getRgbF())
    red, green, blue = (_linear_channel(channel) for channel in rgba[:3])
    l_value = 0.4122214708 * red + 0.5363325363 * green + 0.0514459929 * blue
    m_value = 0.2119034982 * red + 0.6806995451 * green + 0.1073969566 * blue
    s_value = 0.0883024619 * red + 0.2817188376 * green + 0.6299787005 * blue
    l_root, m_root, s_root = cbrt(l_value), cbrt(m_value), cbrt(s_value)
    return (
        0.2104542553 * l_root + 0.7936177850 * m_root - 0.0040720468 * s_root,
        1.9779984951 * l_root - 2.4285922050 * m_root + 0.4505937099 * s_root,
        0.0259040371 * l_root + 0.7827717662 * m_root - 0.8086757660 * s_root,
    )


def _from_oklab(lightness: float, a_value: float, b_value: float, alpha: int = 255) -> QColor:
    l_root = lightness + 0.3963377774 * a_value + 0.2158037573 * b_value
    m_root = lightness - 0.1055613458 * a_value - 0.0638541728 * b_value
    s_root = lightness - 0.0894841775 * a_value - 1.2914855480 * b_value
    l_value, m_value, s_value = l_root**3, m_root**3, s_root**3
    red = +4.0767416621 * l_value - 3.3077115913 * m_value + 0.2309699292 * s_value
    green = -1.2684380046 * l_value + 2.6097574011 * m_value - 0.3413193965 * s_value
    blue = -0.0041960863 * l_value - 0.7034186147 * m_value + 1.7076147010 * s_value
    color = QColor.fromRgbF(_srgb_channel(red), _srgb_channel(green), _srgb_channel(blue))
    color.setAlpha(alpha)
    return color


def _distance(first: QColor, second: QColor) -> float:
    one = _oklab(first)
    two = _oklab(second)
    return sum((left - right) ** 2 for left, right in zip(one, two))


def _transfer_color(
    color_value: str,
    source_base: QColor,
    target_base: QColor,
    *,
    strength: float = 1.0,
) -> QColor:
    color = QColor(color_value)
    source_lab = _oklab(source_base)
    target_lab = _oklab(target_base)
    color_lab = _oklab(color)
    strength = max(0.0, min(1.0, float(strength)))
    return _from_oklab(
        color_lab[0] + (target_lab[0] - source_lab[0]) * strength,
        color_lab[1] + (target_lab[1] - source_lab[1]) * strength,
        color_lab[2] + (target_lab[2] - source_lab[2]) * strength,
        color.alpha(),
    )


def transfer_palette_color(color: QColor | str, palette: DockAdaptivePalette | None) -> QColor:
    """Transfer an app-theme color into a sampled dock palette."""
    source = QColor(color)
    if palette is None or not source.isValid():
        return source
    theme = next(
        (candidate for candidate in THEMES.values() if candidate.id == palette.source_theme),
        None,
    )
    if theme is None:
        return source
    return _transfer_color(
        source.name(QColor.HexArgb),
        QColor(theme.bg_dark),
        QColor(palette.base),
        strength=0.5,
    )


def _contrast_ratio(first: QColor, second: QColor) -> float:
    def luminance(color: QColor) -> float:
        rgba = cast(tuple[float, float, float, float], color.getRgbF())
        red, green, blue = (_linear_channel(channel) for channel in rgba[:3])
        return 0.2126 * red + 0.7152 * green + 0.0722 * blue

    lighter, darker = sorted((luminance(first), luminance(second)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def _color_name(color: QColor) -> str:
    return color.name(QColor.HexArgb) if color.alpha() < 255 else color.name()


def palette_from_colors(colors: list[QColor]) -> DockAdaptivePalette | None:
    valid = [color for color in colors if color.isValid() and color.alpha() > 220]
    if not valid:
        return None
    buckets = Counter((color.red() // 16, color.green() // 16, color.blue() // 16) for color in valid)
    dominant_bucket = buckets.most_common(1)[0][0]
    dominant = [
        color for color in valid
        if (color.red() // 16, color.green() // 16, color.blue() // 16) == dominant_bucket
    ]
    base = QColor(
        round(sum(color.red() for color in dominant) / len(dominant)),
        round(sum(color.green() for color in dominant) / len(dominant)),
        round(sum(color.blue() for color in dominant) / len(dominant)),
    )
    theme = min(THEMES.values(), key=lambda candidate: _distance(QColor(candidate.bg_dark), base))
    source_base = QColor(theme.bg_dark)

    def transfer(value: str) -> QColor:
        return _transfer_color(value, source_base, base, strength=0.5)

    text = transfer(theme.text_main)
    if _contrast_ratio(text, base) < 4.5:
        text = QColor("#101216") if base.lightnessF() > 0.52 else QColor("#f3f5f7")
    muted = transfer(theme.text_muted)
    return DockAdaptivePalette(
        base=base.name(),
        darker=_color_name(transfer(theme.bg_darker)),
        panel=_color_name(transfer(theme.bg_med)),
        raised=_color_name(transfer(theme.bg_light)),
        hover=_color_name(transfer(theme.bg_hover)),
        border=_color_name(transfer(theme.border)),
        accent=_color_name(transfer(theme.primary)),
        accent_hover=_color_name(transfer(theme.primary_hover)),
        text=_color_name(text),
        muted=_color_name(muted),
        selection=_color_name(transfer(theme.selection)),
        scrollbar=_color_name(transfer(theme.bg_scrollbar)),
        scrollbar_handle=_color_name(transfer(theme.bg_scrollbar_handle)),
        source_theme=theme.id,
    )


def _sample_image_colors(image: QImage) -> list[QColor]:
    if image.isNull():
        return []
    return [
        image.pixelColor(x_value, y_value)
        for y_value in range(0, image.height(), max(1, image.height() // 96))
        for x_value in range(0, image.width(), max(1, image.width() // 24))
    ]


def _is_blank_host_capture(colors: list[QColor]) -> bool:
    if not colors:
        return True
    nearly_black = sum(
        1 for color in colors
        if color.alpha() > 220 and max(color.red(), color.green(), color.blue()) <= 3
    )
    return nearly_black / len(colors) >= 0.98


def _grab_windows_host_region(app, screen, geometry: QRect) -> QImage | None:
    """Capture the dock footprint from the first host window beneath Unshuffle."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
        user32.GetWindow.restype = wintypes.HWND
        user32.IsWindowVisible.argtypes = [wintypes.HWND]
        user32.IsWindowVisible.restype = wintypes.BOOL
        user32.IsIconic.argtypes = [wintypes.HWND]
        user32.IsIconic.restype = wintypes.BOOL
        user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        user32.GetWindowRect.restype = wintypes.BOOL
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        hwnd = int(app.winId())
        process_id = os.getpid()
        ratio = max(1.0, float(screen.devicePixelRatio()))
        dock_rect = QRect(
            round(geometry.x() * ratio),
            round(geometry.y() * ratio),
            round(geometry.width() * ratio),
            round(geometry.height() * ratio),
        )
        candidate = user32.GetWindow(hwnd, 2)  # GW_HWNDNEXT: directly below in Z order.
        while candidate:
            owner_pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(candidate, ctypes.byref(owner_pid))
            rect = wintypes.RECT()
            visible = bool(user32.IsWindowVisible(candidate)) and not bool(user32.IsIconic(candidate))
            if (
                visible
                and owner_pid.value != process_id
                and user32.GetWindowRect(candidate, ctypes.byref(rect))
            ):
                host_rect = QRect(rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)
                overlap = host_rect.intersected(dock_rect)
                overlap_area = overlap.width() * overlap.height()
                dock_area = max(1, dock_rect.width() * dock_rect.height())
                if overlap_area >= dock_area * 0.35:
                    local_x = round((overlap.left() - host_rect.left()) / ratio)
                    local_y = round((overlap.top() - host_rect.top()) / ratio)
                    width = max(1, round(overlap.width() / ratio))
                    height = max(1, round(overlap.height() / ratio))
                    image = screen.grabWindow(
                        int(candidate), local_x, local_y, width, height
                    ).toImage()
                    if not image.isNull():
                        return image
            candidate = user32.GetWindow(candidate, 2)
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        return None
    return None


class DockAppearanceController(QObject):
    """Applies a local, opt-in palette sampled from the host beneath the dock."""

    def __init__(self, app) -> None:
        super().__init__(app)
        self.app = app
        self.settings = app.settings_controller.settings
        self._idle_timer = QTimer(self)
        self._idle_timer.setSingleShot(True)
        self._idle_timer.setInterval(400)
        self._idle_timer.timeout.connect(self._poll_background)
        self._scan_timer = QTimer(self)
        self._scan_timer.setSingleShot(True)
        self._scan_timer.setInterval(4000)
        self._scan_timer.timeout.connect(self._apply_pending_palette)
        self._periodic_timer = QTimer(self)
        self._periodic_timer.setSingleShot(True)
        self._periodic_timer.setInterval(5000)
        self._periodic_timer.timeout.connect(self._poll_background)
        self._pending_palette: DockAdaptivePalette | None = None
        self._movement_scan_pending = False
        self._last_capture_source = "none"
        app.dock_view.environment_screen_overlay.sweepFinished.connect(self._apply_pending_palette)
        app.installEventFilter(self)

    def is_enabled(self) -> bool:
        return self.settings.value(DOCK_MATCH_HOST_KEY, False, type=bool)

    def set_enabled(self, enabled: bool) -> bool:
        enabled = bool(enabled)
        if enabled and not self.settings.value(DOCK_MATCH_HOST_CONSENT_KEY, False, type=bool):
            answer = QMessageBox.question(
                self.app,
                "Match Dock to Background",
                "Allow Unshuffle to sample nearby screen colors while docked?\n\n"
                "Only a derived color palette is kept. Screenshots are never saved or transmitted.",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if answer != QMessageBox.Yes:
                return False
            self.settings.setValue(DOCK_MATCH_HOST_CONSENT_KEY, True)
        self.settings.setValue(DOCK_MATCH_HOST_KEY, enabled)
        if enabled:
            self.apply_cached()
            self.schedule()
        else:
            self._stop_timers()
            self.app.dock_view.set_environment_scanning(False)
            self.app.dock_view.clear_adaptive_palette()
            self._restore_vibe_theme()
        return enabled

    def eventFilter(self, watched, event) -> bool:
        if watched is self.app and event.type() in (QEvent.Move, QEvent.Resize):
            self.schedule()
        return False

    def schedule(self) -> None:
        if self.is_enabled() and self.app.stack.currentWidget() is self.app.dock_view:
            self.app.dock_view.set_environment_scanning(False)
            self._scan_timer.stop()
            self._periodic_timer.stop()
            self._pending_palette = None
            self._movement_scan_pending = True
            self._idle_timer.start()

    def _poll_background(self) -> None:
        self.refresh()

    def _stop_timers(self) -> None:
        self._idle_timer.stop()
        self._scan_timer.stop()
        self._periodic_timer.stop()
        self._pending_palette = None
        self._movement_scan_pending = False

    def apply_cached(self) -> None:
        palette = DockAdaptivePalette.from_json(self.settings.value(DOCK_MATCH_HOST_PALETTE_KEY, ""))
        if palette is not None:
            self.app.dock_view.apply_adaptive_palette(palette, animate=False)
            self._apply_vibe_palette(palette)

    def _apply_vibe_palette(self, palette: DockAdaptivePalette) -> None:
        vibe_bar = getattr(self.app, "vibe_bar", None)
        if vibe_bar is not None and hasattr(vibe_bar, "apply_adaptive_palette"):
            vibe_bar.apply_adaptive_palette(
                palette,
                lambda color, current=palette: transfer_palette_color(color, current),
            )

    def _restore_vibe_theme(self) -> None:
        vibe_bar = getattr(self.app, "vibe_bar", None)
        if vibe_bar is not None and hasattr(vibe_bar, "refresh_theme"):
            vibe_bar.refresh_theme()

    def _apply_pending_palette(self) -> None:
        self._scan_timer.stop()
        palette = self._pending_palette
        self._pending_palette = None
        if (
            palette is None
            or not self.is_enabled()
            or self.app.stack.currentWidget() is not self.app.dock_view
        ):
            self.app.dock_view.set_environment_scanning(False)
            return
        self.settings.setValue(DOCK_MATCH_HOST_PALETTE_KEY, json.dumps(asdict(palette)))
        self.app.dock_view.apply_adaptive_palette(palette, animate=True)
        self._apply_vibe_palette(palette)
        self.app.dock_view.set_environment_scanning(False)
        self._periodic_timer.start()

    def suspend(self) -> None:
        self._stop_timers()
        self.app.dock_view.set_environment_scanning(False)
        self.app.dock_view.restore_native_border()
        self._restore_vibe_theme()

    def refresh(self) -> None:
        if not self.is_enabled() or self.app.stack.currentWidget() is not self.app.dock_view:
            self.app.dock_view.set_environment_scanning(False)
            self._stop_timers()
            return
        movement_scan = self._movement_scan_pending
        self._movement_scan_pending = False
        # Keep the visual sweep out of the pixels used to derive the palette.
        self.app.dock_view.set_environment_scanning(False)
        try:
            screen = self.app.screen() or QGuiApplication.primaryScreen()
            if screen is None:
                return
            geometry = self.app.frameGeometry()
            available = screen.availableGeometry()
            host_image = _grab_windows_host_region(self.app, screen, geometry)
            host_colors = _sample_image_colors(host_image) if host_image is not None else []
            blank_host_capture = bool(host_colors) and _is_blank_host_capture(host_colors)
            colors = [] if blank_host_capture else host_colors
            capture_source = "host-window" if colors else "adjacent-fallback"
            strip_width = min(24, max(1, available.width()))
            regions: list[tuple[int, int, int, int]] = []
            if not colors and geometry.left() - strip_width >= available.left():
                regions.append((geometry.left() - strip_width, geometry.top(), strip_width, geometry.height()))
            if not colors and geometry.right() + strip_width <= available.right():
                regions.append((geometry.right() + 1, geometry.top(), strip_width, geometry.height()))
            if not colors and not regions:
                strip_height = min(24, max(1, available.height()))
                if geometry.top() - strip_height >= available.top():
                    regions.append((geometry.left(), geometry.top() - strip_height, geometry.width(), strip_height))
                if geometry.bottom() + strip_height <= available.bottom():
                    regions.append((geometry.left(), geometry.bottom() + 1, geometry.width(), strip_height))
            for x_value, y_value, width, height in regions:
                image = screen.grabWindow(
                    0,
                    x_value,
                    max(available.top(), y_value),
                    max(1, width),
                    max(1, min(height, available.bottom() - max(available.top(), y_value) + 1)),
                ).toImage()
                colors.extend(_sample_image_colors(image))
            if colors and blank_host_capture:
                capture_source = "adjacent-blank-host"
            palette = palette_from_colors(colors)
            if palette is None:
                self._last_capture_source = "unavailable"
                return
            current = getattr(self.app.dock_view, "_adaptive_palette", None)
            threshold = 0.000005 if movement_scan else 0.00008
            changed = (
                current is None
                or current.source_theme != palette.source_theme
                or _distance(QColor(current.base), QColor(palette.base)) > threshold
            )
            self._last_capture_source = capture_source
            logger.debug(
                "Dock background sample: source=%s base=%s theme=%s changed=%s movement=%s",
                capture_source,
                palette.base,
                palette.source_theme,
                changed,
                movement_scan,
            )
            if changed or movement_scan:
                self._pending_palette = palette
                self.app.dock_view.set_environment_scanning(True)
                self._scan_timer.start()
        finally:
            if not self._scan_timer.isActive():
                self.app.dock_view.set_environment_scanning(False)
            if (
                not self._scan_timer.isActive()
                and self.is_enabled()
                and self.app.stack.currentWidget() is self.app.dock_view
            ):
                self._periodic_timer.start()
