from __future__ import annotations

import logging
from collections.abc import Callable

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QTimer, Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout, QWidget

from ..utils.app_icon import apply_app_icon
from ..utils.layout_helpers import apply_layout_margins, apply_layout_spacing
from ..utils.styles import ColorPalette, apply_style, button_style, scaled_px
from unshuffle.core.progress import format_eta


class OperationMonitorDialog(QDialog):
    """Application-modal, non-blocking monitor for in-app long operations."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Working")
        self.setWindowModality(Qt.ApplicationModal)
        self.setAttribute(Qt.WA_StyledBackground, True)
        apply_app_icon(self)
        self.setFixedSize(scaled_px(420), scaled_px(176))
        self._active = True
        self._cancel_handler: Callable[[], None] | None = None
        self._last_phase = ""
        self._phase_transition_id = 0
        self._applying_deferred_phase = False

        layout = QVBoxLayout(self)
        apply_layout_margins(layout, (scaled_px(16), scaled_px(14), scaled_px(16), scaled_px(14)))
        apply_layout_spacing(layout, scaled_px(8))

        self.title_label = QLabel("Working")
        self.title_label.setObjectName("OperationMonitorTitle")

        self.phase_label = QLabel("")
        self.phase_label.setObjectName("OperationMonitorPhase")

        self.detail_label = QLabel("")
        self.detail_label.setWordWrap(True)
        self.detail_label.setVisible(False)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(True)
        self.progress.setFixedHeight(scaled_px(20))
        self._progress_animation = QPropertyAnimation(self.progress, b"value", self)
        self._progress_animation.setEasingCurve(QEasingCurve.Type.Linear)

        self.eta_label = QLabel("")
        self.eta_label.setObjectName("OperationMonitorEta")
        self.eta_label.setVisible(False)

        button_row = QHBoxLayout()
        apply_layout_margins(button_row, (0, 0, 0, 0))
        apply_layout_spacing(button_row, scaled_px(8))
        button_row.addStretch(1)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setObjectName("danger")
        self.btn_cancel.clicked.connect(self._on_cancel)
        button_row.addWidget(self.btn_cancel, 0)

        layout.addWidget(self.title_label)
        layout.addWidget(self.phase_label)
        layout.addWidget(self.detail_label)
        layout.addWidget(self.progress)
        layout.addWidget(self.eta_label)
        layout.addLayout(button_row)

        self.refresh_theme()

    def start(self, title: str, *, cancellable: bool = False, on_cancel: Callable[[], None] | None = None, compact: bool = False) -> None:
        self._active = True
        self._cancel_handler = on_cancel
        self._last_phase = ""
        self._phase_transition_id += 1
        self._applying_deferred_phase = False
        title = title or "Working"
        self.setWindowTitle(title)
        self.title_label.setText(title)
        self.title_label.setVisible(not compact)
        self.phase_label.setText("")
        self.phase_label.setVisible(not compact)
        self.detail_label.setText("")
        self.detail_label.setVisible(False)
        self.eta_label.setText("")
        self.eta_label.setVisible(False)
        self.btn_cancel.setVisible(bool(cancellable))
        self.btn_cancel.setEnabled(bool(cancellable))
        self.btn_cancel.setText("Cancel")
        self._progress_animation.stop()
        self.progress.setRange(0, 0)
        self.progress.setFormat("")
        self.setFixedHeight(scaled_px(92 if compact else 176))

    def set_status(self, text: str) -> None:
        self.update_progress({"message": text})

    def update_progress(self, payload) -> None:
        text = ""
        phase = ""
        value = None
        phase_changed = False
        if isinstance(payload, dict):
            if "cancellable" in payload:
                cancellable = bool(payload.get("cancellable"))
                self.btn_cancel.setVisible(cancellable)
                self.btn_cancel.setEnabled(cancellable)
                if not cancellable:
                    self._cancel_handler = None
            phase = str(payload.get("phase") or "").strip()
            text = str(payload.get("message") or payload.get("status") or payload.get("text") or "")
            if phase:
                if phase != self._last_phase:
                    phase_changed = True
                    if self._should_defer_phase_change():
                        self._defer_phase_change(payload)
                        return
                    self._progress_animation.stop()
                    self.progress.setValue(0)
                self._last_phase = phase
                self.phase_label.setText(phase)
            eta_text = format_eta(payload.get("eta_seconds"))
            self.eta_label.setText(f"Remaining Time: {eta_text}" if eta_text else "")
            self.eta_label.setVisible(bool(eta_text))
            raw_value = payload.get("percent")
            if raw_value is None:
                raw_value = payload.get("progress")
            if raw_value is None and payload.get("current") is not None and payload.get("total") is not None:
                try:
                    total = int(payload.get("total") or 0)
                    current = int(payload.get("current") or 0)
                    raw_value = int(round((current / total) * 100)) if total > 0 else None
                except (TypeError, ValueError, ZeroDivisionError):
                    raw_value = None
            try:
                value = int(raw_value) if raw_value is not None else None
            except (TypeError, ValueError):
                value = None
        else:
            text = str(payload or "")

        if text:
            if phase and self._status_repeats_phase(text, phase):
                self.detail_label.setText("")
                self.detail_label.setVisible(False)
            else:
                self.detail_label.setText(text)
                self.detail_label.setVisible(True)

        if value is None or value < 0:
            self._progress_animation.stop()
            self.progress.setRange(0, 0)
            self.progress.setFormat("")
        else:
            self.progress.setRange(0, 100)
            self._set_progress_value(value, snap=phase_changed)
            self.progress.setFormat("%p%")

    def finish(self, text: str | None = None) -> None:
        self._active = False
        self._progress_animation.stop()
        if text:
            self.detail_label.setText(text)
            self.detail_label.setVisible(True)
        self.accept()

    def fail(self, message: str) -> None:
        self._active = False
        self._progress_animation.stop()
        self.detail_label.setText(message or "Operation failed.")
        self.detail_label.setVisible(True)
        self.reject()

    @staticmethod
    def _status_repeats_phase(text: str, phase: str) -> bool:
        def normalize(value: str) -> str:
            return "".join(char.lower() for char in value if char.isalnum())

        return bool(normalize(phase) and normalize(text) == normalize(phase))

    def _should_defer_phase_change(self) -> bool:
        return (
            bool(self._last_phase)
            and not self._applying_deferred_phase
            and self.progress.maximum() == 100
            and self.progress.value() < 100
        )

    def _defer_phase_change(self, payload: dict) -> None:
        self._phase_transition_id += 1
        transition_id = self._phase_transition_id
        current = max(0, self.progress.value())
        duration = max(80, (100 - current) * 6)
        self._set_progress_value(100, min_duration=80, ms_per_percent=6)

        def _apply_deferred() -> None:
            if transition_id != self._phase_transition_id or not self._active:
                return
            self._applying_deferred_phase = True
            try:
                self.update_progress(payload)
            finally:
                self._applying_deferred_phase = False

        QTimer.singleShot(duration, _apply_deferred)

    def _set_progress_value(
        self,
        value: int,
        *,
        snap: bool = False,
        min_duration: int = 120,
        ms_per_percent: int = 12,
    ) -> None:
        target = max(0, min(100, int(value)))
        current = self.progress.value()
        if snap or current < 0 or target <= current:
            self._progress_animation.stop()
            self.progress.setValue(target)
            return
        self._progress_animation.stop()
        self._progress_animation.setStartValue(current)
        self._progress_animation.setEndValue(target)
        self._progress_animation.setDuration(max(min_duration, (target - current) * ms_per_percent))
        self._progress_animation.start()

    def _on_cancel(self) -> None:
        logging.info("Operation monitor Cancel clicked for %s.", self.windowTitle() or "operation")
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.setText("Stopping")
        if self._cancel_handler is not None:
            self._cancel_handler()

    def closeEvent(self, event) -> None:
        if self._active:
            event.ignore()
            return
        super().closeEvent(event)

    def refresh_theme(self) -> None:
        apply_style(
            self,
            f"""
            QDialog {{
                background: {ColorPalette.BG_DARK};
                color: {ColorPalette.TEXT_LIGHT};
                border: none;
            }}
            QLabel {{
                background: transparent;
                border: none;
            }}
            QLabel#OperationMonitorTitle {{
                color: {ColorPalette.TEXT_LIGHT};
                font-weight: 700;
            }}
            QLabel#OperationMonitorPhase {{
                color: {ColorPalette.TEXT_LIGHT};
                font-weight: 600;
            }}
            QLabel#OperationMonitorEta {{
                color: {ColorPalette.TEXT_DIM};
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
                color: {ColorPalette.TEXT_LIGHT};
                text-align: center;
                font-weight: 700;
            }}
            QProgressBar::chunk {{
                background: {ColorPalette.PRIMARY};
                border-radius: 3px;
            }}
            """
        )
        apply_style(self.detail_label, f"color: {ColorPalette.TEXT_MUTED};")
