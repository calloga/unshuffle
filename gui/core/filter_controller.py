from PySide6.QtGui import QAction
from PySide6.QtWidgets import QInputDialog, QMenu
from ..utils.constants import COLUMN_CONFIG, HEADER_FILTERABLE_COLUMNS, StagingColumn
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
        if not model or not getattr(self.parent, "proxy_model", None):
            return

        menu = QMenu(self.parent)
        apply_style(menu, menu_style())

        config = COLUMN_CONFIG[StagingColumn(col)]
        current_query = str(getattr(self.parent.search_controller, "current_query", "") or "")
        from gui.core.filter_query import field_filter_values

        active_values = field_filter_values(current_query, config["prefix"])
        choose_act = QAction(f"Filter {config['label']} by value...", self.parent)
        choose_act.triggered.connect(lambda checked=False: self.prompt_header_filter(col))
        menu.addAction(choose_act)

        clear_act = QAction(f"Clear {config['label']} filter", self.parent)
        clear_act.setEnabled(bool(active_values))
        clear_act.triggered.connect(lambda checked=False: self.clear_header_filter(col))
        menu.addAction(clear_act)

        placement = "category" if col == StagingColumn.CATEGORY else "subcategory" if col == StagingColumn.SUBCATEGORY else ""
        custom_options = [
            option
            for option in getattr(self.parent.library_tab, "_custom_tree_filter_options", [])
            if option.placement == placement
        ]
        if custom_options:
            from gui.core.filter_query import query_contains_token

            menu.addSeparator()
            custom_menu = menu.addMenu("Custom Tree")
            for option in custom_options:
                act = QAction(option.label, self.parent)
                act.setCheckable(True)
                act.setChecked(query_contains_token(current_query, option.query))
                act.triggered.connect(
                    lambda checked, selected=option: self.apply_filter_query(
                        selected.query,
                        checked,
                        mode="replace",
                    )
                )
                custom_menu.addAction(act)

        header = self.parent.library_tab.view_table.horizontalHeader()
        menu.exec(header.mapToGlobal(pos))

    def prompt_header_filter(self, col):
        model = getattr(self.parent, "model", None)
        if model is None or not hasattr(model, "get_unique_values"):
            return
        config = COLUMN_CONFIG[StagingColumn(col)]
        values = model.get_unique_values(col)
        if StagingColumn(col) == StagingColumn.CONFIDENCE:
            percentages = set()
            for value in values:
                try:
                    number = float(str(value).strip().rstrip("%"))
                except ValueError:
                    continue
                if number <= 1.0:
                    number *= 100.0
                percentages.add(max(0, min(100, round(number))))
            values = [f"{value}%" for value in sorted(percentages)]
        if not values:
            return
        from gui.core.filter_query import field_filter_values

        current_values = field_filter_values(
            str(getattr(self.parent.search_controller, "current_query", "") or ""),
            config["prefix"],
        )
        current_display = current_values[0] if current_values else ""
        if StagingColumn(col) == StagingColumn.CONFIDENCE and "-" in current_display:
            low, high = current_display.split("-", 1)
            current_display = f"{low}%" if low == high else ""
        current_index = values.index(current_display) if current_display in values else 0
        selected, ok = QInputDialog.getItem(
            self.parent,
            f"Filter {config['label']}",
            "Search or select a value:",
            values,
            current_index,
            True,
        )
        if ok and str(selected or "").strip():
            self._set_header_filter_query(col, str(selected).strip())

    def _set_header_filter_query(self, col, value: str | None) -> None:
        from gui.core.filter_query import replace_field_filter

        config = COLUMN_CONFIG[StagingColumn(col)]
        if value and StagingColumn(col) == StagingColumn.CONFIDENCE:
            percentage = str(value).strip().rstrip("%")
            value = f"{percentage}-{percentage}"
        current_query = str(getattr(self.parent.search_controller, "current_query", "") or "")
        self.parent.search_controller.set_query(
            replace_field_filter(current_query, config["prefix"], value),
            immediate=True,
        )
        self.parent.library_tab.update_header_labels()

    def toggle_column_filter(self, col, value, checked):
        self._set_header_filter_query(col, value if checked else None)

    def clear_header_filter(self, col):
        self._set_header_filter_query(col, None)
