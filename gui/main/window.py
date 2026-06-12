import logging
import time

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtGui import QUndoStack
from PySide6.QtWidgets import QApplication, QDialog, QFrame, QGridLayout, QHBoxLayout, QMainWindow, QMessageBox, QPushButton, QStackedWidget, QVBoxLayout, QWidget
import shiboken6

from unshuffle.core.constants import APP_NAME, APP_VERSION
from ..core import (
    AcousticController,
    AudioController,
    DataManager,
    DraftingController,
    FilterController,
    SearchController,
    SettingsController,
    SystemController,
    TaggingController,
    CoherenceController,
    TreeOrganizationController,
    ViewController,
    WorkerManager,
    WorkflowController,
    create_app_settings,
)
from ..core.acoustic_session_state import AcousticSessionState
from ..models import MultiFilterProxyModel
from ..styles import ThemeManager
from ..styles.tokens_geometry import (
    MAIN_LAYOUT_MARGIN_NONE,
    WINDOW_MIN_HEIGHT,
    WINDOW_MIN_WIDTH,
)
from ..utils import ui_helpers
from ..utils.app_icon import apply_app_icon
from ..utils.constants import MAIN_WINDOW_HEIGHT, MAIN_WINDOW_WIDTH
from ..utils.styles import (
    ColorPalette,
    apply_style,
    button_style,
    scaled_px,
)
from ..utils.layout_helpers import apply_layout_margins
from ..utils.layout_helpers import apply_layout_spacing
from ..utils.widget_helpers import apply_minimum_height, apply_minimum_width
from ..views import DockView
from ..widgets import BuildPage, HistoryPage, LibraryTab, ModernFooter, ModernMenuBar, SidebarCarousel, SystemPage, VibeAnchorBar
from ..widgets.preview_control_bar import PreviewControlBar
from . import window_state
from . import window_navigation
from . import window_lifecycle
from . import window_runtime
from . import window_search
from . import window_startup
from . import window_theme
from . import window_workspace


