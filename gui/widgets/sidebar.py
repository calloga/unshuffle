from pathlib import Path
from PySide6.QtCore import Qt, QSize, QTimer, Signal
from PySide6.QtGui import QPainter, QColor, QPixmap, QIcon
from PySide6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QAbstractScrollArea, QScrollArea, QScrollBar, QWidget, QMenu, QSizePolicy
)
import shiboken6

from unshuffle.core.assets import asset_path
from .sliders import ModernRangeSlider
from .sections import CollapsibleSection
from .labels import section_label
from ..utils.styles import (
    ColorPalette,
    apply_style,
    frame_plain_style,
    sidebar_content_style,
    make_qcolor,
    section_label_style,
    sidebar_title_style,
    sidebar_active_item_style,
    sidebar_add_button_style,
    sidebar_base_style,
    sidebar_header_style,
    sidebar_item_label_style,
    sidebar_remove_button_style,
    sidebar_scroll_style,
    scrollbar_style,
)
from ..utils.layout_helpers import apply_layout_margins, apply_layout_spacing
from ..utils.widget_helpers import apply_fixed_height, apply_fixed_size, apply_fixed_width
from ..utils.constants import (
    LIB_TAB_CONTENT_ZERO_MARGINS,
    SIDEBAR_ADD_BUTTON_HEIGHT,
    SIDEBAR_HEADER_HEIGHT,
    SIDEBAR_ICON_BUTTON_SIZE,
    SIDEBAR_ICON_TINT_ALPHA,
    SIDEBAR_INNER_CONTENT_WIDTH,
    SIDEBAR_ITEM_HEIGHT,
    SIDEBAR_ITEM_TRAILING_MARGIN,
    SIDEBAR_MINOR_SECTION_SPACING,
    SIDEBAR_OUTER_MARGIN,
    SIDEBAR_OUTER_MARGIN_LEFT,
    SIDEBAR_OUTER_MARGIN_TOP,
    SIDEBAR_ROW_SPACING,
    SIDEBAR_SECTION_SPACING,
    SIDEBAR_WIDTH,
)

POSSIBLE_DUPLICATE_FILTER_NAME = "Possible duplicates"
POSSIBLE_DUPLICATE_FILTER_QUERY = 'tag:"possibleduplicate"'

CORRUPT_SILENT_EMPTY_FILTER_NAME = "Corrupt / Silent / Empty"
CORRUPT_SILENT_EMPTY_FILTER_QUERY = 'tag:"Silent" OR tag:"Empty" OR tag:"Corrupted"'

class SignalFloorControl(QFrame):
    """
    Control for setting the minimum confidence/probability floor for classification.
    """
    rangeChanged = Signal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        apply_style(self, frame_plain_style())
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        
        layout = QVBoxLayout(self)
        apply_layout_margins(layout, LIB_TAB_CONTENT_ZERO_MARGINS)
        apply_layout_spacing(layout, SIDEBAR_ROW_SPACING)

        header = QHBoxLayout()
        apply_layout_margins(header, LIB_TAB_CONTENT_ZERO_MARGINS)
        
        self.lbl_title = QLabel("CONFIDENCE: 0% - 100%")
        apply_style(self.lbl_title, section_label_style())
        header.addWidget(self.lbl_title, 1)
        layout.addLayout(header)

        self.slider = ModernRangeSlider()
        self.slider.setValues(0, 100)
        self.slider.valuesChanged.connect(self._on_slider_changed)
        layout.addWidget(self.slider)

    def _on_slider_changed(self, min_val, max_val):
        self.lbl_title.setText(f"CONFIDENCE: {min_val}% - {max_val}%")
        self.rangeChanged.emit(min_val / 100.0, max_val / 100.0)

    def set_range(self, min_val: float, max_val: float):
        self.slider.setValues(round(min_val * 100), round(max_val * 100))
        self.lbl_title.setText(f"CONFIDENCE: {round(min_val * 100)}% - {round(max_val * 100)}%")

    def set_floor(self, val: float):
        self.set_range(val, 1.0)

    def refresh_theme(self) -> None:
        apply_style(self.lbl_title, section_label_style())


