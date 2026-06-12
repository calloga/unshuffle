"""Theme and zoom management for GUI."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from .qss import build_main_style
from .tokens_geometry import DEFAULT_ZOOM_PERCENT, clamp_zoom
from .tokens_semantic import (
    ASH_THEME_KEY,
    DEFAULT_THEME_KEY,
    OCEAN_THEME_KEY,
    SYSTEM_THEME_KEY,
    THEMES,
    ThemeColors,
)


def normalize_theme_key(theme_key: str | None) -> str:
    raw_key = (theme_key or "").strip()
    if not raw_key:
        return DEFAULT_THEME_KEY
    if raw_key == SYSTEM_THEME_KEY:
        return SYSTEM_THEME_KEY
    if raw_key in THEMES:
        return raw_key
    return DEFAULT_THEME_KEY


def resolve_system_theme_key(color_scheme: Qt.ColorScheme | None) -> str:
    if color_scheme == Qt.ColorScheme.Light:
        return OCEAN_THEME_KEY
    return ASH_THEME_KEY


def _current_color_scheme(app: QApplication | None) -> Qt.ColorScheme | None:
    if app is None:
        return None
    try:
        return app.styleHints().colorScheme()
    except Exception:
        return None


@dataclass
class ThemeState:
    key: str = DEFAULT_THEME_KEY
    follow_system: bool = False
    effective_key: str = DEFAULT_THEME_KEY
    zoom_percent: int = DEFAULT_ZOOM_PERCENT


class ThemeManager:
    def __init__(self) -> None:
        self.state = ThemeState()

    def available_themes(self) -> dict[str, ThemeColors]:
        return THEMES

    def set_theme(self, theme_key: str) -> None:
        normalized = normalize_theme_key(theme_key)
        if normalized == SYSTEM_THEME_KEY:
            self.state.follow_system = True
            self.sync_system_theme()
            return
        self.state.key = normalized
        self.state.follow_system = False
        self.state.effective_key = normalized

    def sync_system_theme(self, app: QApplication | None = None) -> None:
        if not self.state.follow_system:
            self.state.effective_key = self.state.key
            return
        current_app = app or QApplication.instance()
        self.state.effective_key = resolve_system_theme_key(
            _current_color_scheme(current_app if isinstance(current_app, QApplication) else None)
        )

    def set_zoom(self, zoom_percent: int) -> None:
        self.state.zoom_percent = clamp_zoom(zoom_percent)

    @property
    def requested_theme_key(self) -> str:
        return SYSTEM_THEME_KEY if self.state.follow_system else self.state.key

    @property
    def colors(self) -> ThemeColors:
        return THEMES[self.state.effective_key]

    def build_qss(self) -> str:
        return build_main_style(self.colors)

    def scaled_font(self, base_font: QFont) -> QFont:
        factor = self.state.zoom_percent / 100.0
        out = QFont(base_font)
        if out.pointSizeF() > 0:
            out.setPointSizeF(out.pointSizeF() * factor)
        elif out.pointSize() > 0:
            out.setPointSize(round(out.pointSize() * factor))
        return out
