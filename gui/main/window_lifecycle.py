from __future__ import annotations

import logging

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from ..utils.constants import MAIN_WINDOW_HEIGHT, MAIN_WINDOW_WIDTH


def resize_for_show(window) -> None:
    is_docked = False
    if hasattr(window, "stack") and hasattr(window, "dock_view"):
        is_docked = window.stack.currentWidget() is window.dock_view
    if not is_docked and window.width() < MAIN_WINDOW_WIDTH:
        window.resize(MAIN_WINDOW_WIDTH, max(window.height(), MAIN_WINDOW_HEIGHT))


def save_settings_for_close(window) -> None:
    try:
        window.settings_controller.save_app_settings()
    except Exception:
        logging.exception("Failed to save app settings during shutdown.")


def close_engine_for_shutdown(window) -> None:
    if window.engine:
        try:
            window.engine.close()
        except Exception:
            logging.exception("Failed to close engine during shutdown.")


def maybe_quit_after_close() -> None:
    app = QApplication.instance()
    if app is not None:
        QTimer.singleShot(0, app.quit)