class LibrarySidebarItem(QFrame):
    """
    A single folder entry in the sidebar list.
    """
    removeRequested = Signal(Path)

    def __init__(self, path: Path, is_filtered=False, parent=None):
        super().__init__(parent)
        self.path = path
        apply_fixed_height(self, SIDEBAR_ITEM_HEIGHT)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        layout = QHBoxLayout(self)
        apply_layout_margins(
            layout,
            (
                LIB_TAB_CONTENT_ZERO_MARGINS[0],
                LIB_TAB_CONTENT_ZERO_MARGINS[1],
                SIDEBAR_ITEM_TRAILING_MARGIN,
                LIB_TAB_CONTENT_ZERO_MARGINS[3],
            ),
        )

        name = path.name if path.name else str(path)
        self.lbl = QLabel(name)
        self.lbl.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        apply_style(self.lbl, sidebar_item_label_style())
        layout.addWidget(self.lbl, 1)
        
        semi_transparent_white = make_qcolor(ColorPalette.TEXT_MAIN)
        semi_transparent_white.setAlpha(SIDEBAR_ICON_TINT_ALPHA)
        pixmap = QPixmap(str(asset_path("icons", "close.png")))
        if not pixmap.isNull():
            painter = QPainter(pixmap)
            painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
            painter.fillRect(pixmap.rect(), semi_transparent_white)
            painter.end()

        self.btn = QPushButton("")
        if not pixmap.isNull():
            self.btn.setIcon(QIcon(pixmap))
        apply_fixed_size(self.btn, SIDEBAR_ICON_BUTTON_SIZE, SIDEBAR_ICON_BUTTON_SIZE)
        icon_size = max(1, SIDEBAR_ICON_BUTTON_SIZE - 4)
        self.btn.setIconSize(QSize(icon_size, icon_size))
        self.btn.setCursor(Qt.PointingHandCursor)
        apply_style(self.btn, sidebar_remove_button_style())
        self.btn.clicked.connect(lambda checked=False: self.removeRequested.emit(self.path))
        layout.addWidget(self.btn)

        self.is_filtered = is_filtered
        self._update_pill_style()
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)

    def _sidebar_parent(self):
        parent = self.parentWidget()
        while parent is not None:
            if hasattr(parent, "_remember_source_filter") and hasattr(parent, "toggleFilterRequested"):
                return parent
            parent = parent.parentWidget()
        return None

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.is_filtered = not self.is_filtered
            self._update_pill_style()
            parent = self._sidebar_parent()
            mode = self._compose_mode(event.modifiers())
            if parent is not None:
                parent._remember_source_filter(self.path, self.is_filtered, mode)
                parent.toggleFilterRequested.emit(self.path, self.is_filtered, mode)
        super().mousePressEvent(event)

    def _compose_mode(self, modifiers):
        if modifiers & Qt.ShiftModifier: return "or"
        if modifiers & Qt.ControlModifier: return "and"
        return "replace"

    def _update_pill_style(self):
        if self.is_filtered:
            apply_style(self, sidebar_active_item_style())
        else:
            apply_style(self, "")

    def set_filtered(self, is_filtered: bool):
        self.is_filtered = is_filtered
        self._update_pill_style()

    def refresh_theme(self) -> None:
        apply_style(self.lbl, sidebar_item_label_style())
        apply_style(self.btn, sidebar_remove_button_style())
        self._update_pill_style()

    def _show_context_menu(self, pos):
        menu = QMenu(self)
        from PySide6.QtGui import QAction
        refresh_act = QAction("Refresh Folder", self)
        def _emit_refresh():
            parent = self._sidebar_parent()
            if parent is not None:
                parent.refreshRequested.emit(self.path)

        refresh_act.triggered.connect(_emit_refresh)
        remove_act = QAction("Remove from Library", self)
        remove_act.triggered.connect(lambda checked=False: self.removeRequested.emit(self.path))
        menu.addAction(refresh_act)
        menu.addSeparator()
        menu.addAction(remove_act)
        menu.exec(self.mapToGlobal(pos))


