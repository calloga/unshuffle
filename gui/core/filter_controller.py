from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QInputDialog, QMenu
from ..utils.constants import HEADER_FILTERABLE_COLUMNS
from ..utils.styles import menu_style
from ..utils.style_helpers import apply_style

class FilterController:
    """
    Handles saved filters, quick filters, and header-based filtering.
    """
    def __init__(self, settings_controller, parent=None):
        self.settings_controller = settings_controller
        self.parent = parent

    def refresh_dock_filters(self):
        """Populates DockView filter carousel with saved filters and source roots."""
        from pathlib import Path
        from gui.widgets.sidebar import POSSIBLE_DUPLICATE_FILTER_NAME, POSSIBLE_DUPLICATE_FILTER_QUERY

        options = []
        
        if self.parent.engine and hasattr(self.parent.engine, "session_source_roots"):
            for root in self.parent.engine.session_source_roots:
                name = Path(root).name
                query = f'source:"{root}"'
                options.append((f"Dir: {name}", query))
        
        filters = self.settings_controller.get_saved_filters()
        sidebar = getattr(getattr(self.parent, "library_tab", None), "sidebar", None)
        if bool(getattr(sidebar, "corrupt_silent_empty_filter_enabled", False)):
            from gui.widgets.sidebar import CORRUPT_SILENT_EMPTY_FILTER_NAME, CORRUPT_SILENT_EMPTY_FILTER_QUERY
            options.append((f"Filter: {CORRUPT_SILENT_EMPTY_FILTER_NAME}", CORRUPT_SILENT_EMPTY_FILTER_QUERY))
        if bool(getattr(sidebar, "possible_duplicate_filter_enabled", False)):
            options.append((f"Filter: {POSSIBLE_DUPLICATE_FILTER_NAME}", POSSIBLE_DUPLICATE_FILTER_QUERY))
        for f in filters:
            name = f.get("name", "Unnamed")
            query = f.get("query", "")
            options.append((f"Filter: {name}", query))
            
        if hasattr(self.parent, "dock_view"):
            self.parent.dock_view.set_filters(options)

    def prompt_save_filter(self, name, query):
        query = str(query or "").strip()
        if not query:
            return
        new_name, ok = QInputDialog.getText(self.parent, "Save Filter", "Filter Name:", text=str(name or "").strip() or query)
        if ok and new_name:
            self.add_saved_filter(new_name, query)

    def add_saved_filter(self, name, query):
        if self.settings_controller.add_filter(name, query):
            filters = self.settings_controller.get_saved_filters()
            if hasattr(self.parent, "library_tab"):
                self.parent.library_tab.set_saved_filters(filters)
            self.refresh_dock_filters()

    def remove_saved_filter(self, query):
        if self.settings_controller.remove_filter(query):
            updated = self.settings_controller.get_saved_filters()
            if hasattr(self.parent, "library_tab"):
                self.parent.library_tab.set_saved_filters(updated)
            self.refresh_dock_filters()

    def handle_saved_filter(self, query, is_active, mode="replace"):
        self.apply_filter_query(query, is_active, mode=mode)

    def handle_quick_filter(self, query, mode="replace"):
        self.apply_filter_query(query, True, mode=mode)

    def apply_filter_query(self, query: str, is_active: bool, mode: str = "replace"):
        effective_mode = "and" if mode == "append" else mode
        self.parent.search_controller.apply_filter(query, is_active, mode=effective_mode)

    def show_header_menu(self, col, pos):
        if col not in HEADER_FILTERABLE_COLUMNS:
            return
        model = getattr(self.parent, "model", None)
        proxy_model = getattr(self.parent, "proxy_model", None)
        if not model or not proxy_model:
            return

        if hasattr(model, "get_unique_values"):
            unique_vals = model.get_unique_values(col)
        else:
            unique_vals = sorted(
                {
                    str(value)
                    for row in range(model.rowCount())
                    for value in [model.index(row, col).data(Qt.DisplayRole)]
                    if value
                }
            )

        menu = QMenu(self.parent)
        apply_style(menu, menu_style())

        all_act = QAction("All", self.parent)
        all_act.triggered.connect(lambda checked=False: self.clear_header_filter(col))
        menu.addAction(all_act)
        menu.addSeparator()

        active_values = set(proxy_model.column_filters.get(col, set()))
        for value in unique_vals:
            act = QAction(value, self.parent)
            act.setCheckable(True)
            act.setChecked(value in active_values)
            act.triggered.connect(lambda checked, selected=value: self.toggle_column_filter(col, selected, checked))
            menu.addAction(act)

        header = self.parent.library_tab.view_table.horizontalHeader()
        menu.exec(header.mapToGlobal(pos))

    def toggle_column_filter(self, col, value, checked):
        current = set(self.parent.proxy_model.column_filters.get(col, set()))
        if checked:
            current.add(value)
        else:
            current.discard(value)
        self.parent.proxy_model.set_column_filters(col, current or None)
        self.parent.view_controller.update_library_views(tree_delay_ms=0)
        self.parent.library_tab.update_header_labels()

    def clear_header_filter(self, col):
        self.parent.proxy_model.set_column_filters(col, None)
        self.parent.view_controller.update_library_views(tree_delay_ms=0)
        self.parent.library_tab.update_header_labels()
