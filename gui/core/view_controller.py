import logging
import time
from collections.abc import Sized
from typing import cast

from PySide6.QtCore import QObject, Signal, Qt, QTimer
from PySide6.QtWidgets import QWidget
from ..utils.tree_helpers import run_draft_tree_refresh, get_tree_branch_path_for_record
from ..utils.constants import (
    DOCKED_MAXIMUM_WIDTH,
    DOCKED_MINIMUM_HEIGHT,
    DOCKED_WINDOW_WIDTH,
    MAIN_WINDOW_HEIGHT,
    MAIN_WINDOW_WIDTH,
    StagingColumn,
)
from ..utils.widget_helpers import apply_minimum_width
from .view_modes import filtered_source_records, next_view_mode, normalize_library_tab_mode, normalize_view_mode

class ViewController(QObject):
    """
    Handles UI shell state, view modes, and orchestrates view refreshes.
    """
    viewModeChanged = Signal(bool)
    dockedChanged = Signal(bool)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.app = parent
        self._tree_rebuild_timer = QTimer(self)
        self._tree_rebuild_timer.setSingleShot(True)
        self._tree_rebuild_timer.timeout.connect(self.do_tree_rebuild)
        self._tree_rebuild_pending = False
        self._map_prewarm_scheduled = False
        self._map_prewarm_waiting_for_coherence = False
        self._pending_map_prewarm = False
        self._pending_duplicate_map_prewarm = False
        self._tree_prewarm_scheduled = False
        self._tree_build_signature = None
        self._normal_window_flags = None

    def apply_current_sort_state(self, *, force: bool = False):
        """Applies sidebar sort using source-model ordering, then refreshes views."""
        if not self.app.proxy_model:
            return
        drafting = getattr(self.app, "drafting_controller", None)
        if not force and drafting is not None and drafting.has_changes():
            self.update_library_views(tree_delay_ms=0)
            return
        try:
            lt = self.app.library_tab
            sort_idx = lt.combo_sort.currentIndex()
        except RuntimeError:
            return
        if sort_idx < 0 or sort_idx >= len(lt.sort_columns):
            self._set_tree_sort_column(StagingColumn.FILENAME)
            self.app.proxy_model.sort(StagingColumn.FILENAME.value, Qt.AscendingOrder)
            self.update_library_views(tree_delay_ms=0)
            return
        column = lt.sort_columns[sort_idx]
        model = getattr(self.app, "model", None)
        set_group_column = getattr(model, "set_group_column", None)
        if callable(set_group_column):
            set_group_column(column)
        self._set_tree_sort_column(column)
        if hasattr(self.app.proxy_model, "sortColumn") and self.app.proxy_model.sortColumn() != -1:
            self.app.proxy_model.sort(-1)
        self.update_library_views(tree_delay_ms=0)

    def _set_tree_sort_column(self, column: StagingColumn | int) -> None:
        tree_model = getattr(getattr(self.app, "library_tab", None), "tree_model", None)
        set_sort_column = getattr(tree_model, "set_sort_column", None)
        if callable(set_sort_column):
            set_sort_column(column)

    def set_view_mode(self, mode):
        view_mode = self._normalize_view_mode(mode)
        self.app.library_tab.set_view_mode(view_mode)
        self.viewModeChanged.emit(view_mode == "tree")
        if view_mode == "tree":
            has_library_data = bool(getattr(self.app, "model", None) and getattr(self.app, "proxy_model", None))
            if self._tree_rebuild_pending or (has_library_data and self._tree_build_signature != self._current_tree_signature()):
                self.schedule_tree_rebuild(delay_ms=0)
        elif view_mode == "map":
            QTimer.singleShot(0, self.refresh_library_map)
        elif view_mode == "table":
            self.update_library_views(tree_delay_ms=0)
        if hasattr(self.app, "save_library_page_state"):
            self.app.save_library_page_state()

    def _normalize_view_mode(self, mode) -> str:
        first_available = getattr(self.app.library_tab, "first_available_view_mode", None)
        return normalize_view_mode(
            mode,
            self._view_available,
            first_available if callable(first_available) else lambda: "table",
        )

    def cycle_view_mode(self) -> None:
        self.set_view_mode(next_view_mode(self.app.library_tab.current_view_mode(), self._view_available))

    def refresh_library_map(self, *, force: bool = False) -> None:
        if self._coherence_is_running():
            self._defer_map_until_coherence(duplicate=False)
            return
        started = time.perf_counter()
        library_tab = getattr(self.app, "library_tab", None)
        if library_tab is None or not self._view_available("map"):
            self._map_prewarm_scheduled = False
            return
        page = self.app._ensure_library_map() if hasattr(self.app, "_ensure_library_map") else getattr(library_tab, "coherence_map", None)
        if page is not None and hasattr(page, "refresh_from_app"):
            audio_type = library_tab._current_audio_type_filter()
            category = library_tab._current_category_filter()
            point_limit = 10000
            map_point_limit = getattr(page, "_map_point_limit", None)
            if callable(map_point_limit):
                try:
                    point_limit = max(1, int(cast(int | str | float, map_point_limit())))
                except (TypeError, ValueError):
                    point_limit = 10000
            visible_ids_from_proxy = getattr(library_tab, "_visible_record_ids_from_proxy", None)
            visible_ids = None
            if callable(visible_ids_from_proxy):
                try:
                    visible_ids = visible_ids_from_proxy(limit=point_limit)
                except TypeError:
                    visible_ids = visible_ids_from_proxy()
            can_reuse = getattr(page, "can_reuse_current_data", None)
            warm = bool(
                callable(can_reuse)
                and not force
                and can_reuse(self.app, audio_type=audio_type, category=category)
            )
            if not warm and hasattr(page, "set_loading"):
                page.set_loading(True, "Preparing map...")
            page.refresh_from_app(
                self.app,
                force=force,
                audio_type=audio_type,
                category=category,
                priority_row_ids=page._row_ids_from_visible_record_ids(visible_ids) if hasattr(page, "_row_ids_from_visible_record_ids") else None,
            )
            if hasattr(page, "schedule_library_projection_prewarm"):
                is_frontload = getattr(self.app, "_frontloading_startup", False)
                if is_frontload:
                    page.schedule_library_projection_prewarm(frontload=True)
                else:
                    page.schedule_library_projection_prewarm()
            elif hasattr(page, "prewarm_library_projections"):
                page.prewarm_library_projections()
            if hasattr(page, "set_library_filters"):
                page.set_library_filters(
                    audio_type,
                    category,
                    visible_ids,
                )
            elapsed_ms = (time.perf_counter() - started) * 1000
            logging.getLogger("unshuffle").debug(
                "Library map refresh: warm=%s force=%s type=%r category=%r visible_ids=%s elapsed=%.1fms",
                warm,
                force,
                audio_type,
                category,
                "none" if visible_ids is None else len(cast(Sized, visible_ids)),
                elapsed_ms,
            )
        self._map_prewarm_scheduled = False

    def prewarm_library_map(self, *, delay_ms: int = 1200) -> None:
        if self._map_prewarm_scheduled:
            return
        if not self._view_available("map"):
            return
        if not getattr(self.app, "model", None):
            return
        self._map_prewarm_scheduled = True
        QTimer.singleShot(max(0, delay_ms), self._prewarm_library_map_now)

    def _prewarm_library_map_now(self) -> None:
        self._map_prewarm_scheduled = False
        if not getattr(self.app, "model", None):
            return
        if self._coherence_is_running():
            self._defer_map_until_coherence(duplicate=False)
            return
        self.refresh_library_map(force=False)

    def prewarm_possible_duplicate_map(self) -> None:
        """Keep generated-duplicate points inside the warm capped map data set."""
        if self._coherence_is_running():
            self._defer_map_until_coherence(duplicate=True)
            return
        store = getattr(self.app, "session_store", None)
        if store is None or not hasattr(store, "tagged_row_ids") or not self._view_available("map"):
            return
        row_ids = store.tagged_row_ids("possibleduplicate")
        if not row_ids:
            return
        library_tab = getattr(self.app, "library_tab", None)
        page = self.app._ensure_library_map() if hasattr(self.app, "_ensure_library_map") else getattr(library_tab, "coherence_map", None)
        if page is None:
            return
        page.refresh_from_app(self.app, force=False, priority_row_ids=row_ids)
        if hasattr(page, "schedule_library_projection_prewarm"):
            page.schedule_library_projection_prewarm()
        elif hasattr(page, "prewarm_library_projections"):
            page.prewarm_library_projections()

    def _coherence_is_running(self) -> bool:
        controller = getattr(self.app, "coherence_controller", None)
        return bool(controller is not None and getattr(controller, "_running_workers", None))

    def _defer_map_until_coherence(self, *, duplicate: bool) -> None:
        if duplicate:
            self._pending_duplicate_map_prewarm = True
        else:
            self._pending_map_prewarm = True
        if self._map_prewarm_waiting_for_coherence:
            return
        controller = getattr(self.app, "coherence_controller", None)
        if controller is None or not hasattr(controller, "coherenceFinished"):
            return
        self._map_prewarm_waiting_for_coherence = True

        def _resume_map_prewarm() -> None:
            try:
                controller.coherenceFinished.disconnect(_resume_map_prewarm)
            except (RuntimeError, TypeError):
                pass
            self._map_prewarm_waiting_for_coherence = False
            duplicate_pending = self._pending_duplicate_map_prewarm
            map_pending = self._pending_map_prewarm
            self._pending_duplicate_map_prewarm = False
            self._pending_map_prewarm = False
            if duplicate_pending:
                QTimer.singleShot(0, self.prewarm_possible_duplicate_map)
            elif map_pending:
                QTimer.singleShot(0, self._prewarm_library_map_now)

        controller.coherenceFinished.connect(_resume_map_prewarm)

    def prewarm_library_tree(self, *, delay_ms: int = 1600) -> None:
        if self._tree_prewarm_scheduled:
            return
        if not self._view_available("tree"):
            return
        if not getattr(self.app, "model", None) or not getattr(self.app, "proxy_model", None):
            return
        if self.is_tree_visible():
            return
        self._tree_prewarm_scheduled = True
        QTimer.singleShot(max(0, delay_ms), self._prewarm_library_tree_now)

    def _prewarm_library_tree_now(self) -> None:
        self._tree_prewarm_scheduled = False
        if not self._view_available("tree"):
            return
        if not getattr(self.app, "model", None) or not getattr(self.app, "proxy_model", None):
            return
        if self.is_tree_visible():
            return
        session_store = getattr(self.app, "session_store", None)
        if session_store is not None and hasattr(self.app.library_tab.tree_model, "rebuild_from_store"):
            self.app.library_tab.tree_model.rebuild_from_store(session_store, getattr(self.app.model, "query", None))
        else:
            filtered = filtered_source_records(self.app.model, self.app.proxy_model)
            self.app.library_tab.tree_model.rebuild(filtered)
        self._tree_rebuild_pending = False

    def frontload_library_views(self, *, include_map: bool = True) -> None:
        """Prepare the first-use library views while launch is still covered by the splash."""
        if not getattr(self.app, "model", None):
            return
        if self._tree_rebuild_timer.isActive():
            self._tree_rebuild_timer.stop()
        if self._view_available("tree"):
            if self.is_tree_visible():
                self.do_tree_rebuild()
            else:
                self._prewarm_library_tree_now()
        if include_map and self._view_available("map"):
            self.refresh_library_map(force=False)
        if self.app.stack.currentWidget() is getattr(self.app, "dock_view", None) and self._view_available("map"):
            self.prewarm_docked_map(delay_ms=0)

    def frontload_library_view(self, mode: str) -> None:
        mode = str(mode or "").strip().lower()
        if mode == "table":
            prewarm = getattr(getattr(self.app, "model", None), "prewarm_initial_window", None)
            if callable(prewarm):
                prewarm()
            self.app.library_tab.refresh_table_viewport()
            return
        if mode == "tree":
            if self._tree_rebuild_timer.isActive():
                self._tree_rebuild_timer.stop()
            if self.is_tree_visible():
                self.do_tree_rebuild()
            else:
                self._prewarm_library_tree_now()
            return
        if mode == "map":
            self.refresh_library_map(force=False)

    def toggle_docked(self, checked):
        if checked:
            self.app.settings_controller.settings.setValue("window_geometry", self.app.saveGeometry())
        else:
            self.app.settings_controller.settings.setValue("docked_geometry", self.app.saveGeometry())

        self.app.settings_controller.save_docked_mode(checked)
        self.app.custom_menu_bar.set_docked_checked(checked)
        if getattr(self.app, "page_nav_bar", None):
            self.app.page_nav_bar.setVisible(not checked)
        if getattr(self.app, "footer", None) and hasattr(self.app.footer, "set_docked_presentation"):
            self.app.footer.set_docked_presentation(checked)
        
        if checked:
            self.app.custom_menu_bar.hide()
            
        if checked:
            if self._normal_window_flags is None:
                self._normal_window_flags = self.app.windowFlags()
            dock_flags = self._normal_window_flags | Qt.WindowStaysOnTopHint | Qt.WindowCloseButtonHint
            dock_appearance = getattr(self.app, "dock_appearance_controller", None)
            dock_flags |= Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint
            self.app.setWindowFlags(dock_flags)
            self.app.dock_view.set_hover_title_enabled(True)
            self.app.stack.setCurrentWidget(self.app.dock_view)
     
            text = self.app.library_tab.edit_search.text()
            if getattr(self.app, "search_controller", None) is not None:
                self.app.search_controller.set_query(text, immediate=True)
                self.app.search_controller.sync_search_ui(self.app.search_controller.current_query)
                
            if getattr(self.app, "audio_controller", None) is not None:
                self.app.audio_controller.toggle_audio_bar(False, immediate=True)
            apply_minimum_width(cast(QWidget, self.app), DOCKED_WINDOW_WIDTH)
            self.app.setMaximumWidth(DOCKED_MAXIMUM_WIDTH)
            
            import os
            docked_geom = self.app.settings_controller.settings.value("docked_geometry")
            if docked_geom and os.environ.get("QT_QPA_PLATFORM") != "offscreen":
                self.app.restoreGeometry(docked_geom)
                self.app.resize(DOCKED_WINDOW_WIDTH, self.app.height())
            else:
                self._resize_docked_for_view_mode(getattr(self.app.dock_view, "_view_mode", "tree"))
                
            if self._tree_rebuild_pending:
                self.schedule_tree_rebuild(delay_ms=0)
            if self._view_available("map"):
                self.prewarm_docked_map(delay_ms=0)
            if hasattr(self.app.dock_view, "sync_transport_state"):
                self.app.dock_view.sync_transport_state()
        else:
            dock_appearance = getattr(self.app, "dock_appearance_controller", None)
            normal_flags = self._normal_window_flags
            if normal_flags is None:
                normal_flags = self.app.windowFlags() & ~Qt.WindowStaysOnTopHint & ~Qt.FramelessWindowHint
                normal_flags |= Qt.WindowCloseButtonHint
            self.app.setWindowFlags(normal_flags)
            self._normal_window_flags = None
            self.app.dock_view.set_hover_title_enabled(False)
            self.app.stack.setCurrentWidget(self.app.library_tab)
        
            text = self.app.dock_view.edit_search.text()
            if getattr(self.app, "search_controller", None) is not None:
                self.app.search_controller.set_query(text, immediate=True)
                self.app.search_controller.sync_search_ui(self.app.search_controller.current_query)
                
            if getattr(self.app, "audio_controller", None) is not None:
                self.app.audio_controller.toggle_audio_bar(True, immediate=True)
            apply_minimum_width(cast(QWidget, self.app), MAIN_WINDOW_WIDTH)
            self.app.setMaximumWidth(16777215)
            
            import os
            normal_geom = self.app.settings_controller.settings.value("window_geometry")
            if normal_geom and os.environ.get("QT_QPA_PLATFORM") != "offscreen":
                self.app.restoreGeometry(normal_geom)
                if self.app.width() < MAIN_WINDOW_WIDTH:
                    self.app.resize(MAIN_WINDOW_WIDTH, max(self.app.height(), MAIN_WINDOW_HEIGHT))
            else:
                self.app.resize(MAIN_WINDOW_WIDTH, MAIN_WINDOW_HEIGHT)
            
        if not getattr(self.app, "_defer_window_show", False):
            self.app.show()
        dock_appearance = getattr(self.app, "dock_appearance_controller", None)
        if dock_appearance is not None:
            QTimer.singleShot(0, dock_appearance.context_changed)
        if checked:
            self.app.dock_view.set_hover_title_enabled(True)
        self.app.custom_menu_bar.setVisible(not checked)
        if checked and hasattr(self.app.dock_view, "sync_transport_state"):
            QTimer.singleShot(0, self.app.dock_view.sync_transport_state)
        if hasattr(self.app, "_apply_native_window_theme"):
            self.app._apply_native_window_theme()
        self.dockedChanged.emit(checked)

    def prewarm_docked_map(self, *, delay_ms: int = 0) -> None:
        if not hasattr(self.app, "dock_view"):
            return
        if not self._view_available("map"):
            return
        if not getattr(self.app, "model", None):
            return
        QTimer.singleShot(max(0, delay_ms), self._prewarm_docked_map_now)

    def _prewarm_docked_map_now(self) -> None:
        if not getattr(self.app, "model", None):
            return
        if not self._view_available("map"):
            return
        if hasattr(self.app.dock_view, "prewarm_map_from_app"):
            self.app.dock_view.prewarm_map_from_app(self.app, force=False)

    def refresh_docked_map(self, *, force: bool = False) -> None:
        if not hasattr(self.app, "dock_view"):
            return
        if not self._view_available("map"):
            return
        if hasattr(self.app.dock_view, "refresh_map_from_app"):
            self.app.dock_view.refresh_map_from_app(self.app, force=force)

    def on_docked_view_mode_changed(self, mode: str) -> None:
        mode = (mode or "").lower()
        self._resize_docked_for_view_mode(mode)
        if mode == "map":
            self.refresh_docked_map(force=False)

    def _resize_docked_for_view_mode(self, mode: str) -> None:
        if self.app.stack.currentWidget() is not getattr(self.app, "dock_view", None):
            return
        width = max(DOCKED_WINDOW_WIDTH, self.app.width()) if mode == "map" else DOCKED_WINDOW_WIDTH
        preferred_height = DOCKED_MINIMUM_HEIGHT
        if hasattr(self.app.dock_view, "preferred_docked_height_for_mode"):
            preferred_height = self.app.dock_view.preferred_docked_height_for_mode(mode)
        height = max(DOCKED_MINIMUM_HEIGHT, preferred_height)
        self.app.resize(width, height)

    def schedule_tree_rebuild(self, delay_ms=100):
        self._tree_rebuild_pending = True
        self._tree_rebuild_timer.start(delay_ms)

    def _current_tree_signature(self):
        model = getattr(self.app, "model", None)
        tree_model = getattr(getattr(self.app, "library_tab", None), "tree_model", None)
        profile = getattr(tree_model, "custom_tree_profile", None)
        return (
            id(model),
            getattr(model, "query", None),
            getattr(model, "group_column", None),
            getattr(tree_model, "sort_column", None),
            getattr(profile, "id", None),
            getattr(profile, "updated_at", None),
        )

    def do_tree_rebuild(self):
        self._tree_rebuild_pending = False
        if not self._view_available("tree") and self.app.stack.currentWidget() is not self.app.dock_view:
            return
        if not self.app.model or not self.app.proxy_model:
            return

        from PySide6.QtWidgets import QDialog
        dialogs = getattr(self.app, "findChildren", lambda _t: [])(QDialog)
        if isinstance(dialogs, list) and any(isinstance(w, QDialog) and w.isModal() and w.isVisible() for w in dialogs):
            self.schedule_tree_rebuild(500)
            return

        tree_views = []
        if self.app.stack.currentWidget() is self.app.dock_view and hasattr(self.app.dock_view, "view_tree"):
            tree_views.append(self.app.dock_view.view_tree)
        elif self._library_tab_mode() == "tree":
            tree_views.append(self.app.library_tab.view_tree)
        if not tree_views:
            self._tree_rebuild_pending = True
            return
        states = {view: view.snapshot_state() for view in tree_views}

        session_store = getattr(self.app, "session_store", None)
        if session_store is None:
            try:
                int(self.app.proxy_model.rowCount())
            except (TypeError, ValueError):
                logging.debug("Skipped tree rebuild because the proxy model is not ready.", exc_info=True)
                return

        for view in tree_views:
            view.setUpdatesEnabled(False)
        try:
            if session_store is not None and hasattr(self.app.library_tab.tree_model, "rebuild_from_store"):
                self.app.library_tab.tree_model.rebuild_from_store(session_store, getattr(self.app.model, "query", None))
            else:
                filtered = filtered_source_records(self.app.model, self.app.proxy_model)
                self.app.library_tab.tree_model.rebuild(filtered)
            self._tree_build_signature = self._current_tree_signature()
            for view in tree_views:
                view.restore_state(states[view])
        finally:
            for view in tree_views:
                view.setUpdatesEnabled(True)

    def update_footer_count(self):
        if not getattr(self.app, "footer", None):
            return
        if self.app.proxy_model is not None:
            try:
                count = int(self.app.proxy_model.rowCount())
            except (TypeError, ValueError):
                count = 0
        elif self.app.model is not None:
            count = self.app.model.rowCount() if hasattr(self.app.model, "rowCount") else len(self.app.model.records)
        else:
            count = 0
        self.app.footer.set_count(f"{count} files ready")

    def update_library_views(self, tree_delay_ms=100):
        self.update_footer_count()

        store = getattr(self.app, "session_store", None)
        library_tab = getattr(self.app, "library_tab", None)
        if store is not None and library_tab is not None and hasattr(library_tab, "set_taxonomy_availability"):
            context = getattr(getattr(self.app, "model", None), "_effective_taxonomy_context", None)
            taxonomy_rows = store.effective_taxonomy_group_counts(context)
            library_tab.set_taxonomy_availability(taxonomy_rows)
            dock_view = getattr(self.app, "dock_view", None)
            if dock_view is not None and hasattr(dock_view, "set_category_options"):
                dock_view.set_category_options(
                    [
                        (label, value)
                        for label, value in library_tab._category_options_for_type_state(False, False, True)
                        if isinstance(value, str)
                    ]
                )

        if self._view_available("table"):
            self.app.library_tab.view_table.viewport().update()
        if self.app.library_tab.current_view_mode() == "map":
            self.app.library_tab.sync_map_filters()
        if (
            hasattr(self.app, "dock_view")
            and self.app.stack.currentWidget() is self.app.dock_view
            and getattr(self.app.dock_view, "_view_mode", "") == "map"
        ):
            self.refresh_docked_map(force=False)
        
        if self.is_tree_visible():
            self.schedule_tree_rebuild(tree_delay_ms)
        else:
            self._tree_rebuild_pending = (
                self._tree_build_signature != self._current_tree_signature()
            )

    def is_tree_visible(self):
        if self.app.stack.currentWidget() is self.app.dock_view:
            return True
        return self._library_tab_mode() == "tree"

    def _view_available(self, mode: str) -> bool:
        is_available = getattr(self.app.library_tab, "is_view_available", None)
        if callable(is_available):
            result = is_available(mode)
            if isinstance(result, bool):
                return result
        return True

    def _library_tab_mode(self) -> str:
        lib_stack = getattr(self.app.library_tab, "lib_stack", None)
        fallback_index = lib_stack.currentIndex() if lib_stack is not None and hasattr(lib_stack, "currentIndex") else None
        current_view_mode = getattr(self.app.library_tab, "current_view_mode", None)
        if callable(current_view_mode):
            return normalize_library_tab_mode(str(current_view_mode() or ""), fallback_index=fallback_index)
        return normalize_library_tab_mode("", fallback_index=fallback_index)
