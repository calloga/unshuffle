from __future__ import annotations

import logging


def apply_runtime_engine(window, engine) -> None:
    window.engine = engine
    window.search_controller.engine = engine
    if engine is None:
        window.search_controller.search_engine.set_bridge(None)
        window.data_manager.set_bridge(None)
        window.library_tab.set_saved_filters([])
    else:
        from unshuffle.bridge.persistence_bridge import PersistenceBridge
        from unshuffle.bridge.search_bridge import SearchBridge

        window.search_controller.search_engine.set_bridge(SearchBridge(engine))
        window.data_manager.set_bridge(PersistenceBridge(engine))
        window.library_tab.set_saved_filters(window.settings_controller.get_saved_filters())
    window.worker_manager.set_engine(engine)
    try:
        window.filter_controller.refresh_dock_filters()
    except Exception:
        logging.exception("Failed to refresh dock filters after runtime context update.")
    if window.system_controller is not None:
        try:
            window.system_controller.refresh_capabilities()
        except Exception:
            logging.exception("Failed to refresh system capabilities after runtime context update.")


def apply_runtime_model(window, model) -> None:
    window.model = model
    if model is not None:
        model.draft_edit_callback = window.drafting_controller.apply_table_edit
        model.draft_bulk_callback = window.drafting_controller.apply_table_bulk_updates
    window.search_controller.model = model
    window.acoustic_controller.model = model
    if getattr(window, "tagging_controller", None):
        window.tagging_controller.clear_state()
    if getattr(window, "coherence_controller", None):
        window.coherence_controller.clear_state()
    maybe_refresh_library_map(window)
    maybe_schedule_startup_coherence(window)


def maybe_refresh_library_map(window) -> None:
    library_analyzer = getattr(getattr(window, "library_tab", None), "coherence_map", None)
    if (
        library_analyzer is not None
        and hasattr(library_analyzer, "refresh_from_app")
        and window.stack.currentWidget() is window.library_tab
        and window.library_tab.current_view_mode() == "map"
    ):
        try:
            window.view_controller.refresh_library_map(force=True)
        except Exception:
            logging.exception("Failed to refresh library map after runtime context update.")


def maybe_schedule_startup_coherence(window) -> None:
    if (
        window._should_auto_check_coherence_on_start()
        and not getattr(window, "_frontloading_startup", False)
        and not getattr(window, "_scan_finalizing", False)
    ):
        try:
            window.coherence_controller.schedule_after_render(mode="auto")
        except Exception:
            logging.exception("Failed to schedule startup coherence after runtime context update.")
