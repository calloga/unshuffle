from __future__ import annotations


def reset_library_search(app, *, defer_background_work: bool) -> None:
    app.library_tab.edit_search.blockSignals(True)
    app.library_tab.edit_search.clear()
    app.library_tab.edit_search.blockSignals(False)
    if defer_background_work and hasattr(app.search_controller, "clear_query_state"):
        app.search_controller.clear_query_state(sync_ui=True)
    else:
        app.search_controller.set_query("", immediate=True)


def refresh_library_sources_and_suggestions(app) -> None:
    if hasattr(app.library_tab, "set_sources") and app.engine:
        app.library_tab.set_sources(app.engine.session_source_roots)
    if hasattr(app.library_tab, "refresh_search_suggestions"):
        app.library_tab.refresh_search_suggestions()


def normalized_scan_stats(stats, records, category_counts_fn) -> dict:
    normalized = dict(stats or {})
    normalized.setdefault("category_counts", category_counts_fn(records))
    return normalized


def records_include_corrupt_silent_or_empty(records) -> bool:
    for rec in records or []:
        rec_tags = {str(tag).strip().lower() for tag in (getattr(rec, "tags", []) or [])}
        if "silent" in rec_tags or "empty" in rec_tags or "corrupted" in rec_tags:
            return True
    return False


def update_corrupt_filter_state(app) -> None:
    records = getattr(getattr(app, "model", None), "records", None)
    enabled = records_include_corrupt_silent_or_empty(records)
    if hasattr(app.library_tab, "set_corrupt_silent_empty_filter_enabled"):
        app.library_tab.set_corrupt_silent_empty_filter_enabled(enabled)
