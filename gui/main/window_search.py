from __future__ import annotations

import shiboken6


def current_records(window):
    if not window.model:
        return []
    return list(window.model.records)


def set_search_status(window, text: str) -> None:
    footer = getattr(window, "footer", None)
    if footer is None:
        return
    try:
        if shiboken6.isValid(footer):
            footer.set_status(text)
    except RuntimeError:
        pass


def handle_search_results_applied(window) -> None:
    window.view_controller.update_footer_count()
    if hasattr(window, "library_tab"):
        window.library_tab.sync_map_filters()
    if hasattr(window, "view_controller"):
        window.view_controller.refresh_docked_map(force=False)


def schedule_search_tree_refresh(window, delay_ms: int = 0) -> None:
    window.view_controller.schedule_tree_rebuild(delay_ms)


def sync_search_ui_state(
    window,
    *,
    query: str,
    active_saved_filters,
    active_source_filters,
    active_categories,
    confidence_range,
) -> None:
    if hasattr(window, "library_tab"):
        edit = window.library_tab.edit_search
        if edit.text() != query:
            edit.blockSignals(True)
            edit.setText(query)
            edit.blockSignals(False)
        window.library_tab._refresh_search_button_state()
        window.library_tab.set_active_saved_filters(active_saved_filters)
        window.library_tab.set_active_source_filters(active_source_filters)
        window.library_tab.category_carousel.set_active_values(active_categories)
        window.library_tab.signal_floor_control.set_range(*confidence_range)
        window.sync_type_filter_state()
        window.library_tab.sync_map_filters()

    if hasattr(window, "dock_view"):
        window.dock_view.set_search_text(query)
        window.dock_view.set_active_saved_filters(set(active_saved_filters or set()) | set(active_source_filters or set()))
        window.dock_view.set_category_state(active_categories)
        window.sync_type_filter_state()
        window.dock_view.set_confidence_range(*confidence_range)
        window.view_controller.refresh_docked_map(force=False)


def type_filter_state(window) -> tuple[bool, bool, bool]:
    types = getattr(getattr(window, "search_controller", None), "_audio_types", None)
    if types is None:
        return (False, False, True)
    type_values = {str(value) for value in types}
    state = ("Oneshots" in type_values, "Loops" in type_values, False)
    if (state[0] and state[1]) or (not state[0] and not state[1]):
        return (False, False, True)
    return state


def sync_type_filter_state(window) -> None:
    state = type_filter_state(window)
    if hasattr(window, "library_tab"):
        window.library_tab.set_type_state(*state)
    if hasattr(window, "dock_view"):
        window.dock_view.set_type_state(*state)


def selected_records(window):
    if not getattr(window, "model", None):
        return []
    if window.stack.currentWidget() is window.dock_view:
        return window.dock_view.selected_records()
    return window.library_tab.selected_records(window.model, window.proxy_model)


def selected_record(window):
    records = selected_records(window)
    return records[0] if records else None
