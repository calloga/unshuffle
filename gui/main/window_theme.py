from __future__ import annotations

import sys

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QWidget

from ..utils import ui_helpers
from ..utils.styles import set_zoom_percent, sync_color_palette
from ..styles import clamp_zoom


def apply_theme(window, theme_key: str) -> None:
    window.theme_manager.set_theme(theme_key)
    app = QApplication.instance()
    window.theme_manager.sync_system_theme(app if isinstance(app, QApplication) else None)
    sync_color_palette(window.theme_manager.colors)
    qss = window.theme_manager.build_qss()
    app_for_stylesheet = app if isinstance(app, QApplication) else None
    previous_updates = window.updatesEnabled()
    window.setUpdatesEnabled(False)
    try:
        apply_theme_stylesheet(window, qss, app_for_stylesheet)
        refresh_theme_bindings(window, visible_only=window.isVisible())
        if hasattr(window, "custom_menu_bar"):
            window.custom_menu_bar.set_theme_checked(window.theme_manager.requested_theme_key)
    finally:
        window.setUpdatesEnabled(previous_updates)
    apply_native_window_theme(window)
    window.update()


def apply_theme_stylesheet(window, qss: str, app: object | None) -> None:
    set_app_style = getattr(app, "setStyleSheet", None)
    if app is not None and not window.isVisible() and callable(set_app_style):
        set_app_style(qss)
        return

    window.setStyleSheet(qss)
    if app is None:
        return

    top_level_widgets = getattr(app, "topLevelWidgets", None)
    if not callable(top_level_widgets):
        return
    widgets = top_level_widgets()
    if not isinstance(widgets, (list, tuple)):
        return
    for widget in widgets:
        if widget is window or not widget.isVisible():
            continue
        if not isinstance(widget, QWidget):
            continue
        widget.setStyleSheet(qss)
        refresh = getattr(widget, "refresh_theme", None)
        if callable(refresh):
            refresh()


def apply_zoom(window, zoom_percent: int) -> None:
    zoom = clamp_zoom(zoom_percent)
    window.theme_manager.set_zoom(zoom)
    set_zoom_percent(zoom)
    app = QApplication.instance()
    scaled_font = window.theme_manager.scaled_font(window._base_font)
    if app is not None:
        app.setFont(scaled_font)
    window.setFont(scaled_font)
    refresh_theme_bindings(window)
    if hasattr(window, "custom_menu_bar"):
        window.custom_menu_bar.set_zoom_checked(zoom)


def refresh_theme_bindings(window, *, visible_only: bool = False) -> None:
    refreshed = set()
    for widget in window.findChildren(QWidget):
        if visible_only and not widget.isVisibleTo(window):
            continue
        refresh = getattr(widget, "refresh_theme", None)
        if callable(refresh) and id(widget) not in refreshed:
            refreshed.add(id(widget))
            refresh()
    window._style_page_nav_bar()
    if getattr(window, "library_tab", None):
        model = window.library_tab.view_table.model()
        source_model = model.sourceModel() if hasattr(model, "sourceModel") else model
        if source_model is not None and hasattr(source_model, "refresh_theme_palette"):
            source_model.refresh_theme_palette()
        window.library_tab.view_table.viewport().update()
        window.library_tab.view_tree.viewport().update()
        ui_helpers.setup_view_headers(window)


def apply_native_window_theme(window, widget: QWidget | None = None) -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        target = widget or window
        hwnd = target.winId()
        if not hwnd:
            return

        colors = window.theme_manager.colors
        dwmapi = ctypes.windll.dwmapi
        DWMWA_USE_IMMERSIVE_DARK_MODE = 20
        DWMWA_BORDER_COLOR = 34
        DWMWA_CAPTION_COLOR = 35
        DWMWA_TEXT_COLOR = 36

        def _qcolor(color: str, fallback: str) -> QColor:
            text = (color or "").strip()
            parsed = QColor(text)
            if not parsed.isValid() and text.startswith("rgba(") and text.endswith(")"):
                parts = [part.strip() for part in text[5:-1].split(",")]
                if len(parts) == 4:
                    try:
                        parsed = QColor(int(parts[0]), int(parts[1]), int(parts[2]), int(float(parts[3]) * 255))
                    except ValueError:
                        parsed = QColor()
            if not parsed.isValid():
                parsed = QColor((fallback or "#000000"))
            return parsed

        def _colorref(color: str, fallback: str | None = None) -> int:
            base = _qcolor(fallback or colors.bg_dark, colors.bg_dark)
            parsed = _qcolor(color, fallback or colors.bg_dark)
            if parsed.alpha() < 255:
                alpha = parsed.alphaF()
                r = int(parsed.red() * alpha + base.red() * (1.0 - alpha))
                g = int(parsed.green() * alpha + base.green() * (1.0 - alpha))
                b = int(parsed.blue() * alpha + base.blue() * (1.0 - alpha))
            else:
                r = parsed.red()
                g = parsed.green()
                b = parsed.blue()
            return r | (g << 8) | (b << 16)

        dark_mode = ctypes.c_int(1)
        dwmapi.DwmSetWindowAttribute(
            hwnd,
            DWMWA_USE_IMMERSIVE_DARK_MODE,
            ctypes.byref(dark_mode),
            ctypes.sizeof(dark_mode),
        )

        table_separator = _qcolor(colors.border_light, colors.bg_dark)
        table_separator.setAlpha(10 if _qcolor(colors.bg_list, colors.bg_dark).lightness() < 120 else 14)
        caption = ctypes.c_int(_colorref(colors.bg_dark))
        border = ctypes.c_int(_colorref(table_separator.name(QColor.HexArgb), colors.bg_dark))
        text = ctypes.c_int(_colorref(colors.text_light))

        for attr, value in (
            (DWMWA_CAPTION_COLOR, caption),
            (DWMWA_BORDER_COLOR, border),
            (DWMWA_TEXT_COLOR, text),
        ):
            dwmapi.DwmSetWindowAttribute(
                hwnd,
                attr,
                ctypes.byref(value),
                ctypes.sizeof(value),
            )
    except Exception:
        pass
