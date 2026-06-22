from __future__ import annotations

import threading

from PySide6.QtCore import QObject, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QMessageBox, QWidget

from unshuffle.core.constants import APP_NAME, APP_VERSION
from unshuffle.updates import UpdateInfo, fetch_update_info, is_newer_version


class UpdateController(QObject):
    updateAvailable = Signal(object)
    noUpdateAvailable = Signal()
    updateCheckFailed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._checking = False
        self._prompted_versions: set[str] = set()
        self.updateAvailable.connect(self._show_update_prompt)

    def check_for_updates(self, *, manual: bool = False) -> None:
        if self._checking:
            return
        self._checking = True

        def _worker() -> None:
            try:
                info = fetch_update_info()
                if info is not None and is_newer_version(info.version, APP_VERSION):
                    self.updateAvailable.emit(info)
                elif manual:
                    self.noUpdateAvailable.emit()
            except Exception:
                if manual:
                    self.updateCheckFailed.emit()
            finally:
                self._checking = False

        thread = threading.Thread(target=_worker, name="unshuffle-update-check", daemon=True)
        thread.start()

    @Slot(object)
    def _show_update_prompt(self, info: UpdateInfo) -> None:
        if info.version in self._prompted_versions:
            return
        self._prompted_versions.add(info.version)
        parent = self._parent_widget()
        message = QMessageBox(parent)
        message.setIcon(QMessageBox.Information)
        message.setWindowTitle("Update Available")
        message.setText(f"{APP_NAME} {info.version} is available.")
        message.setInformativeText(f"You are running {APP_NAME} {APP_VERSION}.")
        open_button = message.addButton("Update", QMessageBox.AcceptRole)
        message.addButton("Later", QMessageBox.RejectRole)
        message.exec()
        if message.clickedButton() is open_button:
            update_url = info.download_url or info.url
            if update_url:
                QDesktopServices.openUrl(QUrl(update_url))

    @Slot()
    def show_no_update_message(self) -> None:
        QMessageBox.information(self._parent_widget(), "Check for Updates", f"{APP_NAME} {APP_VERSION} is up to date.")

    @Slot()
    def show_update_check_failed_message(self) -> None:
        QMessageBox.warning(self._parent_widget(), "Check for Updates", "Could not check for updates right now.")

    def _parent_widget(self) -> QWidget | None:
        parent = self.parent()
        return parent if isinstance(parent, QWidget) else None
