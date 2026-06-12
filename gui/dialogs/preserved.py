from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, 
    QPushButton, QFileDialog, QDialogButtonBox, QMessageBox
)
from PySide6.QtCore import Qt
from pathlib import Path
from ..utils.constants import (
    PRESERVED_BOTTOM_SPACING,
    PRESERVED_DIALOG_MIN_WIDTH,
    PRESERVED_LAYOUT_SPACING,
    PRESERVED_PATH_SPACING,
)
from ..utils.layout_helpers import apply_layout_spacing
from ..utils.styles import apply_style, build_dialog_base_style, build_dialog_input_style, preserved_ok_button_style
from ..utils.widget_helpers import apply_minimum_width
from unshuffle.core.path_safety import is_path_within_directory

class PreservedDialog(QDialog):
    """
    Dialog to confirm and select the root folder for a Preserved operation.
    """
    def __init__(self, initial_path: Path, parent=None, source_roots: list[Path] | None = None):
        super().__init__(parent)
        self.setWindowTitle("Mark Folder as Preserved")
        apply_minimum_width(self, PRESERVED_DIALOG_MIN_WIDTH)
        self.selected_path = initial_path
        self.source_roots = [Path(root) for root in (source_roots or [])]
        apply_style(self, build_dialog_base_style())
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        apply_layout_spacing(layout, PRESERVED_LAYOUT_SPACING)

        info_label = QLabel(
            "<b>Preserve Folder Structure</b><br><br>"
            "Unshuffle will treat this folder as a single unit. It will move the entire directory "
            "as is to your library, preserving all internal subfolders and filenames."
        )
        info_label.setWordWrap(True)
        layout.addWidget(info_label)

        path_group = QVBoxLayout()
        apply_layout_spacing(path_group, PRESERVED_PATH_SPACING)
        path_group.addWidget(QLabel("Target Folder to Preserve:"))
        
        path_hbox = QHBoxLayout()
        self.path_edit = QLineEdit(str(self.selected_path))
        self.path_edit.setReadOnly(True)
        apply_style(self.path_edit, build_dialog_input_style())
        path_hbox.addWidget(self.path_edit)
        
        btn_browse = QPushButton("Browse...")
        btn_browse.clicked.connect(self._on_browse)
        path_hbox.addWidget(btn_browse)
        path_group.addLayout(path_hbox)
        layout.addLayout(path_group)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel,
            Qt.Horizontal, self
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        
        ok_btn = buttons.button(QDialogButtonBox.Ok)
        ok_btn.setText("Mark as Preserved")
        apply_style(ok_btn, preserved_ok_button_style())
        
        layout.addSpacing(PRESERVED_BOTTOM_SPACING)
        layout.addWidget(buttons)

    def _on_browse(self):
        dir_path = QFileDialog.getExistingDirectory(
            self, "Select Folder to Preserve", str(self.selected_path)
        )
        if dir_path:
            candidate = Path(dir_path)
            if self._path_allowed(candidate):
                self.selected_path = candidate
                self.path_edit.setText(dir_path)
            else:
                self._warn_outside_roots()

    def accept(self):
        if not self._path_allowed(self.selected_path):
            self._warn_outside_roots()
            return
        super().accept()

    def get_path(self) -> Path:
        return self.selected_path

    def _path_allowed(self, path: Path) -> bool:
        if not self.source_roots:
            return True
        return any(is_path_within_directory(path, root) for root in self.source_roots)

    def _warn_outside_roots(self) -> None:
        QMessageBox.warning(
            self,
            "Preserve Folder Outside Sources",
            "Choose a folder inside the current source directory list.",
        )
