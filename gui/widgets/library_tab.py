import json
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollBar,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)
import shiboken6

from unshuffle.core.constants import CATEGORIES

from . import sections as sw_sections
from . import sidebar as sw_sidebar
from . import toggles as sw_toggles
from .buttons import AnimatedIconButton, SidebarIconButton
from .coherence_analyzer import CoherenceAnalyzerPage
from .delegates import ComboDelegate
from .filter_suggestion_line_edit import FilterSuggestionLineEdit
from .filter_suggestions import build_filter_suggestions, saved_filter_queries
from .library_filters import category_options_for_type_state
from .library_sources import updated_recent_scan_sources
from .library_records import (
    opposite_audio_type_for_records,
    paths_for_source_rows,
    records_for_source_rows,
    tab_separated_selection_text,
)
from .library_context_menu import show_library_context_menu
from .library_columns import (
    ALWAYS_HIDDEN_TABLE_COLUMNS,
    TABLE_COLUMN_ORDER_KEY,
    captured_column_width_ratios,
    decode_column_order,
    default_column_width_ratios,
    encode_column_order,
    load_column_visibility,
    save_column_visibility,
    proportional_column_widths,
    visible_table_columns,
)
from .library_view_state import (
    LIBRARY_VIEW_MODES,
    first_available_view_mode,
    is_view_mode_available,
    normalize_available_view_modes,
    normalize_view_mode,
)
from ..utils.constants import (
    COLUMN_CONFIG,
    LIB_TAB_COLUMN_MIN_WIDTH,
    LIB_TAB_CONTENT_ZERO_MARGINS,
    LIB_TAB_CONTENT_GAP,
    LIB_TAB_RECENT_SCAN_SOURCES_LIMIT,
    LIB_TAB_RESIZE_DEBOUNCE_MS,
    LIB_TAB_ROW_MIN_HEIGHT,
    LIB_TAB_OUTER_MARGIN,
    LIB_TAB_TOOL_BUTTON_ICON_SIZE,
    LIB_TAB_DOCKED_MARGIN,
    LIB_TAB_RIGHT_PANEL_MARGIN,
    LIB_TAB_RIGHT_PANEL_SPACING,
    LIB_TAB_SEARCH_BUTTON_HEIGHT,
    LIB_TAB_SIDEBAR_CONTROL_SPACING,
    LIB_TAB_TOOLBAR_MARGIN_BOTTOM,
    LIB_TAB_TOOLBAR_MARGIN_H,
    LIB_TAB_TOOLBAR_MARGIN_TOP,
    LIB_TAB_TOOLBAR_SPACING,
    LIB_TAB_VIEW_BOX_MARGIN,
    LIB_TAB_VIEW_BOX_SPACING,
    LIB_TAB_VIEW_BUTTON_HEIGHT,
    LIB_TAB_VIEW_BUTTON_ICON_BOX_HEIGHT,
    LIB_TAB_VIEW_BUTTON_ICON_BOX_WIDTH,
    LIB_TAB_VIEW_BUTTON_ICON_SIZE,
    LIB_TAB_VIEW_BUTTON_WIDTH,
    MIN_COLUMN_WIDTH,
    REDO_ICON,
    STAGING_HEADERS,
    StagingColumn,
    UNDO_ICON,
)
from ..models import LibraryTreeModel
from ..views import LibraryTreeView, StagingTableView
from ..utils.styles import apply_style, workspace_sidebar_button_style, transparent_panel_style, dock_save_search_button_style
from ..core.settings_controller import create_app_settings
from ..utils.layout_helpers import apply_layout_margins, apply_layout_spacing
from ..utils.widget_helpers import apply_fixed_height, apply_fixed_size, apply_minimum_width

def _qt_alive(widget) -> bool:
    return widget is not None and shiboken6.isValid(widget)