class SavedFilterItem(QFrame):
    """
    A single saved search query entry in the sidebar.
    """
    toggleFilterRequested = Signal(str, bool, str)
    removeRequested = Signal(str)

    def __init__(self, name: str, query: str, is_filtered=False, parent=None, removable=True, filter_enabled=True):
        super().__init__(parent)
        self.name = name
        self.query = query
        self.is_filtered = is_filtered
        self.removable = bool(removable)
        self.filter_enabled = bool(filter_enabled)
        apply_fixed_height(self, SIDEBAR_ITEM_HEIGHT)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        layout = QHBoxLayout(self)
        apply_layout_margins(
            layout,
            (
                LIB_TAB_CONTENT_ZERO_MARGINS[0],
                LIB_TAB_CONTENT_ZERO_MARGINS[1],
                SIDEBAR_ITEM_TRAILING_MARGIN,
                LIB_TAB_CONTENT_ZERO_MARGINS[3],
            ),
        )

        self.lbl = QLabel(name)
        self.lbl.setToolTip(query)
        self.lbl.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        apply_style(self.lbl, sidebar_item_label_style())
        layout.addWidget(self.lbl, 1)

        self.btn = QPushButton("x")
        apply_fixed_size(self.btn, SIDEBAR_ICON_BUTTON_SIZE, SIDEBAR_ICON_BUTTON_SIZE)
        self.btn.setCursor(Qt.PointingHandCursor)
        apply_style(self.btn, sidebar_remove_button_style())
        self.btn.clicked.connect(lambda checked=False: self.removeRequested.emit(self.query))
        self.btn.setVisible(self.removable)
        layout.addWidget(self.btn)
        self.setEnabled(self.filter_enabled)

        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)
        self._update_style()

    def mousePressEvent(self, event):
        if not self.filter_enabled:
            return
        if event.button() == Qt.LeftButton:
            self.is_filtered = not self.is_filtered
            self._update_style()
            self.toggleFilterRequested.emit(self.query, self.is_filtered, self._compose_mode(event.modifiers()))
        super().mousePressEvent(event)

    def _compose_mode(self, modifiers):
        if modifiers & Qt.ShiftModifier: return "or"
        if modifiers & Qt.ControlModifier: return "and"
        return "replace"

    def _update_style(self):
        if self.is_filtered:
            apply_style(self, sidebar_active_item_style())
        else:
            apply_style(self, "")

    def set_filtered(self, is_filtered: bool):
        self.is_filtered = is_filtered
        self._update_style()

    def refresh_theme(self) -> None:
        apply_style(self.lbl, sidebar_item_label_style())
        apply_style(self.btn, sidebar_remove_button_style())
        self._update_style()

    def set_filter_enabled(self, enabled: bool) -> None:
        self.filter_enabled = enabled
        self.setEnabled(self.filter_enabled)

    def _show_context_menu(self, pos):
        menu = QMenu(self)
        from PySide6.QtGui import QAction
        remove_act = QAction("Remove Saved Filter", self)
        remove_act.triggered.connect(lambda checked=False: self.removeRequested.emit(self.query))
        menu.addAction(remove_act)
        menu.exec(self.mapToGlobal(pos))


