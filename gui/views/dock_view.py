from pathlib import Path

from .. import widgets
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLineEdit,
    QPushButton, QFrame, QSizePolicy, QStackedWidget, QButtonGroup,
    QScrollArea,
)
from PySide6.QtCore import QSize, Signal, Qt

from unshuffle.core.constants import CATEGORIES

from .. import widgets as sw
from .library_tree import LibraryTreeView
from ..utils.constants import (
    DOCKED_HEADER_LAYOUT_MARGINS,
    DOCKED_HEADER_LAYOUT_SPACING,
    DOCKED_MAIN_LAYOUT_MARGINS,
    DOCKED_MAIN_LAYOUT_SPACING,
    DOCKED_MINIMUM_HEIGHT,
    DOCKED_MINIMUM_WIDTH,
    DOCKED_OPTIONS_LAYOUT_SPACING,
    LIB_TAB_CONTENT_ZERO_MARGINS,
    DOCKED_SEARCH_BAR_FIXED_HEIGHT,
    DOCKED_SEARCH_BAR_MINIMUM_WIDTH,
    DOCKED_SCROLL_CONTENT_MIN_HEIGHT,
    DOCKED_SEARCH_ROW_MARGINS,
    DOCKED_SEARCH_ROW_SPACING,
    DOCKED_TREE_PANEL_MIN_HEIGHT,
    LIB_TAB_VIEW_BUTTON_HEIGHT,
    LIB_TAB_VIEW_BUTTON_ICON_BOX_HEIGHT,
    LIB_TAB_VIEW_BUTTON_ICON_BOX_WIDTH,
    LIB_TAB_VIEW_BUTTON_ICON_SIZE,
    LIB_TAB_VIEW_BUTTON_WIDTH,
)
from ..utils.styles import (
    apply_style,
    dock_options_button_style,
    dock_save_search_button_style,
    dock_view_style,
    scaled_px,
)
from ..utils.layout_helpers import apply_layout_margins, apply_layout_spacing
from ..utils.widget_helpers import apply_fixed_height, apply_fixed_width, apply_minimum_width
from ..widgets.buttons import SidebarIconButton


