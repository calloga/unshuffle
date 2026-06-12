from __future__ import annotations

from functools import lru_cache
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QWidget

from unshuffle.core.assets import asset_path

APP_ICON_PATH = asset_path("icons", "app_logo.png")


@lru_cache(maxsize=1)
def app_icon() -> QIcon:
    if not APP_ICON_PATH.exists():
        return QIcon()
    return QIcon(str(APP_ICON_PATH))


def apply_app_icon(widget: QWidget | None = None) -> None:
    icon = app_icon()
    if icon.isNull():
        return
    app = QApplication.instance()
    if app is not None:
        app.setWindowIcon(icon)
    if widget is not None:
        widget.setWindowIcon(icon)