class LibraryTab(QWidget):
    """
    Core app component.
    Manages the workbench toolbar, table/tree views, and execution triggers.
    """

    undoRequested = Signal()
    redoRequested = Signal()
    searchChanged = Signal(str)
    sortChanged = Signal(int)
    typeToggleClicked = Signal(bool, bool, bool)
    viewSwitchRequested = Signal()
    viewModeRequested = Signal(object)
    categoryFilterRequested = Signal(str, bool)
    commitRequested = Signal()
    playRequested = Signal(object)
    similarityRequested = Signal(object)
    bulkCategoryRequested = Signal(str, list)
    bulkSubcategoryRequested = Signal(str, str, list)
    bulkTypeRequested = Signal(str, list)
    headerMenuRequested = Signal(int, object)
    scanRequested = Signal(list, bool)
    refreshRequested = Signal(Path)
    removeFolderRequested = Signal(Path)
    deleteRecordsRequested = Signal(object)
    reorganizeRecordsRequested = Signal(object, object)
    saveFilterRequested = Signal(str, str)
    quickFilterRequested = Signal(str, str)
    savedFilterRequested = Signal(str, bool, str)
    removeSavedFilterRequested = Signal(str)
    toggleFilterRequested = Signal(Path, bool, str)
    openExplorerRequested = Signal(object)
    rangeChanged = Signal(float, float)
    focusSearchRequested = Signal()
    tagsEditRequested = Signal(object, object, object)
    preserveRequested = Signal(object, object)
    unpreserveRequested = Signal(object, object)
    treeOrganizationEditRequested = Signal()

    def __init__(self, undo_stack, parent=None):
        super().__init__(parent)
        self.undo_stack = undo_stack
        self.user_resized_rows = set()
        self.proxy_model = None
        self.saved_filters = []
        self._column_width_ratios: dict[StagingColumn, float] = {}
        self._applying_proportional_resize = False
        self._restoring_column_order = False
        self._column_order_connected = False
        self._last_applied_column_widths: dict[StagingColumn, int] = {}
        self._available_view_modes: set[str] = set(LIBRARY_VIEW_MODES)
        self.coherence_map = None
        self._pending_search_text = ""

        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.timeout.connect(self._optimize_row_heights)
        self._search_emit_timer = QTimer(self)
        self._search_emit_timer.setSingleShot(True)
        self._search_emit_timer.setInterval(240)
        self._search_emit_timer.timeout.connect(self._emit_pending_search_text)

        self._setup_ui()
        self.set_sort_index(self.combo_sort.currentIndex())
        self._refresh_search_button_state()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        apply_layout_margins(
            layout,
            (
                LIB_TAB_OUTER_MARGIN,
                LIB_TAB_OUTER_MARGIN,
                LIB_TAB_OUTER_MARGIN,
                LIB_TAB_OUTER_MARGIN,
            ),
        )
        apply_layout_spacing(layout, LIB_TAB_CONTENT_GAP)

        self.toolbar_container = QWidget()
        self.toolbar_container.setObjectName("LibraryToolbar")
        toolbar = QHBoxLayout(self.toolbar_container)
        apply_layout_margins(
            toolbar,
            (
                LIB_TAB_TOOLBAR_MARGIN_H,
                LIB_TAB_TOOLBAR_MARGIN_TOP,
                LIB_TAB_TOOLBAR_MARGIN_H,
                LIB_TAB_TOOLBAR_MARGIN_BOTTOM,
            ),
        )
        apply_layout_spacing(toolbar, LIB_TAB_TOOLBAR_SPACING)
        self.toolbar_layout = toolbar

        self.btn_undo = AnimatedIconButton(
            UNDO_ICON,
            QSize(LIB_TAB_TOOL_BUTTON_ICON_SIZE, LIB_TAB_TOOL_BUTTON_ICON_SIZE),
        )
        self.btn_undo.clicked.connect(lambda checked=False: self.undoRequested.emit())
        self.btn_redo = AnimatedIconButton(
            REDO_ICON,
            QSize(LIB_TAB_TOOL_BUTTON_ICON_SIZE, LIB_TAB_TOOL_BUTTON_ICON_SIZE),
        )
        self.btn_redo.clicked.connect(lambda checked=False: self.redoRequested.emit())
        toolbar.addWidget(self.btn_undo)
        toolbar.addWidget(self.btn_redo)
        toolbar.addSpacing(LIB_TAB_TOOLBAR_SPACING)

        self.search_container = QWidget()
        search_layout = QHBoxLayout(self.search_container)
        apply_layout_margins(search_layout, LIB_TAB_CONTENT_ZERO_MARGINS)
        self.edit_search = FilterSuggestionLineEdit(popup_object_name="LibrarySearchCompleter")
        self.edit_search.setPlaceholderText("Search library...")
        apply_minimum_width(self.edit_search, LIB_TAB_CONTENT_ZERO_MARGINS[0])
        apply_fixed_height(self.edit_search, LIB_TAB_SEARCH_BUTTON_HEIGHT)
        self.edit_search.textChanged.connect(self._queue_search_changed)
        search_layout.addWidget(self.edit_search)

        self.btn_save_search = QPushButton("Save")
        self.btn_save_search.setToolTip("Save current search as a filter")
        self.btn_save_search.setEnabled(False)
        apply_fixed_height(self.btn_save_search, LIB_TAB_SEARCH_BUTTON_HEIGHT)
        apply_style(self.btn_save_search, dock_save_search_button_style())
        self.btn_save_search.clicked.connect(lambda checked=False: self._emit_save_current_search())
        self.edit_search.textChanged.connect(lambda _text: self._refresh_search_button_state())
        search_layout.addWidget(self.btn_save_search)
        toolbar.addWidget(self.search_container, 1)

        self.content_layout = QHBoxLayout()
        apply_layout_margins(self.content_layout, LIB_TAB_CONTENT_ZERO_MARGINS)
        apply_layout_spacing(self.content_layout, LIB_TAB_CONTENT_GAP)

        self.sidebar = sw_sidebar.LibrarySidebar()
        self.sidebar.addRequested.connect(lambda: self._on_add_clicked())
        self.sidebar.removeRequested.connect(lambda path: self.removeFolderRequested.emit(path))
        self.sidebar.refreshRequested.connect(lambda path: self.refreshRequested.emit(path))
        self.sidebar.toggleFilterRequested.connect(lambda path, active, mode: self.toggleFilterRequested.emit(path, active, mode))
        self.sidebar.savedFilterRequested.connect(lambda query, active, mode: self.savedFilterRequested.emit(query, active, mode))
        self.sidebar.removeSavedFilterRequested.connect(lambda query: self.removeSavedFilterRequested.emit(query))
        self.signal_floor_control = self.sidebar.signal_floor_control
        self.signal_floor_control.rangeChanged.connect(self.rangeChanged.emit)
        self.content_layout.addWidget(self.sidebar)

        right_panel = QWidget()
        right_panel.setObjectName("LibraryViewPanel")
        right_layout = QVBoxLayout(right_panel)
        apply_layout_margins(right_layout, LIB_TAB_CONTENT_ZERO_MARGINS)
        apply_layout_spacing(right_layout, LIB_TAB_RIGHT_PANEL_SPACING)
        self.right_layout = right_layout

        self.sort_columns = [
            StagingColumn.PACK,
            StagingColumn.FILENAME,
            StagingColumn.CATEGORY,
            StagingColumn.SUBCATEGORY,
            StagingColumn.TAGS,
            StagingColumn.CONFIDENCE,
        ]
        self.combo_sort = QComboBox()
        self.combo_sort.addItems([STAGING_HEADERS[i] for i in self.sort_columns])
        self.combo_sort.currentIndexChanged.connect(self.sortChanged.emit)
        self.combo_sort.hide()

        self.type_picker = sw_toggles.TypeToggle()
        self.type_picker.typeChanged.connect(self._on_type_toggle_clicked)
        self.type_box = self.type_picker

        self.btn_view_switch = QPushButton("Tree")
        self.btn_view_switch.setCheckable(True)
        self.btn_view_switch.hide()
        self.btn_view_switch.clicked.connect(lambda checked=False: self.viewSwitchRequested.emit())

        self.view_box = QFrame()
        apply_style(self.view_box, transparent_panel_style())
        view_layout = QHBoxLayout(self.view_box)
        apply_layout_margins(
            view_layout,
            (
                LIB_TAB_VIEW_BOX_MARGIN,
                LIB_TAB_VIEW_BOX_MARGIN,
                LIB_TAB_VIEW_BOX_MARGIN,
                LIB_TAB_VIEW_BOX_MARGIN,
            ),
        )
        apply_layout_spacing(view_layout, LIB_TAB_VIEW_BOX_SPACING)

        self.btn_table_view = SidebarIconButton(
            "icons/table.png",
            QSize(LIB_TAB_VIEW_BUTTON_ICON_SIZE, LIB_TAB_VIEW_BUTTON_ICON_SIZE),
            QSize(LIB_TAB_VIEW_BUTTON_ICON_BOX_WIDTH, LIB_TAB_VIEW_BUTTON_ICON_BOX_HEIGHT),
        )
        self.btn_tree_view = SidebarIconButton(
            "icons/tree.png",
            QSize(LIB_TAB_VIEW_BUTTON_ICON_SIZE, LIB_TAB_VIEW_BUTTON_ICON_SIZE),
            QSize(LIB_TAB_VIEW_BUTTON_ICON_BOX_WIDTH, LIB_TAB_VIEW_BUTTON_ICON_BOX_HEIGHT),
        )
        self.btn_map_view = SidebarIconButton(
            "icons/map.png",
            QSize(LIB_TAB_VIEW_BUTTON_ICON_SIZE, LIB_TAB_VIEW_BUTTON_ICON_SIZE),
            QSize(LIB_TAB_VIEW_BUTTON_ICON_BOX_WIDTH, LIB_TAB_VIEW_BUTTON_ICON_BOX_HEIGHT),
        )
        self.btn_table_view.setToolTip("Table view")
        self.btn_tree_view.setToolTip("Tree view")
        self.btn_map_view.setToolTip("Map view")
        for btn in (self.btn_table_view, self.btn_tree_view, self.btn_map_view):
            btn.setCheckable(True)
            apply_fixed_size(btn, LIB_TAB_VIEW_BUTTON_WIDTH, LIB_TAB_VIEW_BUTTON_HEIGHT)
            btn.setCursor(Qt.PointingHandCursor)

        from PySide6.QtWidgets import QButtonGroup
        self.view_group = QButtonGroup(self)
        self.view_group.setExclusive(True)
        self.view_group.addButton(self.btn_table_view)
        self.view_group.addButton(self.btn_tree_view)
        self.view_group.addButton(self.btn_map_view)
        self.btn_table_view.clicked.connect(lambda checked=False: self.viewModeRequested.emit("table"))
        self.btn_tree_view.clicked.connect(lambda checked=False: self.viewModeRequested.emit("tree"))
        self.btn_map_view.clicked.connect(lambda checked=False: self.viewModeRequested.emit("map"))
        view_layout.addWidget(self.btn_table_view)
        view_layout.addWidget(self.btn_tree_view)
        view_layout.addWidget(self.btn_map_view)
        self.view_box.setFixedHeight(LIB_TAB_SEARCH_BUTTON_HEIGHT)
        self._setup_sidebar_controls()
        toolbar.addWidget(self.view_box, 0)
        toolbar.addWidget(self.type_box, 0)

        self.lib_stack = QStackedWidget()
        self.view_table = StagingTableView()
        self.view_table.setItemDelegate(ComboDelegate(self.view_table))
        self.view_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.view_table.customContextMenuRequested.connect(self._show_context_menu)
        header = self.view_table.horizontalHeader()
        header.setContextMenuPolicy(Qt.CustomContextMenu)
        header.customContextMenuRequested.connect(self._on_header_context_menu)
        self.view_table.doubleClicked.connect(self._on_table_double_clicked)
        self.view_table.quickFilterRequested.connect(self._handle_table_quick_filter)
        self.view_table.focusSearchRequested.connect(self.focusSearchRequested.emit)
        self.view_table.playRequested.connect(lambda: self.playRequested.emit(self.view_table.currentIndex()))
        self.view_table.sortColumnRequested.connect(self._on_table_sort_column_requested)
        self.view_table.resized.connect(self._apply_proportional_column_widths)
        self.view_table.resized.connect(self._sync_view_scrollbar)
        self.lib_stack.addWidget(self.view_table)

        self.tree_model = LibraryTreeModel(self)
        self.view_tree = LibraryTreeView()
        self.view_tree.setModel(self.tree_model)
        self.view_tree.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.view_tree.play_requested.connect(lambda r: self.playRequested.emit(r.source_path))
        self.view_tree.similarity_requested.connect(lambda target: self.similarityRequested.emit(target))
        self.view_tree.category_change_requested.connect(self._handle_tree_category_change)
        self.view_tree.tags_edit_requested.connect(lambda records, add_tags, remove_tags: self.tagsEditRequested.emit(records, add_tags, remove_tags))
        self.view_tree.exclude_requested.connect(lambda path: self.deleteRecordsRequested.emit(path))
        self.view_tree.preserve_requested.connect(lambda records, path: self.preserveRequested.emit(records, path))
        self.view_tree.unpreserve_requested.connect(lambda records, path: self.unpreserveRequested.emit(records, path))
        self.view_tree.reorganization_requested.connect(lambda records, fields: self.reorganizeRecordsRequested.emit(records, fields))
        self.view_tree.quick_filter_requested.connect(lambda query, mode: self.quickFilterRequested.emit(query, mode))
        self.view_tree.open_explorer_requested.connect(lambda target: self.openExplorerRequested.emit(target))
        self.view_tree.focus_search_requested.connect(self.focusSearchRequested.emit)

        self.tree_page = QWidget()
        tree_page_layout = QVBoxLayout(self.tree_page)
        apply_layout_margins(tree_page_layout, LIB_TAB_CONTENT_ZERO_MARGINS)
        apply_layout_spacing(tree_page_layout, LIB_TAB_CONTENT_GAP)
        tree_page_layout.addWidget(self.view_tree, 1)
        tree_footer = QHBoxLayout()
        apply_layout_margins(tree_footer, LIB_TAB_CONTENT_ZERO_MARGINS)
        apply_layout_spacing(tree_footer, LIB_TAB_TOOLBAR_SPACING)
        self.lbl_tree_org_badge = QLabel("")
        self.lbl_tree_org_badge.setVisible(False)
        tree_footer.addWidget(self.lbl_tree_org_badge, 0)
        tree_footer.addStretch(1)
        self.btn_tree_org = QPushButton("Edit tree organization")
        self.btn_tree_org.clicked.connect(self.treeOrganizationEditRequested.emit)
        self.btn_tree_org.setObjectName("secondary")
        apply_style(self.btn_tree_org, workspace_sidebar_button_style())
        tree_footer.addWidget(self.btn_tree_org, 0)
        tree_page_layout.addLayout(tree_footer, 0)
        self.lib_stack.addWidget(self.tree_page)

        self.coherence_map = None

        self.view_scrollbar = QScrollBar(Qt.Vertical)
        self.view_scrollbar.setObjectName("ViewEdgeScrollBar")
        self.view_scrollbar.setFixedWidth(8)
        self.view_scrollbar.valueChanged.connect(self._on_view_scrollbar_value_changed)
        for view in (self.view_table, self.view_tree):
            view.verticalScrollBar().valueChanged.connect(self._sync_view_scrollbar)
            view.verticalScrollBar().rangeChanged.connect(self._sync_view_scrollbar)

        self.view_frame = QWidget()
        view_frame_layout = QHBoxLayout(self.view_frame)
        apply_layout_margins(view_frame_layout, LIB_TAB_CONTENT_ZERO_MARGINS)
        apply_layout_spacing(view_frame_layout, 0)
        self.view_content = QWidget()
        view_content_layout = QVBoxLayout(self.view_content)
        apply_layout_margins(view_content_layout, (8, 8, 8, 8))
        apply_layout_spacing(view_content_layout, 0)
        view_content_layout.addWidget(self.lib_stack)
        view_frame_layout.addWidget(self.view_content, 1)
        view_frame_layout.addWidget(self.view_scrollbar, 0)
        right_layout.addWidget(self.view_frame, 1)

        layout.addWidget(self.toolbar_container)
        self.content_layout.addWidget(right_panel, 1)
        layout.addLayout(self.content_layout, 1)
        self.btn_table_view.setChecked(True)
        self.btn_tree_view.setChecked(False)
        self.btn_map_view.setChecked(False)
        self.set_available_view_modes(self._available_view_modes)
        QTimer.singleShot(0, self._sync_view_scrollbar)

    def _setup_sidebar_controls(self):
        from .carousels import SidebarCarousel
        sort_options = [(STAGING_HEADERS[col], i) for i, col in enumerate(self.sort_columns)]
        self.sort_carousel = SidebarCarousel(
            "Sort by",
            sort_options,
            inactive_text="File Name",
        )
        self.sort_carousel.activeChanged.connect(self._on_sort_carousel_toggled)
        self.sort_carousel.valueSelected.connect(self._on_sort_carousel_selected)
        self.sidebar.top_controls.addSpacing(LIB_TAB_SIDEBAR_CONTROL_SPACING)
        self.sidebar.top_controls.addWidget(self.sort_carousel)

        self.category_carousel = SidebarCarousel(
            "Categories",
            self._category_options_for_type_state(*self.type_picker.get_state()),
            inactive_text="All (default)",
        )
        self.category_carousel.activeChanged.connect(self._on_category_carousel_toggled)
        self.category_carousel.valueSelected.connect(self._on_category_carousel_selected)
        self.sidebar.top_controls.addSpacing(LIB_TAB_SIDEBAR_CONTROL_SPACING)
        self.sidebar.top_controls.addWidget(self.category_carousel)

    def _on_sort_carousel_toggled(self, sort_index, is_active):
        self.sort_carousel.set_active_values({sort_index} if is_active else set())
        if is_active:
            self._sync_sort_header(sort_index)
            self.combo_sort.setCurrentIndex(sort_index)
        else:
            self.combo_sort.setCurrentIndex(-1)

    def _on_sort_carousel_selected(self, sort_index):
        self.sort_carousel.set_active_values({sort_index})
        self._sync_sort_header(sort_index)
        self.combo_sort.setCurrentIndex(sort_index)

    def _on_table_sort_column_requested(self, column: int) -> None:
        try:
            staging_column = StagingColumn(column)
        except ValueError:
            return
        if staging_column not in self.sort_columns:
            return
        self.sort_carousel.set_active_values({self.sort_columns.index(staging_column)})
        self._sync_sort_header(self.sort_columns.index(staging_column))
        self.combo_sort.setCurrentIndex(self.sort_columns.index(staging_column))

    def _category_options_for_type_state(self, oneshots: bool, loops: bool, all_files: bool) -> list[tuple[str, str]]:
        return category_options_for_type_state(CATEGORIES, oneshots, loops, all_files)

    def _refresh_category_options_for_type(self, oneshots: bool, loops: bool, all_files: bool) -> None:
        options = self._category_options_for_type_state(oneshots, loops, all_files)
        valid_values = {value for _name, value in options}
        active_values = set(self.category_carousel.active_values)
        invalid_active_values = active_values - valid_values

        self.category_carousel.set_options(options)
        if invalid_active_values:
            self.category_carousel.set_active_values(set())
            for category in sorted(str(value) for value in invalid_active_values):
                self.categoryFilterRequested.emit(category, False)
        self.sync_map_filters()

    def _on_category_carousel_toggled(self, category, is_active):
        self.category_carousel.set_active_values({category} if is_active else set())
        self.sync_map_filters()
        self.categoryFilterRequested.emit(category, is_active)

    def _on_category_carousel_selected(self, category):
        self.category_carousel.set_active_values({category})
        self.sync_map_filters()
        self.categoryFilterRequested.emit(category, True)

    def _handle_tree_category_change(self, rec, category):
        self.bulkCategoryRequested.emit(category, [rec])

    def _on_table_double_clicked(self, index):
        if index.column() == StagingColumn.FILENAME:
            self.playRequested.emit(index)

    def set_docked_mode(self, enabled: bool):
        self.toolbar_container.setVisible(not enabled)
        self.sidebar.setVisible(not enabled)

        if enabled:
            self.set_view_mode(True)
            apply_layout_margins(
                self.right_layout,
                (
                    LIB_TAB_DOCKED_MARGIN,
                    LIB_TAB_DOCKED_MARGIN,
                    LIB_TAB_DOCKED_MARGIN,
                    LIB_TAB_DOCKED_MARGIN,
                ),
            )
            self.right_layout.insertWidget(0, self.search_container)
        else:
            apply_layout_margins(
                self.right_layout,
                LIB_TAB_CONTENT_ZERO_MARGINS,
            )
            self.toolbar_layout.insertWidget(4, self.search_container, 1)

    def _on_type_toggle_clicked(self, _):
        oneshots, loops, all_files = self.type_picker.get_state()
        self._refresh_category_options_for_type(oneshots, loops, all_files)
        self.sync_map_filters()
        self.typeToggleClicked.emit(oneshots, loops, all_files)

    def _show_context_menu(self, pos):
        show_library_context_menu(self, pos)

    def _on_header_context_menu(self, pos):
        col = self.view_table.horizontalHeader().logicalIndexAt(pos)
        self.headerMenuRequested.emit(col, pos)

    def _emit_save_current_search(self):
        query = self.edit_search.text().strip()
        if not query:
            return
        default_name = query.split(":", 1)[1].strip().strip('"') if ":" in query else query
        self.saveFilterRequested.emit(default_name, query)

    def _refresh_search_button_state(self) -> None:
        self.btn_save_search.setEnabled(self.edit_search.isEnabled() and bool(self.edit_search.text().strip()))

    def _handle_table_quick_filter(self, index, mode: str):
        if not index.isValid():
            return
        if index.column() not in COLUMN_CONFIG:
            return
        prefix = COLUMN_CONFIG[index.column()]["prefix"]
        val = str(index.data(Qt.DisplayRole) or "").strip()
        if val:
            self.quickFilterRequested.emit(f'{prefix}:"{val}"', mode)

    def _compose_mode(self, modifiers):
        if modifiers & Qt.ShiftModifier:
            return "or"
        if modifiers & Qt.ControlModifier:
            return "and"
        return "replace"

    def set_busy(self, busy: bool):
        self.view_table.setEnabled(not busy)
        self.view_tree.setEnabled(not busy)
        if self.coherence_map is not None:
            self.coherence_map.setEnabled(not busy)
        self.sidebar.btn_add_source.setEnabled(not busy)
        self.edit_search.setEnabled(not busy)
        self._refresh_search_button_state()
        
        app = self.window()
        has_draft = getattr(app, "drafting_controller", None) is not None and app.drafting_controller.has_changes()
        self.btn_undo.setEnabled(not busy and not has_draft and self.undo_stack.canUndo())
        self.btn_redo.setEnabled(not busy and not has_draft and self.undo_stack.canRedo())
        self.combo_sort.setEnabled(not busy)
        self.btn_view_switch.setEnabled(not busy)
        self.btn_table_view.setEnabled(not busy and self.is_view_available("table"))
        self.btn_tree_view.setEnabled(not busy and self.is_view_available("tree"))
        self.btn_map_view.setEnabled(not busy and self.is_view_available("map"))
        self.btn_tree_org.setEnabled(not busy)
        self.sort_carousel.setEnabled(not busy)
        self.category_carousel.setEnabled(not busy)
        self.signal_floor_control.setEnabled(not busy)
        self.setCursor(Qt.WaitCursor if busy else Qt.ArrowCursor)

    def _on_new_clicked(self):
        settings = create_app_settings()
        last_src = str(settings.value("last_scan_source", "") or "")
        p = QFileDialog.getExistingDirectory(self, "Select Folder to Scan", last_src)
        if p:
            self._remember_scan_source(p)
            self.scanRequested.emit([p], False)

    def _on_add_clicked(self):
        settings = create_app_settings()
        last_src = str(settings.value("last_scan_source", "") or "")
        p = QFileDialog.getExistingDirectory(self, "Select Folder to Add", last_src)
        if p:
            self._remember_scan_source(p)
            self.scanRequested.emit([p], True)

    def _remember_scan_source(self, path: str):
        path = (path or "").strip()
        if not path:
            return
        settings = create_app_settings()
        settings.setValue("last_scan_source", path)
        raw = settings.value("recent_scan_sources_json", "")
        settings.setValue(
            "recent_scan_sources_json",
            json.dumps(updated_recent_scan_sources(raw, path, limit=LIB_TAB_RECENT_SCAN_SOURCES_LIMIT)),
        )

    def set_proxy_model(self, model):
        old_proxy = getattr(self, "proxy_model", None)
        if old_proxy is not None:
            try:
                old_proxy.modelReset.disconnect(self.apply_table_column_visibility)
                old_proxy.layoutChanged.disconnect(self.apply_table_column_visibility)
            except Exception:
                pass
        self.proxy_model = model
        self.view_table.setModel(model)
        if model is not None:
            model.modelReset.connect(self.apply_table_column_visibility)
            model.layoutChanged.connect(self.apply_table_column_visibility)
        self.refresh_search_suggestions()
        self.apply_table_column_visibility()
        self.view_table.horizontalHeader().setMinimumSectionSize(MIN_COLUMN_WIDTH)
        self._connect_column_order_persistence()
        self._column_width_ratios = self._default_column_width_ratios()
        self._last_applied_column_widths = {}
        QTimer.singleShot(0, self._restore_table_column_order)
        QTimer.singleShot(0, self._apply_proportional_column_widths)
        QTimer.singleShot(0, self._sync_view_scrollbar)

    def set_column_visible(self, column: int, visible: bool) -> None:
        try:
            staging_column = StagingColumn(column)
        except ValueError:
            return
        if staging_column in ALWAYS_HIDDEN_TABLE_COLUMNS:
            self.apply_table_column_visibility()
            return
        save_column_visibility(staging_column, visible)
        self.apply_table_column_visibility()
        self._column_width_ratios = self._default_column_width_ratios()
        self._last_applied_column_widths = {}
        QTimer.singleShot(0, self._apply_proportional_column_widths)
        QTimer.singleShot(0, self._sync_view_scrollbar)

    def minimum_table_column_width(self) -> int:
        return MIN_COLUMN_WIDTH

    def set_available_view_modes(self, modes) -> None:
        normalized = normalize_available_view_modes(modes)
        self._available_view_modes = normalized
        buttons = {
            "table": self.btn_table_view,
            "tree": self.btn_tree_view,
            "map": self.btn_map_view,
        }
        for mode, button in buttons.items():
            available = mode in normalized
            button.setVisible(available)
            button.setEnabled(available)
            if not available:
                button.setChecked(False)
        self.view_box.setVisible(bool(normalized))
        if not self.is_view_available(self.current_view_mode()):
            self.set_view_mode(self.first_available_view_mode())
        else:
            self.set_view_mode(self.current_view_mode())

    def available_view_modes(self) -> set[str]:
        return set(getattr(self, "_available_view_modes", set(LIBRARY_VIEW_MODES)))

    def is_view_available(self, mode: str) -> bool:
        return is_view_mode_available(mode, self.available_view_modes())

    def first_available_view_mode(self) -> str:
        return first_available_view_mode(self.available_view_modes())

    def ensure_coherence_map(self):
        if self.coherence_map is not None:
            return self.coherence_map
        self.coherence_map = CoherenceAnalyzerPage(self, show_header=False, show_filters=False)
        self.coherence_map.vibeRequested.connect(lambda path: self.similarityRequested.emit(Path(path)))
        self.lib_stack.addWidget(self.coherence_map)
        return self.coherence_map

    def set_view_mode(self, mode):
        view_mode = self._normalize_view_mode(mode)
        is_tree = view_mode == "tree"
        is_map = view_mode == "map"
        if is_map:
            self.ensure_coherence_map()
        widgets = {"table": self.view_table, "tree": self.tree_page}
        if self.coherence_map is not None:
            widgets["map"] = self.coherence_map
        self.lib_stack.setCurrentWidget(widgets.get(view_mode, self.view_table))
        self.btn_view_switch.setText("Tree" if is_tree else "Table")
        self.btn_tree_view.setChecked(is_tree)
        self.btn_table_view.setChecked(view_mode == "table")
        self.btn_map_view.setChecked(is_map)
        self.btn_tree_view.update()
        self.btn_table_view.update()
        self.btn_map_view.update()
        if view_mode == "table":
            self.apply_table_column_visibility()
            QTimer.singleShot(0, self._apply_proportional_column_widths)
            if self.proxy_model is not None:
                self.proxy_model.invalidate()
            self.view_table.viewport().update()
        if is_map:
            self.sync_map_filters()
        QTimer.singleShot(0, self._sync_view_scrollbar)

    def _normalize_view_mode(self, mode) -> str:
        return normalize_view_mode(mode, self.available_view_modes())

    def current_view_mode(self) -> str:
        current = self.lib_stack.currentWidget()
        if current is self.tree_page:
            return "tree"
        if self.coherence_map is not None and current is self.coherence_map:
            return "map"
        return "table"

    def sync_map_filters(self) -> None:
        page = getattr(self, "coherence_map", None)
        if page is None or not hasattr(self, "type_picker") or not hasattr(self, "category_carousel"):
            return
        if self.current_view_mode() != "map":
            return
        page.set_library_filters(
            self._current_audio_type_filter(),
            self._current_category_filter(),
            self._visible_record_ids_from_proxy(),
        )

    def _visible_record_ids_from_proxy(self) -> set[str] | None:
        proxy = getattr(self, "proxy_model", None)
        if proxy is None:
            return None
        model = proxy.sourceModel()
        if model is None:
            return None
        row_count = proxy.rowCount()
        if hasattr(model, "rowCount") and row_count == model.rowCount():
            matched_ids = getattr(proxy, "matched_ids", None)
            has_filters = bool(
                matched_ids is not None
                or getattr(proxy, "column_filters", None)
                or getattr(proxy, "audio_types", None) is not None
                or getattr(proxy, "_norm_path_filters", None)
                or getattr(proxy, "similarity_active", False)
                or getattr(proxy, "confidence_min", 0.0) > 0.0
                or getattr(proxy, "confidence_max", 1.0) < 1.0
            )
            if not has_filters:
                return None
        visible_ids: set[str] = set()
        for row in range(row_count):
            source_index = proxy.mapToSource(proxy.index(row, 0))
            if not source_index.isValid():
                continue
            source_row = source_index.row()
            if hasattr(model, "record_id"):
                visible_ids.add(str(model.record_id(source_row)))
            else:
                visible_ids.add(str(source_row))
        return visible_ids

    def _current_audio_type_filter(self) -> str:
        oneshots, loops, all_files = self.type_picker.get_state()
        if all_files or (oneshots and loops):
            return ""
        if loops:
            return "Loops"
        if oneshots:
            return "Oneshots"
        return ""

    def _current_category_filter(self) -> str:
        active = list(getattr(self.category_carousel, "active_values", set()) or [])
        return str(active[0]) if len(active) == 1 else ""

    def set_tree_organization_state(self, active: bool, profile_name: str = "") -> None:
        text = f"{profile_name}" if active and profile_name else "Custom tree organization active"
        self.lbl_tree_org_badge.setText(text)
        self.lbl_tree_org_badge.setVisible(active)

    def focus_search(self):
        self.edit_search.setFocus()
        self.edit_search.selectAll()

    def _queue_search_changed(self, text: str) -> None:
        self._pending_search_text = (text or "")
        self._search_emit_timer.start()

    def _emit_pending_search_text(self) -> None:
        self.searchChanged.emit(self._pending_search_text)

    def set_type_state(self, oneshots: bool, loops: bool, all_files: bool):
        self.type_picker.set_state(oneshots, loops, all_files)

    def set_confidence_floor(self, val: float):
        self.signal_floor_control.set_floor(val)

    def set_sort_index(self, sort_index: int) -> None:
        self.combo_sort.blockSignals(True)
        self.combo_sort.setCurrentIndex(sort_index)
        self.combo_sort.blockSignals(False)
        if sort_index is None or sort_index < 0:
            self.sort_carousel.set_active_values(set())
        else:
            self.sort_carousel.set_active_values({sort_index})
            self._sync_sort_header(sort_index)

    def _sync_sort_header(self, sort_index: int) -> None:
        if 0 <= sort_index < len(self.sort_columns):
            self.view_table.horizontalHeader().setSortIndicator(self.sort_columns[sort_index], Qt.DescendingOrder)

    def refresh_theme(self) -> None:
        apply_style(self.view_box, transparent_panel_style())
        self.type_picker.refresh_theme()
        self.sidebar.refresh_theme()
        self.sort_carousel.refresh_theme()
        self.category_carousel.refresh_theme()
        self.signal_floor_control.refresh_theme()
        self.view_table.refresh_theme()
        self.view_tree.refresh_theme()
        self.btn_table_view.refresh_theme()
        self.btn_tree_view.refresh_theme()
        self.btn_map_view.refresh_theme()
        if self.coherence_map is not None:
            self.coherence_map.refresh_theme()
        apply_style(self.btn_save_search, dock_save_search_button_style())
        apply_style(self.btn_tree_org, workspace_sidebar_button_style())
        self._refresh_search_button_state()
        self._sync_view_scrollbar()

    def refresh_table_viewport(self) -> None:
        self.view_table.viewport().update()

    def reset_discovery_controls(self) -> None:
        self.edit_search.blockSignals(True)
        self.edit_search.clear()
        self.edit_search.blockSignals(False)
        self._refresh_search_button_state()
        self.sort_carousel.set_active_values(set())
        self.category_carousel.set_active_values(set())
        self.signal_floor_control.set_range(0.0, 1.0)

    def selected_records(self, model, proxy_model):
        if self.current_view_mode() == "tree":
            return list(self.view_tree._selected_records() or [])

        if model is None or proxy_model is None:
            return []

        selection_model = self.view_table.selectionModel()
        if selection_model is None:
            return []
        rows = sorted(
            {
                proxy_model.mapToSource(index).row()
                for index in selection_model.selectedIndexes()
                if index.isValid()
            }
        )
        return [model.records[row] for row in rows if 0 <= row < len(model.records)]

    def _on_row_resized(self, row, oldSize, newSize):
        if not self.proxy_model:
            return
        source_idx = self.proxy_model.mapToSource(self.proxy_model.index(row, 0))
        if source_idx.isValid():
            self.user_resized_rows.add(source_idx.row())

    def _on_column_resized(self, logicalIndex, oldSize, newSize):
        if self._applying_proportional_resize:
            return
        if logicalIndex == StagingColumn.PATH:
            self._resize_timer.start(LIB_TAB_RESIZE_DEBOUNCE_MS)
        self._capture_column_width_ratios()

    def _optimize_row_heights(self):
        if not self.proxy_model:
            return
        view = self.view_table
        rect = view.viewport().rect()
        top_idx = view.indexAt(rect.topLeft())
        bottom_idx = view.indexAt(rect.bottomLeft())
        if not top_idx.isValid():
            return

        start = top_idx.row()
        end = bottom_idx.row() if bottom_idx.isValid() else self.proxy_model.rowCount() - 1
        source_model = self.proxy_model.sourceModel()
        for row in range(start, end + 1):
            source_idx = self.proxy_model.mapToSource(self.proxy_model.index(row, 0))
            if source_idx.isValid() and source_idx.row() not in self.user_resized_rows:
                tags = source_model.index(source_idx.row(), StagingColumn.TAGS).data(Qt.DisplayRole)
                if tags:
                    desired = max(LIB_TAB_ROW_MIN_HEIGHT, view.sizeHintForRow(row))
                    if view.rowHeight(row) < desired:
                        view.setRowHeight(row, desired)
                else:
                    if view.rowHeight(row) < LIB_TAB_ROW_MIN_HEIGHT:
                        view.setRowHeight(row, LIB_TAB_ROW_MIN_HEIGHT)

    def update_header_labels(self):
        if not self.proxy_model:
            return
        source_model = self.proxy_model.sourceModel()
        if not source_model:
            return

        header = self.view_table.horizontalHeader()
        for col in range(len(STAGING_HEADERS)):
            label = STAGING_HEADERS[col]
            if col in self.proxy_model.column_filters:
                label += " *"
            source_model.setHeaderData(col, Qt.Horizontal, label)
        header.viewport().update()

    def copy_selection_to_clipboard(self):
        selection = self.view_table.selectionModel().selectedIndexes()
        if not selection:
            return
        QApplication.clipboard().setText(tab_separated_selection_text(selection, Qt.DisplayRole))

    def copy_rows_as_paths(self, source_rows: list[int]) -> None:
        model = self.proxy_model.sourceModel() if self.proxy_model else None
        paths = paths_for_source_rows(model, source_rows)
        QApplication.clipboard().setText("\n".join(paths))

    def _records_for_source_rows(self, source_rows: list[int]) -> list[object]:
        model = self.proxy_model.sourceModel() if self.proxy_model else None
        return records_for_source_rows(model, source_rows)

    def _opposite_audio_type_for_records(self, records) -> str:
        return opposite_audio_type_for_records(records)

    def set_sources(self, sources: list[Path]):
        self.sidebar.set_sources(sources)
        self.sidebar.setVisible(True)

    def set_saved_filters(self, filters: list[dict]):
        self.saved_filters = list(filters)
        self.sidebar.set_saved_filters(filters)
        self.sidebar.setVisible(True)
        self.refresh_search_suggestions()

    def refresh_search_suggestions(self) -> None:
        if not hasattr(self, "edit_search"):
            return
        saved_queries = saved_filter_queries(self.saved_filters)
        records = self._records_for_search_suggestions()
        self.edit_search.set_suggestions(build_filter_suggestions(records, saved_queries), saved_queries)

    def _records_for_search_suggestions(self) -> list[object]:
        model = self.proxy_model.sourceModel() if self.proxy_model and hasattr(self.proxy_model, "sourceModel") else None
        return list(getattr(model, "records", []) or [])

    def set_possible_duplicate_filter_enabled(self, enabled: bool):
        self.sidebar.set_possible_duplicate_filter_enabled(enabled)

    def set_corrupt_silent_empty_filter_enabled(self, enabled: bool):
        self.sidebar.set_corrupt_silent_empty_filter_enabled(enabled)

    def set_active_saved_filter(self, query: str):
        self.sidebar.set_active_saved_filter(query)

    def set_active_saved_filters(self, queries: set[str]):
        self.sidebar.set_active_saved_filters(queries)

    def set_active_source_filters(self, sources: set[str]):
        self.sidebar.set_active_source_filters(sources)

    def _visible_table_columns(self) -> list[StagingColumn]:
        self.apply_table_column_visibility()
        header = self.view_table.horizontalHeader()
        return visible_table_columns(header.count(), self.view_table.isColumnHidden)

    def _enforce_always_hidden_table_columns(self) -> None:
        if not hasattr(self, "view_table"):
            return
        for column in ALWAYS_HIDDEN_TABLE_COLUMNS:
            self.view_table.setColumnHidden(column, True)

    def apply_table_column_visibility(self) -> None:
        if not hasattr(self, "view_table"):
            return
        header_count = self.view_table.horizontalHeader().count()
        for col in StagingColumn:
            if int(col) >= header_count:
                continue
            hidden = col in ALWAYS_HIDDEN_TABLE_COLUMNS or not load_column_visibility(col)
            self.view_table.setColumnHidden(col, hidden)

    def _connect_column_order_persistence(self) -> None:
        if self._column_order_connected:
            return
        self.view_table.horizontalHeader().sectionMoved.connect(
            lambda logical, old_visual, new_visual: self._save_table_column_order()
        )
        self._column_order_connected = True

    def _save_table_column_order(self) -> None:
        if self._restoring_column_order:
            return
        header = self.view_table.horizontalHeader()
        create_app_settings().setValue(TABLE_COLUMN_ORDER_KEY, encode_column_order(header))

    def _restore_table_column_order(self) -> None:
        if not _qt_alive(self) or not _qt_alive(getattr(self, "view_table", None)):
            return
        self.apply_table_column_visibility()

        raw = create_app_settings().value(TABLE_COLUMN_ORDER_KEY, "")
        if not raw:
            return
        header = self.view_table.horizontalHeader()
        valid = decode_column_order(raw, header.count())
        if valid is None:
            return
        self._restoring_column_order = True
        try:
            for target_visual, logical in enumerate(valid):
                current_visual = header.visualIndex(logical)
                if current_visual != target_visual:
                    header.moveSection(current_visual, target_visual)
        finally:
            self._restoring_column_order = False
        self.apply_table_column_visibility()

    def _default_column_width_ratios(self) -> dict[StagingColumn, float]:
        return default_column_width_ratios(self._visible_table_columns())

    def _capture_column_width_ratios(self):
        cols = self._visible_table_columns()
        if not cols:
            return
        ratios = captured_column_width_ratios(cols, self.view_table.columnWidth)
        if ratios:
            self._column_width_ratios = ratios

    def _apply_proportional_column_widths(self):
        if (
            not _qt_alive(self)
            or not _qt_alive(getattr(self, "lib_stack", None))
            or not _qt_alive(getattr(self, "view_table", None))
        ):
            return
        if self.lib_stack.currentWidget() is not self.view_table:
            return
        self.apply_table_column_visibility()
        cols = self._visible_table_columns()
        if not cols:
            return
        if not self._column_width_ratios or any(col not in self._column_width_ratios for col in cols):
            self._column_width_ratios = self._default_column_width_ratios()

        widths = proportional_column_widths(
            cols,
            self._column_width_ratios,
            self.view_table.viewport().width(),
        )

        current_widths = {col: self.view_table.columnWidth(col) for col in cols}
        if current_widths == widths and self._last_applied_column_widths == widths:
            return

        self._applying_proportional_resize = True
        try:
            for col in cols:
                if self.view_table.columnWidth(col) != widths[col]:
                    self.view_table.setColumnWidth(col, widths[col])
            self._last_applied_column_widths = dict(widths)
        finally:
            self._applying_proportional_resize = False

    def _current_view_scrollbar(self):
        if (
            not _qt_alive(self)
            or not _qt_alive(getattr(self, "lib_stack", None))
            or not _qt_alive(getattr(self, "view_table", None))
        ):
            return None
        if self.lib_stack.currentWidget() is self.tree_page:
            return self.view_tree.verticalScrollBar()
        if self.coherence_map is not None and self.lib_stack.currentWidget() is self.coherence_map:
            return self.coherence_map.verticalScrollBar() if hasattr(self.coherence_map, "verticalScrollBar") else self.view_table.verticalScrollBar()
        return self.view_table.verticalScrollBar()

    def _sync_view_scrollbar(self, *_args):
        if (
            not _qt_alive(self)
            or not _qt_alive(getattr(self, "view_scrollbar", None))
            or not _qt_alive(getattr(self, "lib_stack", None))
        ):
            return
        if self.coherence_map is not None and self.lib_stack.currentWidget() is self.coherence_map:
            self.view_scrollbar.setVisible(False)
            return
        source = self._current_view_scrollbar()
        if source is None or not _qt_alive(source):
            return
        should_show = source.maximum() > source.minimum()
        if (
            self.view_scrollbar.minimum() == source.minimum()
            and self.view_scrollbar.maximum() == source.maximum()
            and self.view_scrollbar.pageStep() == source.pageStep()
            and self.view_scrollbar.singleStep() == source.singleStep()
            and self.view_scrollbar.value() == source.value()
            and self.view_scrollbar.isVisible() == should_show
        ):
            return
        old_state = self.view_scrollbar.blockSignals(True)
        self.view_scrollbar.setRange(source.minimum(), source.maximum())
        self.view_scrollbar.setPageStep(source.pageStep())
        self.view_scrollbar.setSingleStep(source.singleStep())
        self.view_scrollbar.setValue(source.value())
        self.view_scrollbar.setVisible(should_show)
        self.view_scrollbar.blockSignals(old_state)

    def _on_view_scrollbar_value_changed(self, value: int) -> None:
        source = self._current_view_scrollbar()
        if source is not None and source.value() != value:
            source.setValue(value)
