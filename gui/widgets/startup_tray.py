from __future__ import annotations

from PySide6.QtCore import QObject
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from ..utils.app_icon import app_icon


class StartupTrayController(QObject):
    """Tray surface used while a startup scan runs in the background."""

    def __init__(self, monitor, *, cancel_callback=None, quit_callback=None, parent=None):
        super().__init__(parent)
        self.monitor = monitor
        self.cancel_callback = cancel_callback
        self.quit_callback = quit_callback
        self._active = False
        self._background_notice_shown = False
        self._tray_available = QSystemTrayIcon.isSystemTrayAvailable()
        self.tray = QSystemTrayIcon(app_icon(), self) if self._tray_available else None
        self.menu = QMenu() if self._tray_available else None
        if self.tray is not None and self.menu is not None:
            self.act_show = QAction("Show Scan Window", self)
            self.act_hide = QAction("Hide Scan Window", self)
            self.act_cancel = QAction("Cancel Scan", self)
            self.act_quit = QAction("Quit Unshuffle", self)
            self.act_show.triggered.connect(self.show_monitor)
            self.act_hide.triggered.connect(self.hide_monitor)
            self.act_cancel.triggered.connect(self.cancel_scan)
            self.act_quit.triggered.connect(self.quit_app)
            self.menu.addAction(self.act_show)
            self.menu.addAction(self.act_hide)
            self.menu.addSeparator()
            self.menu.addAction(self.act_cancel)
            self.menu.addAction(self.act_quit)
            self.tray.setContextMenu(self.menu)
            self.tray.setToolTip("Unshuffle - Scanning...")
            self.tray.activated.connect(self._on_activated)

    def is_available(self) -> bool:
        return self.tray is not None

    def start(self) -> None:
        self._active = True
        if self.tray is not None:
            self.tray.show()
        if hasattr(self.monitor, "set_background_close_handler"):
            self.monitor.set_background_close_handler(self.hide_monitor)
        if hasattr(self.monitor, "set_background_minimize_handler"):
            self.monitor.set_background_minimize_handler(self.hide_monitor)

    def finish(self) -> None:
        self._active = False
        if hasattr(self.monitor, "set_background_close_handler"):
            self.monitor.set_background_close_handler(None)
        if hasattr(self.monitor, "set_background_minimize_handler"):
            self.monitor.set_background_minimize_handler(None)
        if self.tray is not None:
            self.tray.hide()

    def update_status(self, payload) -> None:
        text = ""
        if isinstance(payload, dict):
            text = str(payload.get("message") or payload.get("status") or payload.get("text") or "")
        else:
            text = str(payload or "")
        if self.tray is not None:
            self.tray.setToolTip(f"Unshuffle - {text}" if text else "Unshuffle - Scanning...")

    def hide_monitor(self) -> bool:
        if not self._active:
            return False
        if self.tray is None:
            self.monitor.showMinimized()
            return True
        self.monitor.hide()
        self._show_background_notice_once()
        return True

    def _show_background_notice_once(self) -> None:
        if self._background_notice_shown or self.tray is None:
            return
        self._background_notice_shown = True
        if QSystemTrayIcon.supportsMessages():
            self.tray.showMessage(
                "Unshuffle",
                "Scan running in background. Use the tray icon to reopen.",
                QSystemTrayIcon.MessageIcon.Information,
                3500,
            )

    def show_monitor(self) -> None:
        self.monitor.showNormal()
        self.monitor.raise_()
        self.monitor.activateWindow()

    def cancel_scan(self) -> None:
        if callable(self.cancel_callback):
            self.cancel_callback()

    def quit_app(self) -> None:
        self.cancel_scan()
        self.finish()
        app = QApplication.instance()
        if callable(self.quit_callback):
            self.quit_callback()
        elif app is not None:
            app.quit()

    def _on_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.show_monitor()
