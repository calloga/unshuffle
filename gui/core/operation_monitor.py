from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject


class OperationMonitorManager(QObject):
    """Owns the active in-app operation monitor dialog."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.app = parent
        self.dialog = None
        self._token = 0
        self._active_token: int | None = None

    @property
    def active(self) -> bool:
        return self._active_token is not None

    def start(
        self,
        title: str,
        *,
        cancellable: bool = False,
        on_cancel: Callable[[], None] | None = None,
    ) -> int:
        self._token += 1
        self._active_token = self._token
        dialog = self._ensure_dialog()
        dialog.start(title, cancellable=cancellable, on_cancel=on_cancel)
        dialog.show()
        dialog.raise_()
        return self._active_token

    def update(self, payload, *, token: int | None = None) -> None:
        if not self._accepts(token):
            return
        dialog = self._ensure_dialog()
        dialog.update_progress(payload)
        if not dialog.isVisible():
            dialog.show()

    def set_status(self, text: str, *, token: int | None = None) -> None:
        if not self._accepts(token):
            return
        self._ensure_dialog().set_status(text)

    def finish(self, text: str | None = None, *, token: int | None = None) -> None:
        if not self._accepts(token):
            return
        dialog = self.dialog
        self._active_token = None
        if dialog is not None:
            dialog.finish(text)

    def fail(self, message: str, *, token: int | None = None) -> None:
        if not self._accepts(token):
            return
        dialog = self.dialog
        self._active_token = None
        if dialog is not None:
            dialog.fail(message)

    def _accepts(self, token: int | None) -> bool:
        if self._active_token is None:
            return False
        return token is None or token == self._active_token

    def _ensure_dialog(self):
        from ..widgets.operation_monitor import OperationMonitorDialog

        if self.dialog is None:
            self.dialog = OperationMonitorDialog(self.app)
        return self.dialog
