from __future__ import annotations

import logging
import sys
from pathlib import Path

from PySide6.QtCore import QProcess, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

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


def relaunch_startup_launcher_after_close() -> None:
    from .launcher import release_instance_lock

    release_instance_lock()
    frozen = bool(getattr(sys, "frozen", False))
    program = sys.executable
    arguments = [] if frozen else ["-m", "gui"]
    working_directory = (
        Path(sys.executable).resolve().parent
        if frozen
        else Path(__file__).resolve().parents[2]
    )
    result = QProcess.startDetached(program, arguments, str(working_directory))
    started = bool(result[0]) if isinstance(result, tuple) else bool(result)
    if not started:
        logging.error("Could not restart Unshuffle to show the startup launcher.")
        QMessageBox.critical(
            None,
            "Could Not Start Launcher",
            "Unshuffle closed the current session but could not reopen the startup launcher.",
        )
    app = QApplication.instance()
    if app is not None:
        QTimer.singleShot(0, app.quit)
