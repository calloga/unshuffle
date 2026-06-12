from __future__ import annotations

import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..utils.constants import (
    LIB_TAB_CONTENT_ZERO_MARGINS,
    SIDEBAR_HEADER_HEIGHT,
    WORKSPACE_CARD_MARGINS,
    WORKSPACE_ROOT_MARGINS,
    WORKSPACE_ROOT_SPACING,
)
from ..utils.history import load_executed_sessions
from ..utils.layout_helpers import apply_layout_margins, apply_layout_spacing
from ..utils.styles import (
    ColorPalette,
    apply_style,
    button_style,
    scaled_px,
    sidebar_header_style,
    sidebar_title_style,
    workspace_card_style,
    workspace_primary_button_style,
    workspace_table_widget_style,
)
from ..utils.widget_helpers import apply_fixed_height


class HistoryPage(QWidget):
    """Full-page migration history surface."""

    undoRequested = Signal(dict)
    clearRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._sessions: list[dict] = []
        self._retry_sessions: set[str] = set()
        self._setup_ui()

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        apply_layout_margins(root, WORKSPACE_ROOT_MARGINS)
        apply_layout_spacing(root, WORKSPACE_ROOT_SPACING)

        self.card = QFrame()
        self.card.setObjectName("WorkspaceCard")
        apply_style(self.card, workspace_card_style())
        root.addWidget(self.card, 1)

        layout = QVBoxLayout(self.card)
        apply_layout_margins(layout, WORKSPACE_CARD_MARGINS)
        apply_layout_spacing(layout, WORKSPACE_ROOT_SPACING)

        title_row = QHBoxLayout()
        apply_layout_margins(title_row, LIB_TAB_CONTENT_ZERO_MARGINS)
        self.header = self._section_header("History")
        title_row.addWidget(self.header, 1)
        self.btn_clear = QPushButton("Clear History")
        self.btn_clear.clicked.connect(self.clearRequested.emit)
        title_row.addWidget(self.btn_clear)
        layout.addLayout(title_row)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Date", "Mode", "Files", "Source", "Target", "Action"])
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(scaled_px(48))
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.itemDoubleClicked.connect(lambda _item: self._emit_selected_undo())
        self.table.horizontalHeader().setStretchLastSection(True)
        for idx in range(6):
            self.table.horizontalHeader().setSectionResizeMode(idx, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeToContents)
        layout.addWidget(self.table, 1)

        self.refresh_theme()

    def _section_header(self, title: str) -> QWidget:
        header = QWidget()
        apply_fixed_height(header, SIDEBAR_HEADER_HEIGHT)
        apply_style(header, sidebar_header_style())
        layout = QHBoxLayout(header)
        apply_layout_margins(layout, LIB_TAB_CONTENT_ZERO_MARGINS)
        label = QLabel(title)
        apply_style(label, sidebar_title_style())
        layout.addWidget(label, 1)
        self.header_label = label
        return header

    def refresh_from_target(self, target: str) -> None:
        target = (target or "").strip()
        if not target:
            self._set_sessions([], "Select a target library to view history.")
            return
        try:
            sessions = load_executed_sessions(target, limit=100)
        except Exception:
            logging.exception("Failed to load history page for target %s", target)
            self._set_sessions([], "History could not be loaded for this library.")
            return
        if not sessions:
            self._set_sessions([], "No migration history yet.")
            return
        self._set_sessions(sessions, "")

    def _set_sessions(self, sessions: list[dict], message: str) -> None:
        self._sessions = list(sessions or [])
        self.status.setText(message)
        self.status.setVisible(bool(message))
        self.table.setRowCount(len(self._sessions))
        for row, session in enumerate(self._sessions):
            timestamp = str(session.get("timestamp") or "")
            date_text = timestamp.split("T")[0] if "T" in timestamp else timestamp
            values = (
                date_text,
                str(session.get("mode") or "").title(),
                str(session.get("file_count") or 0),
                str(session.get("source_path") or ""),
                str(session.get("target_root") or ""),
                "",
            )
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                if col == 0:
                    item.setData(Qt.UserRole, row)
                self.table.setItem(row, col, item)
            self._set_action_button(row, session)
        self.btn_clear.setEnabled(bool(self._sessions))

    def _set_action_button(self, row: int, session: dict) -> None:
        session_id = str(session.get("session_id") or "")
        state = self._action_state(session_id, session)
        label = {"undo": "Undo", "retry": "Retry Undo", "undone": "Undone"}.get(state, "Undo")
        cell = QWidget()
        cell.setObjectName("HistoryActionCell")
        cell.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        cell_layout = QHBoxLayout(cell)
        apply_layout_margins(cell_layout, (scaled_px(6), scaled_px(8), scaled_px(6), scaled_px(8)))
        apply_layout_spacing(cell_layout, 0)

        button = QPushButton(label)
        button.setObjectName("HistoryActionButton")
        button.setProperty("historyAction", state)
        button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        button.setFixedHeight(scaled_px(26))
        button.setEnabled(state in {"undo", "retry"})
        if button.isEnabled():
            button.clicked.connect(lambda _checked=False, row_index=row: self._emit_row_undo(row_index))
        apply_style(button, history_action_button_style())
        cell_layout.addWidget(button, 1, Qt.AlignVCenter)
        self.table.setCellWidget(row, 5, cell)
        self.table.setRowHeight(row, scaled_px(48))

    def _action_state(self, session_id: str, session: dict | None = None) -> str:
        if session_id in self._retry_sessions:
            return "retry"
        if session and str(session.get("history_state") or "").lower() == "undone":
            return "undone"
        return "undo"

    def _emit_row_undo(self, row: int) -> None:
        if not (0 <= row < len(self._sessions)):
            return
        session = self._sessions[row]
        if self._action_state(str(session.get("session_id") or ""), session) not in {"undo", "retry"}:
            return
        self.undoRequested.emit(dict(session))

    def _emit_selected_undo(self) -> None:
        self._emit_row_undo(self.table.currentRow())

    def mark_retryable(self, session_id: str) -> None:
        session_id = str(session_id or "")
        if not session_id:
            return
        self._retry_sessions.add(session_id)
        for row, session in enumerate(self._sessions):
            if str(session.get("session_id") or "") == session_id:
                self._set_action_button(row, session)
                break

    def mark_undone(self, session_id: str) -> None:
        session_id = str(session_id or "")
        if not session_id:
            return
        self._retry_sessions.discard(session_id)
        for row, session in enumerate(self._sessions):
            if str(session.get("session_id") or "") == session_id:
                session["history_state"] = "undone"
                self._set_action_button(row, session)
                break

    def refresh_theme(self) -> None:
        apply_style(self.card, workspace_card_style())
        apply_style(self.header, sidebar_header_style())
        apply_style(self.header_label, sidebar_title_style())
        apply_style(self.table, workspace_table_widget_style())
        for button in (self.btn_clear,):
            apply_style(button, workspace_primary_button_style())
        for row in range(self.table.rowCount()):
            button = self._action_button_at(row)
            if button is not None:
                apply_style(button, history_action_button_style())

    def _action_button_at(self, row: int) -> QPushButton | None:
        widget = self.table.cellWidget(row, 5)
        if isinstance(widget, QPushButton):
            return widget
        if widget is None:
            return None
        return widget.findChild(QPushButton, "HistoryActionButton")


def history_action_button_style() -> str:
    return (
        f"{button_style('warning', size='cell')}"
        f"QPushButton {{ background: {ColorPalette.WARNING}; color: {ColorPalette.TEXT_INVERSE}; }}"
        f"QPushButton:hover {{ background: {ColorPalette.PRIMARY_HOVER}; }}"
        f"QPushButton[historyAction=\"retry\"] {{ background: {ColorPalette.DANGER}; color: {ColorPalette.TEXT_INVERSE}; }}"
        f"QPushButton[historyAction=\"retry\"]:hover {{ background: {ColorPalette.DANGER_HOVER}; }}"
        f"QPushButton[historyAction=\"undone\"] {{ background: {ColorPalette.BG_HOVER}; color: {ColorPalette.TEXT_DIM}; }}"
    )
