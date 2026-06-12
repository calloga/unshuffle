from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout, QWidget

from ..utils.app_icon import apply_app_icon
from ..utils.layout_helpers import apply_layout_margins, apply_layout_spacing
from ..utils.styles import ColorPalette, apply_style, button_style, scaled_px


class StartupScanMonitor(QWidget):
    """Small, normal window used while a scan-heavy launch is still running."""

    def __init__(self):
        super().__init__(None, Qt.Window | Qt.CustomizeWindowHint | Qt.WindowTitleHint | Qt.WindowMinimizeButtonHint)
        apply_app_icon(self)
        self.setWindowTitle("Unshuffle scan")
        self.setFixedSize(scaled_px(400), scaled_px(154))
        self._background_close_handler: Callable[[], bool] | None = None
        self._background_minimize_handler: Callable[[], bool] | None = None
        self._cancel_handler: Callable[[], None] | None = None

        layout = QVBoxLayout(self)
        apply_layout_margins(layout, (scaled_px(16), scaled_px(14), scaled_px(16), scaled_px(14)))
        apply_layout_spacing(layout, scaled_px(8))

        title_row = QHBoxLayout()
        apply_layout_margins(title_row, (0, 0, 0, 0))
        apply_layout_spacing(title_row, scaled_px(8))
        self.title_label = QLabel("Scanning selected folders")
        self.title_label.setObjectName("StartupScanMonitorTitle")
        title_row.addWidget(self.title_label, 1)

        self.status_label = QLabel("Preparing scan...")
        self.status_label.setWordWrap(True)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(scaled_px(6))

        button_row = QHBoxLayout()
        self.button_row = button_row
        apply_layout_margins(button_row, (0, 0, 0, 0))
        apply_layout_spacing(button_row, scaled_px(8))
        button_row.addStretch(1)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setObjectName("danger")
        self.btn_cancel.clicked.connect(self.cancel_scan)
        button_row.addWidget(self.btn_cancel, 0)
        self.btn_minimize = QPushButton("Minimize")
        self.btn_minimize.setObjectName("primary")
        self.btn_minimize.clicked.connect(self.minimize_to_background)
        self.btn_minimize.setVisible(False)
        button_row.addWidget(self.btn_minimize, 0)

        layout.addLayout(title_row)
        layout.addWidget(self.status_label)
        layout.addWidget(self.progress)
        layout.addLayout(button_row)

        self.refresh_theme()

    def set_status(self, payload) -> None:
        text = ""
        value = None
        if isinstance(payload, dict):
            text = str(payload.get("message") or payload.get("status") or payload.get("text") or "")
            raw_value = payload.get("percent")
            if raw_value is None:
                raw_value = payload.get("progress")
            try:
                value = int(raw_value) if raw_value is not None else None
            except (TypeError, ValueError):
                value = None
        else:
            text = str(payload or "")
        if text:
            self.status_label.setText(text)
        if value is None or value < 0:
            self.progress.setRange(0, 0)
        else:
            self.progress.setRange(0, 100)
            self.progress.setValue(max(0, min(100, value)))

    def show_near_center(self) -> None:
        screen = self.screen()
        if screen is None:
            from PySide6.QtWidgets import QApplication

            screen = QApplication.primaryScreen()
        if screen is not None:
            geo = screen.availableGeometry()
            self.move(geo.center().x() - self.width() // 2, geo.center().y() - self.height() // 2)
        self.show()

    def set_background_close_handler(self, handler: Callable[[], bool] | None) -> None:
        self._background_close_handler = handler

    def set_background_minimize_handler(self, handler: Callable[[], bool] | None) -> None:
        self._background_minimize_handler = handler
        self.btn_minimize.setVisible(handler is not None)

    def set_cancel_handler(self, handler: Callable[[], None] | None) -> None:
        self._cancel_handler = handler

    def cancel_scan(self) -> None:
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.setText("Stopping")
        self.status_label.setText("Canceling scan...")
        if self._cancel_handler is not None:
            self._cancel_handler()

    def minimize_to_background(self) -> None:
        if self._background_minimize_handler is not None and self._background_minimize_handler():
            return
        self.showMinimized()

    def closeEvent(self, event) -> None:
        if self._background_close_handler is not None and self._background_close_handler():
            event.ignore()
            return
        super().closeEvent(event)

    def refresh_theme(self) -> None:
        apply_style(
            self,
            f"""
            QWidget {{
                background: {ColorPalette.BG_DARK};
                color: {ColorPalette.TEXT_LIGHT};
                border: none;
            }}
            QLabel {{
                background: transparent;
                border: none;
            }}
            QLabel#StartupScanMonitorTitle {{
                color: {ColorPalette.TEXT_LIGHT};
            }}
            {button_style("primary", size="normal")}
            QPushButton#danger {{
                background: {ColorPalette.DANGER};
            }}
            QPushButton#danger:hover {{
                background: {ColorPalette.DANGER_HOVER};
            }}
            QPushButton#danger:disabled {{
                background: {ColorPalette.BG_HOVER};
                color: {ColorPalette.TEXT_DIM};
            }}
            QProgressBar {{
                background: {ColorPalette.BG_LIST};
                border: 1px solid {ColorPalette.BORDER};
                border-radius: 3px;
            }}
            QProgressBar::chunk {{
                background: {ColorPalette.PRIMARY};
                border-radius: 3px;
            }}
            """
        )
        apply_style(self.status_label, f"color: {ColorPalette.TEXT_MUTED};")
