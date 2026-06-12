from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QPushButton, QScrollArea, QVBoxLayout, QWidget

from ..utils.constants import (
    LIB_TAB_CONTENT_ZERO_MARGINS,
    SECTION_CONTENT_BOTTOM_MARGIN,
    SECTION_CONTENT_TOP_MARGIN,
    SECTION_INNER_SPACING,
)
from ..utils.styles import apply_style, section_scroll_style, section_toggle_style
from ..utils.layout_helpers import apply_layout_margins, apply_layout_spacing


class CollapsibleSection(QWidget):
    """
    A vertical container with a toggle button that hides/shows its content.
    Optionally includes a scroll area for long lists.
    """

    def __init__(self, title, parent=None, use_scroll=True, icon_below: bool = False):
        super().__init__(parent)
        self.is_expanded = False
        self._title = str(title)
        self.use_scroll = bool(use_scroll)
        self.icon_below = icon_below

        layout = QVBoxLayout(self)
        apply_layout_margins(layout, LIB_TAB_CONTENT_ZERO_MARGINS)
        apply_layout_spacing(layout, LIB_TAB_CONTENT_ZERO_MARGINS[0])

        self.btn = QPushButton(self._button_text())
        self.btn.setProperty("iconBelow", self.icon_below)
        self.btn.setCheckable(True)
        self.btn.setCursor(Qt.PointingHandCursor)
        apply_style(self.btn, section_toggle_style())
        self.btn.clicked.connect(self._toggle)

        self.content = QWidget()
        self.content_shell_layout = QVBoxLayout(self.content)
        apply_layout_margins(
            self.content_shell_layout,
            (
                LIB_TAB_CONTENT_ZERO_MARGINS[0],
                SECTION_CONTENT_TOP_MARGIN,
                LIB_TAB_CONTENT_ZERO_MARGINS[0],
                SECTION_CONTENT_BOTTOM_MARGIN,
            ),
        )
        apply_layout_spacing(self.content_shell_layout, LIB_TAB_CONTENT_ZERO_MARGINS[0])

        self.content_inner = QWidget()
        self.content_layout = QVBoxLayout(self.content_inner)
        apply_layout_margins(self.content_layout, LIB_TAB_CONTENT_ZERO_MARGINS)
        apply_layout_spacing(self.content_layout, SECTION_INNER_SPACING)

        if self.use_scroll:
            self.content_scroll = QScrollArea()
            self.content_scroll.setWidgetResizable(True)
            self.content_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            self.content_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
            self.content_scroll.setFrameShape(QFrame.NoFrame)
            apply_style(self.content_scroll, section_scroll_style())
            self.content_scroll.setWidget(self.content_inner)
            self.content_shell_layout.addWidget(self.content_scroll)
        else:
            self.content_shell_layout.addWidget(self.content_inner)
        self.content.setVisible(False)

        layout.addWidget(self.btn)
        layout.addWidget(self.content, 1)

    def _toggle(self):
        self.is_expanded = not self.is_expanded
        self.btn.setText(self._button_text())
        self.content.setVisible(self.is_expanded)

    def _button_text(self) -> str:
        if self.icon_below:
            return f"{'▴' if self.is_expanded else '▾'}\n{self._title}"
        return f"{'▾' if self.is_expanded else '▸'} {self._title}"

    def set_expanded(self, expanded: bool):
        if self.is_expanded == expanded:
            return
        self._toggle()

    def refresh_theme(self) -> None:
        apply_style(self.btn, section_toggle_style())
        if self.use_scroll and hasattr(self, "content_scroll"):
            apply_style(self.content_scroll, section_scroll_style())

    def add_content_widget(self, widget: QWidget) -> None:
        self.content_layout.addWidget(widget)
