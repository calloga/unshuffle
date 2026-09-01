from __future__ import annotations

from datetime import datetime
from pathlib import PurePosixPath, PureWindowsPath

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..utils.app_icon import apply_app_icon
from ..utils.layout_helpers import apply_layout_margins, apply_layout_spacing
from ..utils.styles import scaled_px


def format_session_timestamp(value: object) -> str:
    """Return a compact, human-readable session timestamp."""
    timestamp = str(value or "").strip()
    if not timestamp:
        return "Date unavailable"
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return timestamp
    hour = parsed.strftime("%I").lstrip("0") or "0"
    return f"{parsed.strftime('%b')} {parsed.day}, {parsed.year} at {hour}:{parsed.strftime('%M %p')}"


def source_folder_name(source: object) -> str:
    """Return the final folder component for either Windows or POSIX paths."""
    value = str(source or "").strip().rstrip("/\\")
    if not value:
        return "Unknown source"
    path = (
        PureWindowsPath(value)
        if "\\" in value or (len(value) > 1 and value[1] == ":")
        else PurePosixPath(value)
    )
    return path.name or path.drive or value


def session_display_name(sources: list[str], fallback_source: object = None) -> str:
    effective_sources = [str(source) for source in sources if str(source or "").strip()]
    if not effective_sources and str(fallback_source or "").strip():
        effective_sources = [str(fallback_source)]
    if not effective_sources:
        return "Imported session"
    name = source_folder_name(effective_sources[0])
    if len(effective_sources) == 1:
        return name
    return f"{name} + {len(effective_sources) - 1} more"


class SessionImportDialog(QDialog):
    """Friendly selector for a sidecar that contains multiple sessions."""

    _COLLAPSED_SOURCE_LIMIT = 4

    def __init__(self, choices: list[dict], parent: QWidget | None = None):
        super().__init__(parent)
        self._choices = choices
        self._show_all_sources = False

        self.setObjectName("SessionImportDialog")
        self.setWindowTitle("Import Session")
        self.setModal(True)
        self.setMinimumWidth(scaled_px(540))
        apply_app_icon(self)

        root = QVBoxLayout(self)
        apply_layout_margins(root, (scaled_px(16), scaled_px(16), scaled_px(16), scaled_px(14)))
        apply_layout_spacing(root, scaled_px(10))

        heading = QLabel("Choose a session to import")
        heading.setObjectName("DialogHeading")
        root.addWidget(heading)

        explanation = QLabel(
            f"Found {len(choices)} importable sessions. Select the one you want to restore."
        )
        explanation.setWordWrap(True)
        root.addWidget(explanation)

        self.session_list = QListWidget()
        self.session_list.setObjectName("SessionImportList")
        self.session_list.setAccessibleName("Sessions available to import")
        for index, choice in enumerate(choices):
            session = choice["session"]
            sources = choice.get("sources", [])
            record_count = int(choice.get("record_count") or 0)
            file_label = "file" if record_count == 1 else "files"
            import_kind = (
                "CSV import"
                if str(session.get("session_id") or "").startswith("csv_")
                else "Staging session"
            )
            title = session_display_name(sources, session.get("source_path"))
            subtitle = (
                f"{import_kind}  |  {record_count:,} {file_label}  |  "
                f"{format_session_timestamp(session.get('timestamp'))}"
            )
            item = QListWidgetItem(f"{title}\n{subtitle}")
            item.setData(Qt.UserRole, index)
            item.setSizeHint(QSize(0, scaled_px(54)))
            self.session_list.addItem(item)
        root.addWidget(self.session_list)

        self.sources_heading = QLabel()
        self.sources_heading.setObjectName("SessionSourcesHeading")
        root.addWidget(self.sources_heading)

        self.sources_label = QLabel()
        self.sources_label.setObjectName("SessionSources")
        self.sources_label.setWordWrap(True)
        self.sources_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        root.addWidget(self.sources_label)

        self.sources_toggle = QPushButton()
        self.sources_toggle.setFlat(True)
        self.sources_toggle.clicked.connect(self._toggle_sources)
        root.addWidget(self.sources_toggle, 0, Qt.AlignLeft)

        buttons = QDialogButtonBox()
        self.import_button = buttons.addButton("Import", QDialogButtonBox.AcceptRole)
        self.cancel_button = buttons.addButton("Cancel", QDialogButtonBox.RejectRole)
        self.import_button.setDefault(True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self.session_list.currentRowChanged.connect(self._session_changed)
        self.session_list.itemDoubleClicked.connect(lambda _item: self.accept())
        if choices:
            self.session_list.setCurrentRow(0)
        else:
            self.import_button.setEnabled(False)
            self._session_changed(-1)

    def selected_session(self) -> dict | None:
        item = self.session_list.currentItem()
        if item is None:
            return None
        index = int(item.data(Qt.UserRole))
        if 0 <= index < len(self._choices):
            return self._choices[index]["session"]
        return None

    def _selected_sources(self) -> list[str]:
        item = self.session_list.currentItem()
        if item is None:
            return []
        index = int(item.data(Qt.UserRole))
        if not 0 <= index < len(self._choices):
            return []
        choice = self._choices[index]
        sources = [
            str(source)
            for source in choice.get("sources", [])
            if str(source or "").strip()
        ]
        if not sources:
            fallback = str(choice["session"].get("source_path") or "").strip()
            if fallback:
                sources = [fallback]
        return sources

    def _session_changed(self, _row: int) -> None:
        self._show_all_sources = False
        self._refresh_sources()

    def _toggle_sources(self) -> None:
        self._show_all_sources = not self._show_all_sources
        self._refresh_sources()

    def _refresh_sources(self) -> None:
        sources = self._selected_sources()
        count = len(sources)
        heading = "Source folder" if count == 1 else f"Source folders ({count})"
        self.sources_heading.setText(heading)

        visible_sources = sources
        hidden_count = 0
        if not self._show_all_sources and count > self._COLLAPSED_SOURCE_LIMIT:
            visible_sources = sources[: self._COLLAPSED_SOURCE_LIMIT]
            hidden_count = count - len(visible_sources)
        source_lines = visible_sources or ["No linked source folders"]
        if hidden_count:
            source_lines.append(f"and {hidden_count} more...")
        self.sources_label.setText("\n".join(source_lines))

        can_toggle = count > self._COLLAPSED_SOURCE_LIMIT
        self.sources_toggle.setVisible(can_toggle)
        self.sources_toggle.setText(
            "Show fewer" if self._show_all_sources else "Show all source folders"
        )