class LibrarySidebar(QFrame):
    """
    Main sidebar container managing directories, saved filters, and options.
    """
    addRequested = Signal()
    removeRequested = Signal(Path)
    refreshRequested = Signal(Path)
    toggleFilterRequested = Signal(Path, bool, str)
    savedFilterRequested = Signal(str, bool, str)
    removeSavedFilterRequested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("LibrarySidebar")
        self.saved_filter_active: set[str] = set()
        self.source_filter_active: set[str] = set()
        self.saved_filters: list[dict] = []
        self.possible_duplicate_filter_enabled = False
        self.corrupt_silent_empty_filter_enabled = False
        self._source_count = 0
        self._saved_filter_count = 0
        apply_fixed_width(self, SIDEBAR_WIDTH)
        apply_style(self, sidebar_base_style())

        outer_layout = QVBoxLayout(self)
        apply_layout_margins(outer_layout, LIB_TAB_CONTENT_ZERO_MARGINS)
        apply_layout_spacing(outer_layout, 0)

        self.sidebar_edge_scrollbar = QScrollBar(Qt.Vertical, self)
        self.sidebar_edge_scrollbar.setObjectName("SidebarEdgeScrollBar")
        apply_fixed_width(self.sidebar_edge_scrollbar, 8)
        apply_style(self.sidebar_edge_scrollbar, scrollbar_style(left=True))
        self.sidebar_edge_scrollbar.valueChanged.connect(self._on_sidebar_scrollbar_value_changed)

        self.sidebar_column = QWidget()
        sidebar_column_layout = QVBoxLayout(self.sidebar_column)
        apply_layout_margins(sidebar_column_layout, LIB_TAB_CONTENT_ZERO_MARGINS)
        apply_layout_spacing(sidebar_column_layout, 0)
        outer_layout.addWidget(self.sidebar_column, 1)

        self.header_shell = QWidget()
        header_shell_layout = QVBoxLayout(self.header_shell)
        apply_layout_margins(
            header_shell_layout,
            (
                SIDEBAR_OUTER_MARGIN_LEFT,
                SIDEBAR_OUTER_MARGIN_TOP,
                SIDEBAR_OUTER_MARGIN,
                LIB_TAB_CONTENT_ZERO_MARGINS[3],
            ),
        )
        apply_layout_spacing(header_shell_layout, LIB_TAB_CONTENT_ZERO_MARGINS[0])

        self.header_container = QWidget()
        apply_fixed_height(self.header_container, SIDEBAR_HEADER_HEIGHT)
        apply_style(self.header_container, sidebar_header_style())
        h_layout = QHBoxLayout(self.header_container)
        apply_layout_margins(h_layout, LIB_TAB_CONTENT_ZERO_MARGINS)

        self.header_libraries = QLabel("Library")
        apply_style(self.header_libraries, sidebar_title_style())
        h_layout.addWidget(self.header_libraries)
        h_layout.addStretch()
        header_shell_layout.addWidget(self.header_container, 0, Qt.AlignHCenter)
        sidebar_column_layout.addWidget(self.header_shell, 0)

        self.sidebar_scroll = QScrollArea()
        self.sidebar_scroll.setWidgetResizable(True)
        self.sidebar_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.sidebar_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.sidebar_scroll.setFrameShape(QFrame.NoFrame)
        self.sidebar_scroll.setLayoutDirection(Qt.LeftToRight)
        apply_style(self.sidebar_scroll, sidebar_scroll_style(left=True))
        self.sidebar_scroll.verticalScrollBar().valueChanged.connect(self._sync_sidebar_scrollbar)
        self.sidebar_scroll.verticalScrollBar().rangeChanged.connect(self._sync_sidebar_scrollbar)
        sidebar_column_layout.addWidget(self.sidebar_scroll)

        self.sidebar_content = QWidget()
        self.sidebar_content.setLayoutDirection(Qt.LeftToRight)
        apply_style(self.sidebar_content, sidebar_content_style())
        self.sidebar_scroll.setWidget(self.sidebar_content)

        layout = QVBoxLayout(self.sidebar_content)
        self.content_layout = layout
        apply_layout_margins(
            layout,
            (
                SIDEBAR_OUTER_MARGIN_LEFT,
                SIDEBAR_SECTION_SPACING,
                SIDEBAR_OUTER_MARGIN,
                SIDEBAR_OUTER_MARGIN,
            ),
        )
        apply_layout_spacing(layout, LIB_TAB_CONTENT_ZERO_MARGINS[0])

        self.directories_section = QWidget()
        directories_layout = QVBoxLayout(self.directories_section)
        apply_layout_margins(directories_layout, LIB_TAB_CONTENT_ZERO_MARGINS)
        apply_layout_spacing(directories_layout, SIDEBAR_ROW_SPACING)
        self.directories_label = section_label("Directories")
        self.directories_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        directories_layout.addWidget(self.directories_label)

        self.directories_scroll = QScrollArea()
        self.directories_scroll.setWidgetResizable(True)
        self.directories_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.directories_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.directories_scroll.setSizeAdjustPolicy(QAbstractScrollArea.AdjustToContents)
        self.directories_scroll.setFrameShape(QFrame.NoFrame)
        self.directories_scroll.setLayoutDirection(Qt.RightToLeft)
        apply_style(self.directories_scroll, sidebar_scroll_style(left=True))
        self.directories_content = QWidget()
        self.directories_content.setLayoutDirection(Qt.LeftToRight)
        apply_style(self.directories_content, sidebar_content_style())
        self.list_layout = QVBoxLayout(self.directories_content)
        apply_layout_margins(self.list_layout, LIB_TAB_CONTENT_ZERO_MARGINS)
        apply_layout_spacing(self.list_layout, SIDEBAR_ROW_SPACING)
        self.list_layout.setAlignment(Qt.AlignTop)
        self.directories_scroll.setWidget(self.directories_content)
        directories_layout.addWidget(self.directories_scroll)

        self.btn_add_source = QPushButton("+")
        apply_fixed_height(self.btn_add_source, SIDEBAR_ADD_BUTTON_HEIGHT)
        self.btn_add_source.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.btn_add_source.setCursor(Qt.PointingHandCursor)
        apply_style(self.btn_add_source, sidebar_add_button_style())
        self.btn_add_source.clicked.connect(lambda checked=False: self.addRequested.emit())
        directories_layout.addWidget(self.btn_add_source)
        layout.addWidget(self.directories_section, 0)
        layout.addSpacing(SIDEBAR_SECTION_SPACING)

        self.saved_filters_section = QWidget()
        saved_layout = QVBoxLayout(self.saved_filters_section)
        apply_layout_margins(saved_layout, LIB_TAB_CONTENT_ZERO_MARGINS)
        apply_layout_spacing(saved_layout, SIDEBAR_ROW_SPACING)
        self.saved_label = section_label("SAVED FILTERS")
        self.saved_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        saved_layout.addWidget(self.saved_label)

        self.saved_filters_scroll = QScrollArea()
        self.saved_filters_scroll.setWidgetResizable(True)
        self.saved_filters_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.saved_filters_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.saved_filters_scroll.setSizeAdjustPolicy(QAbstractScrollArea.AdjustToContents)
        self.saved_filters_scroll.setFrameShape(QFrame.NoFrame)
        self.saved_filters_scroll.setLayoutDirection(Qt.RightToLeft)
        apply_style(self.saved_filters_scroll, sidebar_scroll_style(left=True))
        self.saved_filters_content = QWidget()
        self.saved_filters_content.setLayoutDirection(Qt.LeftToRight)
        apply_style(self.saved_filters_content, sidebar_content_style())
        self.saved_filters_layout = QVBoxLayout(self.saved_filters_content)
        apply_layout_margins(self.saved_filters_layout, LIB_TAB_CONTENT_ZERO_MARGINS)
        apply_layout_spacing(self.saved_filters_layout, SIDEBAR_ROW_SPACING)
        self.saved_filters_layout.setAlignment(Qt.AlignTop)
        self.saved_filters_scroll.setWidget(self.saved_filters_content)
        saved_layout.addWidget(self.saved_filters_scroll)
        layout.addWidget(self.saved_filters_section, 0)
        layout.addSpacing(SIDEBAR_MINOR_SECTION_SPACING)

        layout.addStretch(1)

        self.options_section = CollapsibleSection("OPTIONS", use_scroll=False)
        layout.addWidget(self.options_section)
        self.options_section.set_expanded(True)
        self.top_controls = self.options_section.content_layout
        self.top_controls.setAlignment(Qt.AlignLeft)

        self.signal_floor_control = SignalFloorControl()
        self.top_controls.addWidget(self.signal_floor_control)
        QTimer.singleShot(0, self._sync_sidebar_scrollbar)

    def _remember_source_filter(self, path: Path, is_active: bool, mode: str = "replace"):
        key = self._source_key(path)
        if is_active:
            if mode == "replace":
                self.source_filter_active = {key}
            else:
                self.source_filter_active.add(key)
        else:
            self.source_filter_active.discard(key)
        self._sync_source_items()

    def set_sources(self, sources: list[Path]):
        self._source_count = len(sources or [])
        known_sources = {self._source_key(src) for src in sources}
        self.source_filter_active &= known_sources
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        for src in sources:
            item = LibrarySidebarItem(src, self._source_key(src) in self.source_filter_active)
            item.removeRequested.connect(lambda path: self.removeRequested.emit(path))
            self.list_layout.addWidget(item)
        self._schedule_inner_list_fit()

    def set_active_source_filters(self, sources: set[str]):
        self.source_filter_active = {self._source_key(Path(source)) for source in sources if source.strip()}
        self._sync_source_items()

    def _sync_source_items(self):
        for i in range(self.list_layout.count()):
            widget = self.list_layout.itemAt(i).widget()
            if isinstance(widget, LibrarySidebarItem):
                widget.set_filtered(self._source_key(widget.path) in self.source_filter_active)

    def _source_key(self, path: Path):
        from ..core.filter_query import normalize_source_path_key

        return normalize_source_path_key(path)

    def set_saved_filters(self, filters: list[dict]):
        self._saved_filter_count = 0
        self.saved_filters = list(filters or [])
        filters = list(self.saved_filters)
        builtin_filters = []
        if self.corrupt_silent_empty_filter_enabled:
            builtin_filters.append({
                "name": CORRUPT_SILENT_EMPTY_FILTER_NAME,
                "query": CORRUPT_SILENT_EMPTY_FILTER_QUERY,
                "builtin": True,
                "enabled": True,
            })
        if self.possible_duplicate_filter_enabled:
            builtin_filters.append({
                "name": POSSIBLE_DUPLICATE_FILTER_NAME,
                "query": POSSIBLE_DUPLICATE_FILTER_QUERY,
                "builtin": True,
                "enabled": True,
            })
        filters = builtin_filters + filters
        queries = {str(f.get("query", "")) for f in filters}
        self.saved_filter_active &= queries
        while self.saved_filters_layout.count():
            item = self.saved_filters_layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        for filt in filters:
            name = str(filt.get("name", filt.get("query", "")))
            query = str(filt.get("query", ""))
            if not query: continue
            is_builtin = bool(filt.get("builtin", False))
            is_enabled = bool(filt.get("enabled", True))
            item = SavedFilterItem(
                name,
                query,
                query in self.saved_filter_active,
                removable=not is_builtin,
                filter_enabled=is_enabled,
            )
            item.toggleFilterRequested.connect(self._remember_saved_filter)
            item.toggleFilterRequested.connect(lambda query, active, mode: self.savedFilterRequested.emit(query, active, mode))
            if not is_builtin:
                item.removeRequested.connect(lambda query: self.removeSavedFilterRequested.emit(query))
            self.saved_filters_layout.addWidget(item)
            self._saved_filter_count += 1
        self._schedule_inner_list_fit()

    def set_possible_duplicate_filter_enabled(self, enabled: bool):
        previous = self.possible_duplicate_filter_enabled
        self.possible_duplicate_filter_enabled = enabled
        if previous != self.possible_duplicate_filter_enabled:
            self.set_saved_filters(self.saved_filters)

    def set_corrupt_silent_empty_filter_enabled(self, enabled: bool):
        previous = self.corrupt_silent_empty_filter_enabled
        self.corrupt_silent_empty_filter_enabled = enabled
        if previous != self.corrupt_silent_empty_filter_enabled:
            self.set_saved_filters(self.saved_filters)

    def _schedule_inner_list_fit(self) -> None:
        QTimer.singleShot(0, self._fit_inner_lists)

    def _content_margins_for_scrollbar(self) -> tuple[int, int, int, int]:
        return (
            SIDEBAR_OUTER_MARGIN_LEFT,
            SIDEBAR_SECTION_SPACING,
            SIDEBAR_OUTER_MARGIN,
            SIDEBAR_OUTER_MARGIN,
        )

    def _available_inner_width(self) -> int:
        margins = self._content_margins_for_scrollbar()
        return max(
            1,
            self.width() - margins[0] - margins[2],
        )

    def _body_width(self) -> int:
        return max(1, self._available_inner_width() - SIDEBAR_OUTER_MARGIN * 2)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "sidebar_edge_scrollbar"):
            self.sidebar_edge_scrollbar.setGeometry(0, 0, self.sidebar_edge_scrollbar.width(), self.height())
            self.sidebar_edge_scrollbar.raise_()
        self._schedule_inner_list_fit()

    def _fit_inner_lists(self) -> None:
        try:
            if not shiboken6.isValid(self) or not shiboken6.isValid(self.content_layout):
                return
        except RuntimeError:
            return
        apply_layout_margins(self.content_layout, self._content_margins_for_scrollbar())
        inner_width = self._available_inner_width()
        body_width = self._body_width()
        self.header_shell.setFixedWidth(self.width())
        self.sidebar_content.setFixedWidth(self.width())
        self.header_container.setFixedWidth(inner_width)
        for widget in (self.directories_section, self.saved_filters_section, self.options_section):
            widget.setFixedWidth(body_width)
            index = self.content_layout.indexOf(widget)
            if index >= 0:
                self.content_layout.itemAt(index).setAlignment(Qt.AlignHCenter)
        for widget in (
            self.directories_label,
            self.directories_scroll,
            self.btn_add_source,
            self.saved_label,
            self.saved_filters_scroll,
            self.signal_floor_control,
        ):
            widget.setFixedWidth(body_width)
        for carousel in self.findChildren(QWidget):
            if hasattr(carousel, "value_row"):
                carousel.setFixedWidth(body_width)
                carousel.value_row.setFixedWidth(body_width)
        for scroll, content in (
            (self.directories_scroll, self.directories_content),
            (self.saved_filters_scroll, self.saved_filters_content),
        ):
            content.adjustSize()
            height = max(1, content.sizeHint().height())
            scroll.setMinimumHeight(height)
            scroll.setMaximumHeight(height)
            row_width = max(1, scroll.viewport().width())
            content.setFixedWidth(row_width)
            for i in range(content.layout().count()):
                item = content.layout().itemAt(i)
                if item.widget() is not None:
                    item.widget().setFixedWidth(row_width)
            scroll.horizontalScrollBar().setValue(scroll.horizontalScrollBar().minimum())
        self.sidebar_content.move(0, 0)
        self.sidebar_scroll.horizontalScrollBar().setValue(self.sidebar_scroll.horizontalScrollBar().minimum())
        self._sync_sidebar_scrollbar()

    def _sync_sidebar_scrollbar(self, *_args) -> None:
        try:
            if not shiboken6.isValid(self) or not shiboken6.isValid(self.sidebar_edge_scrollbar):
                return
        except RuntimeError:
            return
        source = self.sidebar_scroll.verticalScrollBar()
        should_show = source.maximum() > source.minimum()
        if (
            self.sidebar_edge_scrollbar.minimum() == source.minimum()
            and self.sidebar_edge_scrollbar.maximum() == source.maximum()
            and self.sidebar_edge_scrollbar.pageStep() == source.pageStep()
            and self.sidebar_edge_scrollbar.singleStep() == source.singleStep()
            and self.sidebar_edge_scrollbar.value() == source.value()
            and self.sidebar_edge_scrollbar.isVisible() == should_show
        ):
            return
        old_state = self.sidebar_edge_scrollbar.blockSignals(True)
        self.sidebar_edge_scrollbar.setGeometry(0, 0, self.sidebar_edge_scrollbar.width(), self.height())
        self.sidebar_edge_scrollbar.setRange(source.minimum(), source.maximum())
        self.sidebar_edge_scrollbar.setPageStep(source.pageStep())
        self.sidebar_edge_scrollbar.setSingleStep(source.singleStep())
        self.sidebar_edge_scrollbar.setValue(source.value())
        self.sidebar_edge_scrollbar.setVisible(should_show)
        self.sidebar_edge_scrollbar.raise_()
        self.sidebar_edge_scrollbar.blockSignals(old_state)

    def _on_sidebar_scrollbar_value_changed(self, value: int) -> None:
        source = self.sidebar_scroll.verticalScrollBar()
        if source.value() != value:
            source.setValue(value)

    def _remember_saved_filter(self, query: str, is_active: bool, mode: str = "replace"):
        if is_active: self.saved_filter_active.add(query)
        else: self.saved_filter_active.discard(query)
        self._sync_saved_filter_items()

    def set_active_saved_filters(self, queries: set[str]):
        queries = {(query or "").strip() for query in queries if (query or "").strip()}
        self.saved_filter_active = queries 
        self._sync_saved_filter_items()

    def _sync_saved_filter_items(self):
        for i in range(self.saved_filters_layout.count()):
            widget = self.saved_filters_layout.itemAt(i).widget()
            if isinstance(widget, SavedFilterItem):
                widget.set_filtered(widget.query in self.saved_filter_active)

    def refresh_theme(self) -> None:
        apply_style(self, sidebar_base_style())
        apply_style(self.sidebar_edge_scrollbar, scrollbar_style(left=True))
        apply_style(self.sidebar_scroll, sidebar_scroll_style(left=True))
        apply_style(self.sidebar_content, sidebar_content_style())
        apply_style(self.header_container, sidebar_header_style())
        apply_style(self.header_libraries, sidebar_title_style())
        apply_style(self.directories_scroll, sidebar_scroll_style(left=True))
        apply_style(self.saved_filters_scroll, sidebar_scroll_style(left=True))
        apply_style(self.directories_content, sidebar_content_style())
        apply_style(self.saved_filters_content, sidebar_content_style())
        apply_style(self.btn_add_source, sidebar_add_button_style())
        for label in self.findChildren(QLabel):
            if label.property("sectionLabel"):
                apply_style(label, section_label_style())
        for section in self.findChildren(CollapsibleSection):
            section.refresh_theme()
        for item_type in (LibrarySidebarItem, SavedFilterItem):
            for item in self.findChildren(item_type):
                item.refresh_theme()
