from PySide6.QtWidgets import QMenuBar
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QApplication

from ..utils.constants import STAGING_HEADERS, StagingColumn
from ..styles import (
    DEFAULT_THEME_KEY,
    ASH_THEME_KEY,
    OCEAN_THEME_KEY,
    PEARL_THEME_KEY,
    SUNSET_THEME_KEY,
)

class ModernMenuBar(QMenuBar):
    """
    Main application menu bar.
    """
    syncRequested = Signal()
    toggleViewRequested = Signal()
    saveViewDefaultRequested = Signal()
    toggleDockedRequested = Signal(bool)
    undoRequested = Signal()
    redoRequested = Signal()
    zoomRequested = Signal(int)
    themeRequested = Signal(str)
    libraryViewAvailabilityRequested = Signal(str, bool)
    startupLauncherVisibilityRequested = Signal(bool)
    tableColumnVisibilityRequested = Signal(int, bool)
    showNonAudioAssetsRequested = Signal(bool)
    libraryRequested = Signal()
    systemRequested = Signal()
    historyRequested = Signal()
    systemTaxonomyDryRunRequested = Signal()
    systemTaxonomyRescanRequested = Signal()
    systemTaxonomyResetWeightsRequested = Signal()
    systemTaxonomyRefreshConflictsRequested = Signal()
    systemTaxonomySyncApplyRequested = Signal()
    treeOrganizationEditRequested = Signal()
    
    libraryAboutToShow = Signal()
    buildAboutToShow = Signal()
    selectionAboutToShow = Signal()
    systemAboutToShow = Signal()
    historyAboutToShow = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self):
        self.menu_library = self.addMenu("Library")
        self.menu_library.aboutToShow.connect(self.libraryAboutToShow.emit)
        self.act_open_library = QAction("Library", self)
        self.act_open_library.setShortcut("Ctrl+L")
        self.act_open_library.triggered.connect(self.libraryRequested.emit)

        self.menu_build = self.addMenu("Build")
        self.menu_build.aboutToShow.connect(self.buildAboutToShow.emit)

        self.menu_system = self.addMenu("System")
        self.menu_system.aboutToShow.connect(self.systemAboutToShow.emit)
        self.menu_selection = self.addMenu("Selection")
        self.menu_selection.aboutToShow.connect(self.selectionAboutToShow.emit)

        self.menu_view = self.addMenu("View")
        self.menu_history = self.addMenu("History")
        self.menu_history.aboutToShow.connect(self.historyAboutToShow.emit)

        self.act_sync = QAction("Load Target Drive Index", self)
        self.act_sync.triggered.connect(self.syncRequested.emit)

        self.menu_preferences = self.menu_view.addMenu("Preferences")
        self.menu_view.addSeparator()
        self.menu_library_views = self.menu_view.addMenu("Library Views")

        self.act_toggle_view = QAction("Switch View Mode (Table/Tree/Map)", self)
        self.act_toggle_view.setShortcut("Ctrl+T")
        self.act_toggle_view.triggered.connect(self.toggleViewRequested.emit)
        self.menu_library_views.addAction(self.act_toggle_view)

        self.act_save_view = QAction("Set Current View as Default", self)
        self.act_save_view.triggered.connect(self.saveViewDefaultRequested.emit)
        self.menu_library_views.addAction(self.act_save_view)

        self._library_views_save_separator = self.menu_library_views.addSeparator()
        self.act_docked = QAction("Docked Mode", self)
        self.act_docked.setCheckable(True)
        self.act_docked.setShortcut("Ctrl+D")
        self.act_docked.triggered.connect(lambda checked: self.toggleDockedRequested.emit(checked))
        self.menu_library_views.addAction(self.act_docked)

        self._library_views_mode_separator = self.menu_library_views.addSeparator()
        
        self.menu_view_table = self.menu_library_views.addMenu("Table")
        self.menu_view_tree = self.menu_library_views.addMenu("Tree")
        self.menu_view_map = self.menu_library_views.addMenu("Map")
        
        self.library_view_actions = {}
        
        # Table Submenu items
        act_table = QAction("Show Table", self)
        act_table.setCheckable(True)
        act_table.setChecked(True)
        act_table.triggered.connect(
            lambda checked=False: self.libraryViewAvailabilityRequested.emit("table", checked)
        )
        self.menu_view_table.addAction(act_table)
        self.library_view_actions["table"] = act_table
        
        # Tree Submenu items
        act_tree = QAction("Show Tree", self)
        act_tree.setCheckable(True)
        act_tree.setChecked(True)
        act_tree.triggered.connect(
            lambda checked=False: self.libraryViewAvailabilityRequested.emit("tree", checked)
        )
        self.menu_view_tree.addAction(act_tree)
        self.library_view_actions["tree"] = act_tree
        
        self.act_edit_tree_org = QAction("Edit Tree Organization", self)
        self.act_edit_tree_org.triggered.connect(self.treeOrganizationEditRequested.emit)
        self.menu_view_tree.addAction(self.act_edit_tree_org)
        
        # Map Submenu items
        act_map = QAction("Show Map", self)
        act_map.setCheckable(True)
        act_map.setChecked(True)
        act_map.triggered.connect(
            lambda checked=False: self.libraryViewAvailabilityRequested.emit("map", checked)
        )
        self.menu_view_map.addAction(act_map)
        self.library_view_actions["map"] = act_map

        self.act_show_startup_launcher = QAction("Show Startup Launcher", self)
        self.act_show_startup_launcher.setCheckable(True)
        self.act_show_startup_launcher.setChecked(True)
        self.act_show_startup_launcher.triggered.connect(
            lambda checked=False: self.startupLauncherVisibilityRequested.emit(
                self.act_show_startup_launcher.isChecked()
            )
        )
        self.menu_preferences.addAction(self.act_show_startup_launcher)

        self.act_show_non_audio = QAction("Show Non-Audio Assets", self)
        self.act_show_non_audio.setCheckable(True)
        self.act_show_non_audio.setChecked(False)
        self.act_show_non_audio.triggered.connect(
            lambda checked=False: self.showNonAudioAssetsRequested.emit(self.act_show_non_audio.isChecked())
        )
        self.menu_preferences.addAction(self.act_show_non_audio)

        self.menu_preferences.addSeparator()
        self.menu_theme = self.menu_preferences.addMenu("Theme")
        self.theme_actions = {}
        for key, label in (
            (DEFAULT_THEME_KEY, "Default"),
            (ASH_THEME_KEY, "Ash"),
            (SUNSET_THEME_KEY, "Sunset"),
            (OCEAN_THEME_KEY, "Ocean"),
            (PEARL_THEME_KEY, "Pearl"),
        ):
            act = QAction(label, self)
            act.setCheckable(True)
            act.triggered.connect(lambda checked=False, t=key: self.themeRequested.emit(t))
            self.menu_theme.addAction(act)
            self.theme_actions[key] = act

        self.menu_zoom = self.menu_preferences.addMenu("Zoom")
        self.zoom_actions = {}
        for zoom in (90, 100, 110, 125):
            act = QAction(f"{zoom}%", self)
            act.setCheckable(True)
            act.triggered.connect(lambda checked=False, z=zoom: self.zoomRequested.emit(z))
            self.menu_zoom.addAction(act)
            self.zoom_actions[zoom] = act

        self.menu_view_table.addSeparator()
        self.menu_table_columns = self.menu_view_table.addMenu("Table Columns")
        self.table_column_actions = {}
        from .library_columns import load_column_visibility
        for column in sorted(StagingColumn):
            act = QAction(STAGING_HEADERS[column], self)
            act.setCheckable(True)
            act.setChecked(load_column_visibility(column))
            act.triggered.connect(
                lambda checked=False, col=int(column): self.tableColumnVisibilityRequested.emit(col, checked)
            )
            self.menu_table_columns.addAction(act)
            self.table_column_actions[column] = act

        self.act_open_system = QAction("System", self)
        self.act_open_system.setShortcut("Ctrl+Shift+T")
        self.act_open_system.triggered.connect(self.systemRequested.emit)
        self.menu_system.addAction(self.act_open_system)

        self.act_open_history = QAction("History", self)
        self.act_open_history.setShortcut("Ctrl+Shift+H")
        self.act_open_history.triggered.connect(self.historyRequested.emit)
        self.menu_history.addAction(self.act_open_history)

        self.act_undo = QAction("Undo", self)
        self.act_undo.setShortcuts(QKeySequence.keyBindings(QKeySequence.Undo))
        self.act_undo.triggered.connect(self.undoRequested.emit)

        self.act_redo = QAction("Redo", self)
        redo_shortcuts = list(QKeySequence.keyBindings(QKeySequence.Redo))
        for seq in ("Ctrl+Y", "Ctrl+Shift+Z"):
            key = QKeySequence(seq)
            if all(existing != key for existing in redo_shortcuts):
                redo_shortcuts.append(key)
        self.act_redo.setShortcuts(redo_shortcuts)
        self.act_redo.triggered.connect(self.redoRequested.emit)

    def set_docked_checked(self, checked):
        self.act_docked.setChecked(checked)
        for action in (
            self.act_save_view,
            self._library_views_save_separator,
            self._library_views_mode_separator,
            self.menu_view_table.menuAction(),
            self.menu_view_tree.menuAction(),
            self.menu_view_map.menuAction(),
        ):
            action.setVisible(not checked)

    def set_zoom_checked(self, zoom_percent: int):
        for zoom, act in self.zoom_actions.items():
            act.setChecked(zoom == zoom_percent)

    def set_theme_checked(self, theme_key: str):
        for key, act in self.theme_actions.items():
            act.setChecked(key == theme_key)

    def set_table_column_checked(self, column: int, checked: bool):
        action = self.table_column_actions.get(StagingColumn(column))
        if action is not None:
            action.setChecked(checked)

    def set_library_view_available(self, mode: str, available: bool):
        action = self.library_view_actions.get((mode or "").lower())
        if action is not None:
            action.setChecked(available)

    def set_startup_launcher_visible(self, enabled: bool):
        self.act_show_startup_launcher.setChecked(enabled)

    def refresh_theme(self) -> None:
        app = QApplication.instance()
        if app is not None:
            self.setFont(app.font())
        self.updateGeometry()
        self.update()