class DockView(QWidget):
    """
    Read-only discovery side-car for docked mode.
    """

    searchChanged = Signal(str)
    typeToggleClicked = Signal(bool, bool, bool)
    orientationRequested = Signal()
    playRequested = Signal(object)
    similarityRequested = Signal(object)
    excludeRequested = Signal(object)
    quickFilterRequested = Signal(str, str)
    categoryChangeRequested = Signal(object, str)
    tagsEditRequested = Signal(object, object, object)
    openExplorerRequested = Signal(object)
    saveSearchRequested = Signal(str)
    filterRequested = Signal(str, bool)
    categoryFilterRequested = Signal(str, bool)
    rangeChanged = Signal(float, float)
    vibeBiasChanged = Signal(int)
    viewModeChanged = Signal(str)
    audioPreviewRequested = Signal(str)
    anchorRequested = Signal(str)
    findRequested = Signal(str)

    def __init__(self, tree_model, parent=None):
        super().__init__(parent)
        self.setObjectName("DockView")
        self.tree_model = tree_model
        self._vibe_state = {"anchor_text": "", "bias": 0, "visible": False}
        self._view_mode = "tree"
        self._map_available = True
        self.map_page = None
        self._setup_ui()
        self.setMinimumSize(DOCKED_MINIMUM_WIDTH, DOCKED_MINIMUM_HEIGHT)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def _setup_ui(self):
        if self.layout():
            layout = self.layout()
            while layout.count():
                item = layout.takeAt(0)
                widget = item.widget()
                if widget:
                    widget.deleteLater()
            if layout is not None:
                QWidget().setLayout(layout)

        root_layout = QVBoxLayout(self)
        apply_layout_margins(root_layout, LIB_TAB_CONTENT_ZERO_MARGINS)
        apply_layout_spacing(root_layout, LIB_TAB_CONTENT_ZERO_MARGINS[0])

        self.scroll_area = QScrollArea()
        self.scroll_area.setObjectName("DockScrollArea")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        root_layout.addWidget(self.scroll_area)

        self.scroll_content = QWidget()
        self.scroll_content.setObjectName("DockScrollContent")
        self.scroll_content.setMinimumWidth(0)
        self.scroll_content.setMinimumHeight(DOCKED_SCROLL_CONTENT_MIN_HEIGHT)
        self.scroll_area.setWidget(self.scroll_content)

        self.main_layout = QVBoxLayout(self.scroll_content)
        apply_layout_margins(self.main_layout, DOCKED_MAIN_LAYOUT_MARGINS)
        apply_layout_spacing(self.main_layout, DOCKED_MAIN_LAYOUT_SPACING)
        apply_style(self, dock_view_style())

        search_row = QHBoxLayout()
        apply_layout_margins(search_row, DOCKED_SEARCH_ROW_MARGINS)
        apply_layout_spacing(search_row, DOCKED_SEARCH_ROW_SPACING)

        self.edit_search = QLineEdit()
        self.edit_search.setPlaceholderText("Search...")
        self.edit_search.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        apply_minimum_width(self.edit_search, DOCKED_SEARCH_BAR_MINIMUM_WIDTH)
        apply_fixed_height(self.edit_search, DOCKED_SEARCH_BAR_FIXED_HEIGHT)
        self.edit_search.textChanged.connect(self.searchChanged.emit)
        self.edit_search.textChanged.connect(lambda _t: self._refresh_search_button_state())
        search_row.addWidget(self.edit_search, 1)

        self.btn_save_search = QPushButton("Save")
        apply_fixed_width(self.btn_save_search, DOCKED_SEARCH_BAR_MINIMUM_WIDTH)
        apply_fixed_height(self.btn_save_search, DOCKED_SEARCH_BAR_FIXED_HEIGHT)
        self.btn_save_search.setEnabled(False)
        apply_style(self.btn_save_search, dock_save_search_button_style())
        self.btn_save_search.clicked.connect(lambda: self.saveSearchRequested.emit(self.edit_search.text()))
        search_row.addWidget(self.btn_save_search)

        self.main_layout.addLayout(search_row)

        view_row = QHBoxLayout()
        apply_layout_margins(view_row, DOCKED_SEARCH_ROW_MARGINS)
        apply_layout_spacing(view_row, DOCKED_SEARCH_ROW_SPACING)
        self.btn_tree_view = SidebarIconButton(
            "icons/tree.png",
            QSize(LIB_TAB_VIEW_BUTTON_ICON_SIZE, LIB_TAB_VIEW_BUTTON_ICON_SIZE),
            QSize(LIB_TAB_VIEW_BUTTON_ICON_BOX_WIDTH, LIB_TAB_VIEW_BUTTON_ICON_BOX_HEIGHT),
        )
        self.btn_tree_view.setCheckable(True)
        self.btn_tree_view.clicked.connect(lambda: self.set_docked_view_mode("tree"))
        self.btn_map_view = SidebarIconButton(
            "icons/map.png",
            QSize(LIB_TAB_VIEW_BUTTON_ICON_SIZE, LIB_TAB_VIEW_BUTTON_ICON_SIZE),
            QSize(LIB_TAB_VIEW_BUTTON_ICON_BOX_WIDTH, LIB_TAB_VIEW_BUTTON_ICON_BOX_HEIGHT),
        )
        self.btn_map_view.setCheckable(True)
        self.btn_map_view.clicked.connect(lambda: self.set_docked_view_mode("map"))
        for button in (self.btn_tree_view, self.btn_map_view):
            apply_fixed_width(button, LIB_TAB_VIEW_BUTTON_WIDTH)
            apply_fixed_height(button, LIB_TAB_VIEW_BUTTON_HEIGHT)
            button.setCursor(Qt.PointingHandCursor)
        self.view_group = QButtonGroup(self)
        self.view_group.setExclusive(True)
        self.view_group.addButton(self.btn_tree_view)
        self.view_group.addButton(self.btn_map_view)
        view_row.addWidget(self.btn_tree_view)
        view_row.addWidget(self.btn_map_view)
        view_row.addStretch(1)
        self.main_layout.addLayout(view_row)

        self.options_section = sw.CollapsibleSection("OPTIONS", use_scroll=False)
        apply_style(self.options_section.btn, dock_options_button_style())
        
        opt_layout = self.options_section.content_layout
        apply_layout_spacing(opt_layout, DOCKED_OPTIONS_LAYOUT_SPACING)

        self.filter_carousel = sw.SidebarCarousel("Filters", [], inactive_text="None")
        self.filter_carousel.activeChanged.connect(self._on_filter_toggled)
        self.filter_carousel.valueSelected.connect(self._on_filter_selected)
        opt_layout.addWidget(self.filter_carousel)

        self.category_carousel = sw.SidebarCarousel("Categories", [(cat, cat) for cat in CATEGORIES], inactive_text="All")
        self.category_carousel.activeChanged.connect(self._on_category_toggled)
        self.category_carousel.valueSelected.connect(self._on_category_selected)
        opt_layout.addWidget(self.category_carousel)

        self.type_picker = widgets.TypeToggle()
        self.type_picker.typeChanged.connect(self._on_type_clicked)
        opt_layout.addWidget(self.type_picker, LIB_TAB_CONTENT_ZERO_MARGINS[0], Qt.AlignLeft)

        self.main_layout.addWidget(self.options_section)

        self.view_stack = QStackedWidget()
        self.view_stack.setMinimumHeight(DOCKED_TREE_PANEL_MIN_HEIGHT)

        self.view_tree = LibraryTreeView()
        self.view_tree.setModel(self.tree_model)
        apply_minimum_width(self.view_tree, LIB_TAB_CONTENT_ZERO_MARGINS[0])
        self.view_tree.setHeaderHidden(True)
        self.view_tree.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.view_tree.set_read_only_discovery(True)
        self.view_tree.play_requested.connect(lambda target: self.playRequested.emit(target))
        self.view_tree.similarity_requested.connect(lambda target: self.similarityRequested.emit(target))
        self.view_tree.exclude_requested.connect(lambda path: self.excludeRequested.emit(path))
        self.view_tree.quick_filter_requested.connect(lambda query, mode: self.quickFilterRequested.emit(query, mode))
        self.view_tree.category_change_requested.connect(lambda rec, category: self.categoryChangeRequested.emit(rec, category))
        self.view_tree.tags_edit_requested.connect(lambda records, add_tags, remove_tags: self.tagsEditRequested.emit(records, add_tags, remove_tags))
        self.view_tree.open_explorer_requested.connect(lambda target: self.openExplorerRequested.emit(target))
        self._force_single_tree_column()
        if hasattr(self.tree_model, "modelReset"):
            self.tree_model.modelReset.connect(self._force_single_tree_column)
        if hasattr(self.tree_model, "rebuildFinished"):
            self.tree_model.rebuildFinished.connect(self._force_single_tree_column)
        self.view_stack.addWidget(self.view_tree)
        self.main_layout.addWidget(self.view_stack, 1)

        self.set_docked_view_mode("tree", emit=False)

    def _force_single_tree_column(self) -> None:
        from PySide6.QtWidgets import QHeaderView

        model = self.view_tree.model()
        if model is None:
            return
        for column in range(1, model.columnCount()):
            self.view_tree.setColumnHidden(column, True)
        header = self.view_tree.header()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setStretchLastSection(True)
        self.view_tree.setColumnWidth(0, max(1, self.view_tree.viewport().width()))
        self.view_tree.horizontalScrollBar().setValue(0)


    def _on_type_clicked(self, _button):
        oneshots, loops, all_files = self.type_picker.get_state()
        self.typeToggleClicked.emit(oneshots, loops, all_files)

    def _on_filter_toggled(self, query, is_active):
        self.filter_carousel.set_active_values({query} if is_active else set())
        self.filterRequested.emit(query, is_active)

    def _on_filter_selected(self, query):
        self.filter_carousel.set_active_values({query})
        self.filterRequested.emit(query, True)

    def _on_category_toggled(self, category, is_active):
        self.category_carousel.set_active_values({category} if is_active else set())
        self.categoryFilterRequested.emit(category, is_active)

    def _on_category_selected(self, category):
        self.category_carousel.set_active_values({category})
        self.categoryFilterRequested.emit(category, True)


    def set_search_text(self, text: str):
        text = text or ""
        if self.edit_search.text() == text:
            return
        self.edit_search.blockSignals(True)
        self.edit_search.setText(text)
        self.edit_search.blockSignals(False)
        self._refresh_search_button_state()
        self.searchChanged.emit(text)

    def _refresh_search_button_state(self):
        self.btn_save_search.setEnabled(self.edit_search.isEnabled() and bool(self.edit_search.text().strip()))


    def set_filters(self, options: list[tuple[str, str]]):
        """Populates the filter carousel with (display_name, query) tuples."""
        self.filter_carousel.set_options(options)

    def set_active_saved_filters(self, queries: set[str]):
        self.filter_carousel.set_active_values(set(queries or set()))

    def set_category_state(self, active_values: set[str]):
        self.category_carousel.set_active_values(set(active_values or set()))


    def set_type_state(self, oneshots: bool, loops: bool, all_files: bool):
       self.type_picker.set_state(oneshots, loops, all_files)

    def set_confidence_range(self, min_val: float, max_val: float):
        self.tree_model.confidence_min = min_val
        self.tree_model.confidence_max = max_val

    def set_vibe_state(self, anchor_text: str, bias: int, visible: bool):
        self._vibe_state = {
            "anchor_text": anchor_text or "",
            "bias": bias,
            "visible": visible,
        }

    def selected_records(self):
        if self._view_mode != "tree":
            return []
        return list(self.view_tree._selected_records() or [])

    def ensure_map_page(self):
        if self.map_page is not None:
            return self.map_page
        from ..widgets.coherence_analyzer import CoherenceAnalyzerPage

        self.map_page = CoherenceAnalyzerPage(self, show_header=False, show_filters=False, show_zoom=False, default_zoom=4)
        self.map_page.audioPreviewRequested.connect(self.audioPreviewRequested.emit)
        self.map_page.anchorRequested.connect(self.anchorRequested.emit)
        self.map_page.findRequested.connect(self.findRequested.emit)
        self.map_page.vibeRequested.connect(lambda path: self.similarityRequested.emit(Path(path)))
        self.map_page.status.hide()
        self.map_page.audio_reserve.setMinimumHeight(0)
        self.map_page.audio_reserve.setMaximumHeight(0)
        self.view_stack.addWidget(self.map_page)
        self._apply_docked_map_square()
        self.map_page.refresh_theme()
        return self.map_page

    def set_docked_view_mode(self, mode: str, *, emit: bool = True) -> None:
        mode = "map" if (mode or "").lower() == "map" else "tree"
        if mode == "map" and not self._map_available:
            mode = "tree"
        if mode == "map":
            page = self.ensure_map_page()
            self.view_stack.setCurrentWidget(page)
            self._apply_docked_map_square()
        else:
            self.view_stack.setCurrentWidget(self.view_tree)
            self.view_stack.setMinimumHeight(DOCKED_TREE_PANEL_MIN_HEIGHT)
            self.view_stack.setMaximumHeight(16777215)
        changed = self._view_mode != mode
        self._view_mode = mode
        self.btn_tree_view.setChecked(mode == "tree")
        self.btn_map_view.setChecked(mode == "map")
        self._refresh_view_mode_buttons()
        if changed and emit:
            self.viewModeChanged.emit(mode)

    def set_map_available(self, available: bool) -> None:
        self._map_available = available
        self.btn_map_view.setVisible(self._map_available)
        self.btn_map_view.setEnabled(self._map_available)
        if not self._map_available and self._view_mode == "map":
            self.set_docked_view_mode("tree")

    def refresh_map_from_app(self, app, *, force: bool = False) -> None:
        if self._view_mode != "map":
            return
        page = self.ensure_map_page()
        page.set_loading(True, "Preparing docked map...")
        self._refresh_map_page(page, app, force=force)

    def prewarm_map_from_app(self, app, *, force: bool = False) -> None:
        if not self._map_available:
            return
        page = self.ensure_map_page()
        self._apply_docked_map_square()
        self._refresh_map_page(page, app, force=force)
        if hasattr(page, "prewarm_library_projections"):
            page.prewarm_library_projections()

    def _refresh_map_page(self, page, app, *, force: bool = False) -> None:
        audio_type = self._current_audio_type_filter()
        category = self._current_category_filter()
        page.refresh_from_app(
            app,
            force=force,
            audio_type=audio_type,
            category=category,
        )
        if hasattr(page, "set_library_filters"):
            page.set_library_filters(audio_type, category, self._visible_record_ids_from_app(app))

    def _visible_record_ids_from_app(self, app) -> set[str] | None:
        library_tab = getattr(app, "library_tab", None)
        if library_tab is not None and hasattr(library_tab, "_visible_record_ids_from_proxy"):
            return library_tab._visible_record_ids_from_proxy()
        return None

    def _current_audio_type_filter(self) -> str:
        oneshots, loops, all_files = self.type_picker.get_state()
        if all_files:
            return ""
        if loops and not oneshots:
            return "Loops"
        if oneshots and not loops:
            return "Oneshots"
        return ""

    def _current_category_filter(self) -> str:
        values = set(self.category_carousel.active_values or set())
        if len(values) == 1:
            return str(next(iter(values)))
        return ""

    def _refresh_view_mode_buttons(self) -> None:
        self.btn_tree_view.refresh_theme()
        self.btn_map_view.refresh_theme()

    def _apply_docked_map_square(self) -> None:
        if self.map_page is None:
            return
        side = max(1, self.view_stack.width())
        self.map_page.map_stage.setMinimumHeight(side)
        self.map_page.map_stage.setMaximumHeight(side)
        self.view_stack.setMinimumHeight(side)
        self.view_stack.setMaximumHeight(16777215)

    def preferred_docked_height_for_mode(self, mode: str) -> int:
        if (mode or "").lower() == "map":
            self._apply_docked_map_square()
        else:
            self.view_stack.setMinimumHeight(DOCKED_TREE_PANEL_MIN_HEIGHT)
            self.view_stack.setMaximumHeight(16777215)
        self.scroll_content.adjustSize()
        self.updateGeometry()
        return max(DOCKED_MINIMUM_HEIGHT, self.scroll_content.sizeHint().height())

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._view_mode == "map":
            self._apply_docked_map_square()

    def refresh_theme(self) -> None:
        apply_style(self, dock_view_style())
        apply_style(self.btn_save_search, dock_save_search_button_style())
        apply_style(self.options_section.btn, dock_options_button_style())
        self._refresh_view_mode_buttons()
        self.view_tree.refresh_theme()
        if self.map_page is not None:
            self.map_page.refresh_theme()
        self.filter_carousel.refresh_theme()
        self.category_carousel.refresh_theme()
        self.type_picker.refresh_theme()
