from collections.abc import Sequence

from PySide6.QtWidgets import QFrame, QVBoxLayout, QHBoxLayout, QPushButton, QSizePolicy, QMenu
from PySide6.QtCore import Qt, QSize, Signal, QEvent
from PySide6.QtGui import QAction
from .buttons import SidebarIconButton
from ..utils.constants import (
    CAROUSEL_ARROW_ICON_SIZE,
    CAROUSEL_BUTTON_HEIGHT,
    CAROUSEL_HEADER_SPACING,
    CAROUSEL_ICON_BUTTON_WIDTH,
    CAROUSEL_ICON_HITBOX_WIDTH,
    CAROUSEL_LAYOUT_SPACING,
    CAROUSEL_ROW_HEIGHT,
    LIB_TAB_CONTENT_ZERO_MARGINS,
    SIDEBAR_INNER_CONTENT_WIDTH,
)
from ..utils.styles import (
    ColorPalette,
    apply_style,
    carousel_frame_style,
    carousel_title_style,
    carousel_value_style,
    frame_plain_style,
    make_qcolor,
)
from ..utils.layout_helpers import apply_layout_margins, apply_layout_spacing
from ..utils.widget_helpers import apply_fixed_height, apply_fixed_size

class SidebarCarousel(QFrame):
    """
    A horizontal selection control that cycles through a list of options.
    Used for Categories and Sorting in the sidebar.
    """
    activeChanged = Signal(object, bool)
    valueSelected = Signal(object)

    def __init__(
        self,
        title: str,
        options: Sequence[tuple[str, object]],
        parent=None,
        inactive_text: str = "",
        compact: bool = False,
        toggleable: bool = True,
    ):
        super().__init__(parent)
        self.options = list(options)
        self.current_index = 0
        self.active_values = set()
        self.is_active = False
        self.inactive_text = (inactive_text or "")
        self.compact = compact
        self.toggleable = toggleable
        apply_style(self, frame_plain_style())

        layout = QHBoxLayout(self) if self.compact else QVBoxLayout(self)
        apply_layout_margins(layout, LIB_TAB_CONTENT_ZERO_MARGINS)
        apply_layout_spacing(layout, CAROUSEL_LAYOUT_SPACING)

        header = QHBoxLayout()
        apply_layout_margins(header, LIB_TAB_CONTENT_ZERO_MARGINS)
        apply_layout_spacing(header, CAROUSEL_HEADER_SPACING)
        self.btn_title = QPushButton(title.upper())
        self.btn_title.setCheckable(self.toggleable)
        if self.toggleable:
            self.btn_title.setCursor(Qt.PointingHandCursor)
            self.btn_title.clicked.connect(self._on_title_toggled)
        header.addWidget(self.btn_title, 1)
        if self.compact:
            layout.addWidget(self.btn_title, 0)
        else:
            layout.addLayout(header)

        self.value_row = QFrame()
        apply_fixed_height(self.value_row, CAROUSEL_ROW_HEIGHT)
        self.value_row.setFixedWidth(SIDEBAR_INNER_CONTENT_WIDTH)
        self.value_row.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        apply_style(self.value_row, carousel_frame_style())
        
        arrow_size = QSize(CAROUSEL_ARROW_ICON_SIZE, CAROUSEL_ARROW_ICON_SIZE)
        self.btn_prev = SidebarIconButton("icons/left.png", arrow_size, QSize(CAROUSEL_ICON_HITBOX_WIDTH, CAROUSEL_ROW_HEIGHT), checkable=False)
        self.btn_prev.setParent(self.value_row)
        self.btn_next = SidebarIconButton("icons/right.png", arrow_size, QSize(CAROUSEL_ICON_HITBOX_WIDTH, CAROUSEL_ROW_HEIGHT), checkable=False)
        self.btn_next.setParent(self.value_row)

        transparent = make_qcolor(ColorPalette.TRANSPARENT)
        self.btn_prev._overlay = transparent
        self.btn_next._overlay = transparent

        self.btn_value = QPushButton("")
        self.btn_value.setParent(self.value_row)
        self.value_row.installEventFilter(self)
        
        for btn in (self.btn_prev, self.btn_next):
            apply_fixed_size(btn, CAROUSEL_ICON_BUTTON_WIDTH, CAROUSEL_BUTTON_HEIGHT)
            btn.setCursor(Qt.PointingHandCursor)
        
        apply_fixed_height(self.btn_value, CAROUSEL_BUTTON_HEIGHT)
        self.btn_value.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.btn_value.setCursor(Qt.PointingHandCursor)
        self.btn_value.installEventFilter(self)
        
        self.btn_prev.clicked.connect(lambda checked=False: self._move(-1))
        self.btn_next.clicked.connect(lambda checked=False: self._move(1))
        layout.addWidget(self.value_row)
        if self.compact:
            self.value_row.setMinimumWidth(160)
            self.value_row.setMaximumWidth(220)
        self._refresh()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._position_overlay_buttons()

    def _position_overlay_buttons(self):
        if hasattr(self, "value_row"):
            width = self.value_row.width()
            self.btn_value.setGeometry(0, 0, width, CAROUSEL_ROW_HEIGHT)
            self.btn_prev.setGeometry(0, 0, CAROUSEL_ICON_HITBOX_WIDTH, CAROUSEL_ROW_HEIGHT)
            self.btn_next.setGeometry(max(0, width - CAROUSEL_ICON_HITBOX_WIDTH), 0, CAROUSEL_ICON_HITBOX_WIDTH, CAROUSEL_ROW_HEIGHT)
            self.btn_value.lower()
            self.btn_prev.raise_()
            self.btn_next.raise_()

    def eventFilter(self, obj, event):
        if obj == self.value_row and event.type() == QEvent.Resize:
            self._position_overlay_buttons()
        if hasattr(self, "btn_value") and obj == self.btn_value and event.type() == QEvent.MouseButtonDblClick:
            self._show_menu()
            return True
        return super().eventFilter(obj, event)

    def _set_section_active_visual(self):
        if self.toggleable and self.is_active:
            apply_style(self.btn_title, carousel_title_style(True))
        else:
            apply_style(self.btn_title, carousel_title_style(False))
        apply_style(self.btn_value, carousel_value_style(self.is_active if self.toggleable else True))

    def _refresh(self):
        if not self.options:
            self.btn_value.setText(self.inactive_text or "No options")
            self.btn_prev.setEnabled(False)
            self.btn_next.setEnabled(False)
            self._set_section_active_visual()
            return
        name, value = self.options[self.current_index]
        self.btn_value.setText(self.inactive_text if (not self.is_active and self.inactive_text) else name)
        self._set_section_active_visual()
        self.btn_prev.setEnabled(len(self.options) > 1)
        self.btn_next.setEnabled(len(self.options) > 1)

    def _move(self, delta):
        if not self.options:
            return
        self.current_index = (self.current_index + delta) % len(self.options)
        self._refresh()
        _, value = self.options[self.current_index]
        self.valueSelected.emit(value)
        if self.is_active:
            self.active_values = {value}
            self.activeChanged.emit(value, True)

    def _show_menu(self):
        if not self.options:
            return
        menu = QMenu(self.window())
        for i, (name, value) in enumerate(self.options):
            act = QAction(name, self)
            act.setCheckable(True)
            act.setChecked(i == self.current_index)
            act.triggered.connect(lambda checked=False, idx=i: self._select_index(idx))
            menu.addAction(act)
        menu.exec(self.mapToGlobal(self.value_row.pos()) + self.value_row.rect().bottomLeft())

    def _select_index(self, idx):
        if not (0 <= idx < len(self.options)):
            return
        self.current_index = idx
        self._refresh()
        _, value = self.options[self.current_index]
        self.valueSelected.emit(value)
        if self.is_active:
            self.active_values = {value}
            self.activeChanged.emit(value, True)

    def _on_title_toggled(self, checked):
        if not self.toggleable:
            return
        self.is_active = bool(checked)
        self._refresh()
        if not self.options:
            return
        _, value = self.options[self.current_index]
        if checked:
            self.active_values = {value}
            self.activeChanged.emit(value, True)
        else:
            self.active_values = set()
            self.activeChanged.emit(value, False)

    def set_active_values(self, values: set):
        if not self.toggleable:
            return
        values = set(values or [])
        self.active_values = values
        if not self.options:
            self.is_active = False
            self.btn_title.blockSignals(True)
            self.btn_title.setChecked(False)
            self.btn_title.blockSignals(False)
            self._refresh()
            return
        selected_index = next((i for i, (_, value) in enumerate(self.options) if value in values), self.current_index)
        self.current_index = selected_index
        self.is_active = bool(values)
        self.btn_title.blockSignals(True)
        self.btn_title.setChecked(self.is_active)
        self.btn_title.blockSignals(False)
        self._refresh()

    def set_current_value(self, value):
        if not self.options:
            return
        if isinstance(value, int) and 0 <= value < len(self.options):
            self.current_index = value
            self._refresh()
            return
        for i, (_, option_value) in enumerate(self.options):
            if option_value == value:
                self.current_index = i
                self._refresh()
                return

    def set_options(self, options: Sequence[tuple[str, object]]):
        current_value = None
        if self.options and 0 <= self.current_index < len(self.options):
            current_value = self.options[self.current_index][1]
        self.options = list(options)
        if current_value is not None:
            self.current_index = next(
                (idx for idx, (_name, value) in enumerate(self.options) if value == current_value),
                0,
            )
        elif self.current_index >= len(self.options):
            self.current_index = 0
        self._refresh()

    def refresh_theme(self) -> None:
        apply_style(self.value_row, carousel_frame_style())
        self._set_section_active_visual()