class ModernApp(QMainWindow):
    """
    Main application orchestrator.
    """
    _UNSET = object()

    def __init__(self, *, defer_startup_restore: bool = False):
        super().__init__()
        self._defer_window_show = defer_startup_restore
        self.setWindowTitle(f"{APP_NAME} v{APP_VERSION}")
        apply_app_icon(self)
        self.resize(MAIN_WINDOW_WIDTH, MAIN_WINDOW_HEIGHT)
        self.setAcceptDrops(True)

        self.settings = create_app_settings()
        self.settings_controller = SettingsController(self.settings, self)
        self.undo_stack = QUndoStack(self)
        self._is_closing = False
        self.engine = None
        self.model = None
        self.proxy_model = MultiFilterProxyModel(self)
        self.theme_manager = ThemeManager()
        self._base_font = self.font()

        self.data_manager = DataManager(app=self)
        self.search_controller = SearchController(self.engine, self.model, self.proxy_model, self)
        self.worker_manager = WorkerManager(self)
        self.workflow_controller = WorkflowController(self.engine, self.worker_manager, self.undo_stack, self)
        self.drafting_controller = DraftingController(self)
        self.acoustic_controller = AcousticController(self.model, self.proxy_model, self)
        self.filter_controller = FilterController(self.settings_controller, self)
        self.view_controller = ViewController(self)
        self._tree_rebuild_timer = self.view_controller._tree_rebuild_timer
        self.system_controller = None
        self.tagging_controller = TaggingController(self)
        self.coherence_controller = CoherenceController(self)
        self.acoustic_session_state = AcousticSessionState(self)
        self.tree_organization_controller = TreeOrganizationController(self)
        self._page_history: list[tuple[str, str | None]] = []
        self._page_history_index = -1
        self._suppress_page_history = False
        self._page_persistence_enabled = False
        self._library_page_state_persistence_enabled = False
        self._restoring_library_page_state = False
        self._frontloading_startup = False
        self._scan_finalizing = False
        self._build_page_signature = None

        self._setup_ui()
        apply_minimum_width(self, WINDOW_MIN_WIDTH)
        apply_minimum_height(self, WINDOW_MIN_HEIGHT)
        self.library_tab.set_proxy_model(self.proxy_model)
        self.audio_controller = AudioController(self.audio_bar, self)
        self._native_theme_filter = _NativeWindowThemeFilter(self)
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self._native_theme_filter)

        self.apply_app_settings(self.settings_controller.load_app_settings())
        self._library_page_state_persistence_enabled = True
        ui_helpers.connect_orchestrator_signals(self)
        if not defer_startup_restore:
            QTimer.singleShot(100, self.workflow_controller.restore_session)

    def frontload_startup(self, status_callback=None, done_callback=None) -> None:
        window_startup.frontload_startup(self, status_callback, done_callback)

    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        apply_layout_spacing(main_layout, MAIN_LAYOUT_MARGIN_NONE)
        apply_layout_margins(
            main_layout,
            (
                MAIN_LAYOUT_MARGIN_NONE,
                MAIN_LAYOUT_MARGIN_NONE,
                MAIN_LAYOUT_MARGIN_NONE,
                MAIN_LAYOUT_MARGIN_NONE,
            ),
        )

        self.stack = QStackedWidget()
        self.library_tab = LibraryTab(self.undo_stack)
        self.dock_view = DockView(self.library_tab.tree_model)
        self.system_page = SystemPage(self)
        self.history_page = HistoryPage(self)
        self.build_page = None

        self.stack.addWidget(self.library_tab)
        self.stack.addWidget(self.dock_view)
        self.stack.addWidget(self.system_page)
        self.stack.addWidget(self.history_page)
        self.page_nav_bar = self._build_page_nav_bar()
        main_layout.addWidget(self.page_nav_bar)
        main_layout.addWidget(self.stack)

        self.vibe_bar = VibeAnchorBar()
        self.vibe_bar.setVisible(False)
        main_layout.addWidget(self.vibe_bar)

        self.audio_bar = PreviewControlBar()
        main_layout.addWidget(self.audio_bar)

        self.footer = ModernFooter()
        main_layout.addWidget(self.footer)

        self.custom_menu_bar = ModernMenuBar(self)
        self.setMenuBar(self.custom_menu_bar)
        self.system_controller = SystemController(self, self.system_page)
        self.history_page.undoRequested.connect(lambda session: self._confirm_history_undo(session))
        self.history_page.clearRequested.connect(self._clear_history_page)
        self.system_page.treeOrganizationRequested.connect(self.tree_organization_controller.open_editor)
        self.system_page.sectionChanged.connect(lambda _section: self._record_current_page())
        self.stack.currentChanged.connect(self._on_stack_current_changed)

        ui_helpers.setup_global_actions(self)
        self._record_current_page()

    def _build_page_nav_bar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("PageNavBar")
        layout = QGridLayout(bar)
        apply_layout_margins(layout, (12, 5, 12, 5))
        apply_layout_spacing(layout, 8)
        self.btn_previous_page = QPushButton("Previous")
        self.btn_next_page = QPushButton("Next")
        self.btn_previous_page.setObjectName("PageNavButton")
        self.btn_next_page.setObjectName("PageNavButton")
        self.page_carousel = SidebarCarousel(
            "",
            [
                ("Library", "library"),
                ("Build", "build"),
                ("System", "system"),
                ("History", "history"),
            ],
            inactive_text="",
            compact=True,
            toggleable=False,
        )
        self.page_carousel.setObjectName("PageCarousel")
        self.page_carousel.btn_title.hide()
        self.page_carousel.value_row.setFixedWidth(scaled_px(160))
        self.page_carousel.value_row.setMinimumWidth(scaled_px(160))
        self.page_carousel.value_row.setMaximumWidth(scaled_px(160))
        self.page_carousel.valueSelected.connect(self._on_page_carousel_selected)
        self.btn_previous_page.clicked.connect(self.go_to_previous_page)
        self.btn_next_page.clicked.connect(self.go_to_next_page)
        layout.setColumnStretch(0, 1)
        layout.setColumnStretch(1, 0)
        layout.setColumnStretch(2, 1)
        layout.addWidget(self.btn_previous_page, 0, 0, Qt.AlignLeft | Qt.AlignVCenter)
        layout.addWidget(self.page_carousel, 0, 1, Qt.AlignCenter)
        layout.addWidget(self.btn_next_page, 0, 2, Qt.AlignRight | Qt.AlignVCenter)
        self._style_page_nav_bar()
        return bar

    def _style_page_nav_bar(self) -> None:
        if not getattr(self, "page_nav_bar", None):
            return
        apply_style(
            self.page_nav_bar,
            (
                f"QFrame#PageNavBar {{ background: {ColorPalette.BG_DARK}; border: none; }}"
                f"{button_style('secondary', size='compact')}"
                f"QPushButton#PageNavButton:disabled {{ background: transparent; color: {ColorPalette.TEXT_DIM}; }}"
            ),
        )
        self._style_page_carousel()
        self._refresh_page_nav_buttons()

    def _style_page_carousel(self) -> None:
        if not getattr(self, "page_carousel", None):
            return
        arrow_pad = scaled_px(28)
        apply_style(
            self.page_carousel.value_row,
            f"QFrame {{ background: {ColorPalette.BG_LIST}; border-radius: {scaled_px(4)}px; border: none; }}",
        )
        apply_style(
            self.page_carousel.btn_value,
            (
                f"QPushButton {{ background: transparent; color: {ColorPalette.TEXT_LIGHT}; border: none; "
                f"border-radius: {scaled_px(4)}px; padding: 0 {arrow_pad}px; text-align: center; "
                f"font-size: {scaled_px(12)}px; font-weight: bold; }}"
                f"QPushButton:hover {{ background: {ColorPalette.TABLE_HOVER}; }}"
            ),
        )

    def _record_current_page(self) -> None:
        window_navigation.record_current_page(self)

    def _on_stack_current_changed(self, _index: int) -> None:
        self._record_current_page()
        if self.isVisible():
            QTimer.singleShot(0, lambda: self._refresh_theme_bindings(visible_only=True))

    def _persist_current_page(self, key: tuple[str, str | None]) -> None:
        window_navigation.persist_current_page(self, key)

    def _current_page_key(self) -> tuple[str, str | None] | None:
        return window_navigation.current_page_key(self)

    def _refresh_page_nav_buttons(self) -> None:
        window_navigation.refresh_page_nav_buttons(self)

    def _on_page_carousel_selected(self, page: str) -> None:
        window_navigation.select_carousel_page(self, page)

    def go_to_previous_page(self) -> None:
        window_navigation.go_to_previous_page(self)

    def go_to_next_page(self) -> None:
        window_navigation.go_to_next_page(self)

    def _activate_page_key(self, key: tuple[str, str | None]) -> None:
        window_navigation.activate_page_key(self, key)

    def _update_library_views(self):
        if getattr(self, "library_tab", None):
            self.view_controller.update_footer_count()
            self.library_tab.refresh_table_viewport()
        self.view_controller.schedule_tree_rebuild(delay_ms=60)

    def _ensure_library_map(self):
        page = self.library_tab.ensure_coherence_map()
        if getattr(page, "_app_signals_connected", False):
            return page
        page.runCoherenceRequested.connect(lambda: self.coherence_controller.start_coherence_audit(force=True, mode="manual"))
        page.continuousRefinementRequested.connect(self.coherence_controller.start_continuous_refinement)
        page.autoCheckChanged.connect(self.settings_controller.set_auto_check_coherence_on_start)
        page.audioPreviewRequested.connect(self.coherence_controller.preview_audio_path)
        page.anchorRequested.connect(self.coherence_controller.promote_record_as_anchor)
        page.findRequested.connect(self.coherence_controller.find_audio_path)
        setattr(page, "_app_signals_connected", True)
        return page

    def set_library_view_available(self, mode: str, available: bool) -> None:
        mode = (mode or "").strip().lower()
        if mode not in {"table", "tree", "map"}:
            return
        modes = self.library_tab.available_view_modes()
        if available:
            modes.add(mode)
        elif len(modes) > 1:
            modes.discard(mode)
        else:
            available = True
        self.library_tab.set_available_view_modes(modes)
        if hasattr(self, "dock_view"):
            self.dock_view.set_map_available("map" in modes)
        self.settings_controller.set_library_view_modes(self.library_tab.available_view_modes())
        self.custom_menu_bar.set_library_view_available(mode, available)
        for view_mode in ("table", "tree", "map"):
            self.custom_menu_bar.set_library_view_available(view_mode, self.library_tab.is_view_available(view_mode))

    def set_startup_launcher_visible(self, enabled: bool) -> None:
        self.settings_controller.set_show_startup_launcher(enabled)
        self.custom_menu_bar.set_startup_launcher_visible(enabled)

    def apply_app_settings(self, state: dict) -> None:
        window_state.apply_app_settings(self, state)

    def _restore_current_page(self, state: dict) -> None:
        window_state.restore_current_page(self, state)

    def save_library_page_state(self) -> None:
        window_state.save_library_page_state(self)

    def restore_library_page_state(self) -> None:
        window_state.restore_library_page_state(self)

    def apply_theme(self, theme_key: str) -> None:
        window_theme.apply_theme(self, theme_key)

    def _apply_theme_stylesheet(self, qss: str, app: object | None) -> None:
        window_theme.apply_theme_stylesheet(self, qss, app)

    def apply_zoom(self, zoom_percent: int) -> None:
        window_theme.apply_zoom(self, zoom_percent)

    def _refresh_theme_bindings(self, *, visible_only: bool = False) -> None:
        window_theme.refresh_theme_bindings(self, visible_only=visible_only)

    def _apply_native_window_theme(self, widget: QWidget | None = None) -> None:
        window_theme.apply_native_window_theme(self, widget)

    def set_runtime_context(self, *, engine=_UNSET, model=_UNSET) -> None:
        if engine is not self._UNSET:
            window_runtime.apply_runtime_engine(self, engine)

        if model is not self._UNSET:
            window_runtime.apply_runtime_model(self, model)

    def _should_auto_check_coherence_on_start(self) -> bool:
        return window_workspace.should_auto_check_coherence_on_start(self)

    def _is_library_map_enabled(self) -> bool:
        return window_workspace.is_library_map_enabled(self)

    def _should_prepare_sound_map(self) -> bool:
        return window_workspace.should_prepare_sound_map(self)

    def open_system_workspace(self, section: str | None = None) -> None:
        window_workspace.open_system_workspace(self, section)

    def open_coherence_map(self) -> None:
        window_workspace.open_coherence_map(self)

    def open_library_workspace(self) -> None:
        window_workspace.open_library_workspace(self)

    def open_history_workspace(self) -> None:
        window_workspace.open_history_workspace(self)

    def show_about(self) -> None:
        from ..dialogs.about_dialog import show_about

        show_about(self)

    def _refresh_history_page(self) -> None:
        window_workspace.refresh_history_page(self)

    def _confirm_history_undo(self, session: dict) -> None:
        from .actions.history import confirm_undo

        confirm_undo(self, session)

    def _clear_history_page(self) -> None:
        from .actions.history import clear_history

        clear_history(self)
        self._refresh_history_page()

    def open_build_workspace(self) -> None:
        window_workspace.open_build_workspace(self, build_page_cls=BuildPage, message_box=QMessageBox)

    def _build_workspace_signature(self, records, source_roots, active_profile) -> tuple:
        return window_workspace.build_workspace_signature(records, source_roots, active_profile)

    def current_records(self):
        return window_search.current_records(self)

    def set_search_status(self, text: str) -> None:
        window_search.set_search_status(self, text)

    def handle_search_results_applied(self) -> None:
        window_search.handle_search_results_applied(self)

    def schedule_search_tree_refresh(self, delay_ms: int = 0) -> None:
        window_search.schedule_search_tree_refresh(self, delay_ms)

    def sync_search_ui_state(
        self,
        *,
        query: str,
        active_saved_filters,
        active_source_filters,
        active_categories,
        confidence_range,
    ) -> None:
        window_search.sync_search_ui_state(
            self,
            query=query,
            active_saved_filters=active_saved_filters,
            active_source_filters=active_source_filters,
            active_categories=active_categories,
            confidence_range=confidence_range,
        )

    def sync_type_filter_state(self) -> None:
        window_search.sync_type_filter_state(self)

    def selected_records(self):
        return window_search.selected_records(self)

    def selected_record(self):
        return window_search.selected_record(self)

    def _on_confidence_range_changed(self, min_val: float, max_val: float):
        self.search_controller.set_confidence_range(min_val, max_val)

    def _on_sort_changed(self, sort_index: int):
        self.library_tab.set_sort_index(sort_index)
        self.view_controller.apply_current_sort_state()

    def showEvent(self, event):
        super().showEvent(event)
        window_lifecycle.resize_for_show(self)

    def closeEvent(self, event):
        self._is_closing = True
        if self.drafting_controller.has_changes():
            reply = QMessageBox.question(
                self,
                "Unsaved Changes",
                "Discard pending changes and close?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                self._is_closing = False
                event.ignore()
                return

        window_lifecycle.save_settings_for_close(self)
        window_lifecycle.close_engine_for_shutdown(self)
        super().closeEvent(event)
        window_lifecycle.maybe_quit_after_close()


class _NativeWindowThemeFilter(QObject):
    """Applies native frame theming to newly shown top-level dialogs/windows."""

    def __init__(self, app_window: ModernApp):
        super().__init__(app_window)
        self._app_window = app_window

    def eventFilter(self, watched, event):
        if watched is QApplication.instance() and self._app_window.theme_manager.state.follow_system:
            if event.type() in (QEvent.ApplicationPaletteChange, QEvent.ThemeChange):
                self._app_window.apply_theme("system")
        if event.type() == QEvent.Show and isinstance(watched, QWidget) and watched.isWindow():
            from ..widgets.startup_splash import StartupSplash
            from ..widgets.startup_scan_monitor import StartupScanMonitor
            from PySide6.QtWidgets import QMainWindow, QDialog, QMessageBox
            if isinstance(watched, (QMainWindow, QDialog, QMessageBox, StartupScanMonitor)) and not isinstance(watched, StartupSplash):
                apply_app_icon(watched)
                self._app_window._apply_native_window_theme(watched)
                QTimer.singleShot(50, lambda: self._app_window._apply_native_window_theme(watched) if shiboken6.isValid(watched) else None)
        return super().eventFilter(watched, event)
