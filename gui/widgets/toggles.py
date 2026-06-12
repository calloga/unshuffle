from PySide6.QtCore import QSize, Signal
from PySide6.QtWidgets import QButtonGroup, QFrame, QHBoxLayout, QPushButton

from gui.utils.constants import (
    TYPE_TOGGLE_BOX_MARGINS,
    TYPE_TOGGLE_BOX_SPACING,
    TYPE_TOGGLE_BUTTON_HEIGHT,
    TYPE_TOGGLE_BUTTON_WIDTH,
)
from gui.utils.styles import (
    apply_style,
    scaled_px,
    type_toggle_box_style,
    type_toggle_button_style,
)
from gui.utils.layout_helpers import apply_layout_margins, apply_layout_spacing
from gui.utils.widget_helpers import apply_fixed_size_q


class TypeToggle(QFrame):
    """
    Compact toggle group for Oneshots, Loops, and All files.
    Emits typeChanged(oneshots, loops, all_files) when selection changes.
    """

    typeChanged = Signal(bool, bool, bool)

    BOX_MARGINS = TYPE_TOGGLE_BOX_MARGINS
    BOX_SPACING = TYPE_TOGGLE_BOX_SPACING
    BUTTON_SIZE = QSize(TYPE_TOGGLE_BUTTON_WIDTH, TYPE_TOGGLE_BUTTON_HEIGHT)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("TypeToggleBox")
        apply_style(self, type_toggle_box_style())

        layout = QHBoxLayout(self)
        apply_layout_margins(layout, self.BOX_MARGINS)
        apply_layout_spacing(layout, self.BOX_SPACING)

        self.btn_oneshots = QPushButton("I")
        self.btn_oneshots.setCheckable(True)
        apply_fixed_size_q(self.btn_oneshots, self.BUTTON_SIZE)

        self.btn_loops = QPushButton("\u221e")
        self.btn_loops.setCheckable(True)
        apply_fixed_size_q(self.btn_loops, self.BUTTON_SIZE)

        self.btn_all = QPushButton("All")
        self.btn_all.setCheckable(True)
        self.btn_all.setChecked(True)
        apply_fixed_size_q(self.btn_all, self.BUTTON_SIZE)

        self.group = QButtonGroup(self)
        self.group.addButton(self.btn_oneshots)
        self.group.addButton(self.btn_loops)
        self.group.addButton(self.btn_all)
        self.group.buttonClicked.connect(self._emit_change)

        layout.addWidget(self.btn_oneshots)
        layout.addWidget(self.btn_loops)
        layout.addWidget(self.btn_all)
        self.refresh_theme()

    def _emit_change(self, _btn=None):
        self.typeChanged.emit(
            self.btn_oneshots.isChecked(),
            self.btn_loops.isChecked(),
            self.btn_all.isChecked(),
        )

    def set_state(self, oneshots, loops, all_files):
        self.group.blockSignals(True)
        self.btn_oneshots.setChecked(oneshots)
        self.btn_loops.setChecked(loops)
        self.btn_all.setChecked(all_files)
        self.group.blockSignals(False)

    def get_state(self):
        return (
            self.btn_oneshots.isChecked(),
            self.btn_loops.isChecked(),
            self.btn_all.isChecked(),
        )

    def refresh_theme(self) -> None:
        apply_style(self, type_toggle_box_style())
        apply_style(self.btn_oneshots, type_toggle_button_style(12, bold=True))
        apply_style(self.btn_loops, type_toggle_button_style(16, bold=False))
        apply_style(self.btn_all, type_toggle_button_style(11, bold=True))
        size = QSize(scaled_px(TYPE_TOGGLE_BUTTON_WIDTH), scaled_px(TYPE_TOGGLE_BUTTON_HEIGHT))
        apply_fixed_size_q(self.btn_oneshots, size)
        apply_fixed_size_q(self.btn_loops, size)
        apply_fixed_size_q(self.btn_all, size)
