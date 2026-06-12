def reset_discovery_state(app):
    if hasattr(app, "library_tab"):
        app.library_tab.reset_discovery_controls()

    if hasattr(app, "search_controller"):
        app.search_controller.set_query("", immediate=True)

    if getattr(app, "proxy_model", None):
        app.proxy_model.set_confidence_range(0.0, 1.0)
