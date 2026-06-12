from __future__ import annotations

from .page_history import carousel_page_value, next_page_index, previous_page_index, record_page_history


def persist_current_page(window, key: tuple[str, str | None]) -> None:
    if not getattr(window, "_page_persistence_enabled", True):
        return
    page, section = key
    if page == "build":
        return
    if getattr(window, "settings_controller", None) and hasattr(window.settings_controller, "set_current_page"):
        window.settings_controller.set_current_page(page, section)


def current_page_key(window) -> tuple[str, str | None] | None:
    current = window.stack.currentWidget() if getattr(window, "stack", None) else None
    if current is window.library_tab:
        return ("library", None)
    if current is window.dock_view:
        return ("dock", None)
    if current is window.system_page:
        sections = {
            window.system_page.discovery_panel: "discovery",
            window.system_page.additions_panel: "additions",
            window.system_page.corrections_panel: "corrections",
            window.system_page.my_anchors_panel: "my_anchors",
            window.system_page.anchors_panel: "anchors",
            window.system_page.tree_organization_panel: "tree_organization",
        }
        return ("system", sections.get(window.system_page.stack.currentWidget(), "tree_organization"))
    if current is window.history_page:
        return ("history", None)
    if current is window.build_page:
        return ("build", None)
    return None


def record_current_page(window) -> None:
    if window._suppress_page_history:
        return
    key = window._current_page_key()
    if key is None:
        return
    window._page_history, window._page_history_index, changed = record_page_history(
        window._page_history,
        window._page_history_index,
        key,
    )
    if not changed:
        window._refresh_page_nav_buttons()
        window._persist_current_page(key)
        return
    window._persist_current_page(key)
    window._refresh_page_nav_buttons()


def refresh_page_nav_buttons(window) -> None:
    if not getattr(window, "btn_previous_page", None):
        return
    window.btn_previous_page.setEnabled(window._page_history_index > 0)
    window.btn_next_page.setEnabled(0 <= window._page_history_index < len(window._page_history) - 1)
    if getattr(window, "page_carousel", None):
        page = carousel_page_value(window._current_page_key())
        window.page_carousel.blockSignals(True)
        window.page_carousel.set_current_value(page)
        window.page_carousel.blockSignals(False)
        window._style_page_carousel()


def select_carousel_page(window, page: str) -> None:
    page = (page or "").strip()
    if not page:
        return
    if page == "library":
        window.open_library_workspace()
    elif page == "system":
        window.open_system_workspace()
    elif page == "build":
        window.open_build_workspace()
    elif page == "history":
        window.open_history_workspace()
    window._refresh_page_nav_buttons()


def go_to_previous_page(window) -> None:
    index = previous_page_index(window._page_history_index)
    if index is None:
        return
    window._page_history_index = index
    window._activate_page_key(window._page_history[window._page_history_index])


def go_to_next_page(window) -> None:
    index = next_page_index(window._page_history, window._page_history_index)
    if index is None:
        return
    window._page_history_index = index
    window._activate_page_key(window._page_history[window._page_history_index])


def activate_page_key(window, key: tuple[str, str | None]) -> None:
    page, section = key
    window._suppress_page_history = True
    try:
        if page == "library":
            window.stack.setCurrentWidget(window.library_tab)
        elif page == "dock":
            window.stack.setCurrentWidget(window.dock_view)
        elif page == "system":
            window.stack.setCurrentWidget(window.system_page)
            if section:
                window.system_page._set_section(section)
        elif page == "history":
            window.stack.setCurrentWidget(window.history_page)
            window._refresh_history_page()
        elif page == "build":
            if window.build_page is not None:
                window.stack.setCurrentWidget(window.build_page)
            else:
                window.open_build_workspace()
    finally:
        window._suppress_page_history = False
        window._persist_current_page(key)
        window._refresh_page_nav_buttons()
