from __future__ import annotations

import logging


def clear_workbench_for_cancelled_scan(app) -> None:
    if not app:
        return
    try:
        from ..models import StagingTableModel

        data_manager = getattr(app, "data_manager", None)
        model = StagingTableModel(
            [],
            getattr(app, "undo_stack", None),
            sync_callback=getattr(data_manager, "sync_record_to_db", None),
        )
        if hasattr(app, "set_runtime_context"):
            app.set_runtime_context(model=model)
        else:
            app.model = model
        if getattr(app, "proxy_model", None) is not None:
            app.proxy_model.setSourceModel(model)
        if getattr(app, "view_controller", None) is not None:
            app.view_controller.update_library_views(tree_delay_ms=0)
        if getattr(app, "footer", None) is not None and hasattr(app.footer, "set_count"):
            app.footer.set_count("0 files ready")
    except Exception:
        logging.debug("Canceled scan workbench cleanup skipped.", exc_info=True)
