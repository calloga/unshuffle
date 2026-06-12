from __future__ import annotations

from ..styles import DEFAULT_THEME_KEY


def apply_app_settings(window, state: dict) -> None:
    geom = state.get("geometry")
    if geom:
        window.restoreGeometry(geom)

    if not state.get("docked_mode"):
        from ..utils.constants import MAIN_WINDOW_WIDTH, MAIN_WINDOW_HEIGHT

        if window.width() < MAIN_WINDOW_WIDTH:
            window.resize(MAIN_WINDOW_WIDTH, max(window.height(), MAIN_WINDOW_HEIGHT))

    floor = 0.0
    if hasattr(window, "library_tab"):
        window.library_tab.set_confidence_floor(floor)
        window.library_tab.tree_model.confidence_floor = floor
        window.library_tab.set_available_view_modes(state.get("library_view_modes", ("table", "tree", "map")))
        if hasattr(window, "dock_view"):
            window.dock_view.set_map_available(window.library_tab.is_view_available("map"))
        for view_mode in ("table", "tree", "map"):
            window.custom_menu_bar.set_library_view_available(view_mode, window.library_tab.is_view_available(view_mode))
        window.custom_menu_bar.set_startup_launcher_visible(window.settings_controller.get_show_startup_launcher())

    default_view_mode = str(state.get("default_view_mode") or "").strip().lower()
    if not default_view_mode:
        default_view_mode = "tree" if state.get("default_view_tree", False) else "table"
    window.view_controller.set_view_mode(default_view_mode)
    window.apply_theme(str(state.get("theme_key", DEFAULT_THEME_KEY)))
    window.apply_zoom(int(state.get("zoom_percent", 100)))

    restore_current_page(window, state)
    if state.get("docked_mode"):
        window.view_controller.toggle_docked(True)


def restore_current_page(window, state: dict) -> None:
    page = str(state.get("current_page") or "library")
    section = str(state.get("current_system_section") or "tree_organization")
    if page == "dock" and not state.get("docked_mode"):
        page = "library"
    if page in {"build", "help"}:
        page = "library"
        section = None
    elif page == "system" and section not in {
        "discovery",
        "additions",
        "corrections",
        "my_anchors",
        "anchors",
        "tree_organization",
    }:
        section = "tree_organization"
    key = (page, section if page == "system" else None)
    window._page_persistence_enabled = True
    window._suppress_page_history = True
    try:
        window._activate_page_key(key)
    finally:
        window._suppress_page_history = False
    window._page_history = []
    window._page_history_index = -1
    window._record_current_page()


def save_library_page_state(window) -> None:
    if (
        not getattr(window, "_library_page_state_persistence_enabled", False)
        or getattr(window, "_restoring_library_page_state", False)
        or not getattr(window, "settings_controller", None)
    ):
        return
    search_controller = getattr(window, "search_controller", None)
    library_tab = getattr(window, "library_tab", None)
    if search_controller is None or library_tab is None:
        return
    audio_types = getattr(search_controller, "_audio_types", None)
    state = {
        "query": getattr(search_controller, "current_query", ""),
        "audio_types": None if audio_types is None else sorted(str(value) for value in audio_types),
        "view_mode": library_tab.current_view_mode() if hasattr(library_tab, "current_view_mode") else "table",
    }
    window.settings_controller.save_library_page_state(state)


def restore_library_page_state(window) -> None:
    if not getattr(window, "settings_controller", None):
        return
    state = window.settings_controller.get_library_page_state()
    if not state:
        return
    search_controller = getattr(window, "search_controller", None)
    library_tab = getattr(window, "library_tab", None)
    if search_controller is None or library_tab is None:
        return
    window._restoring_library_page_state = True
    try:
        raw_types = state.get("audio_types", None)
        type_values = None if raw_types is None else {str(value) for value in raw_types}
        search_controller._audio_types = type_values
        if getattr(window, "proxy_model", None):
            window.proxy_model.set_audio_types(type_values)
        window.sync_type_filter_state()

        query = str(state.get("query") or "").strip()
        search_controller._current_query = query
        search_controller.sync_search_ui(query)

        view_mode = str(state.get("view_mode") or "").strip().lower()
        if view_mode:
            window.view_controller.set_view_mode(view_mode)

        if query:
            search_controller.execute_search()
        elif getattr(window, "view_controller", None):
            window.view_controller.update_library_views(tree_delay_ms=0)
    finally:
        window._restoring_library_page_state = False
