from pathlib import Path

def get_tree_branch_path_for_record(app, rec):
    """Returns the hierarchy path for a record based on current tree levels."""
    if not hasattr(app.library_tab, "tree_model"):
        return ()
    tree_model = app.library_tab.tree_model
    levels = tree_model._active_tree_levels()
    profile = getattr(tree_model, "custom_tree_profile", None)
    if profile is not None:
        from unshuffle.logic.tree_organization import TreeRouteBuilder

        try:
            routes = TreeRouteBuilder().routes_for(
                [rec],
                profile,
                levels,
                presentation_mode=True,
                confidence_min=getattr(tree_model, "confidence_min", 0.0),
                confidence_max=getattr(tree_model, "confidence_max", 1.0),
                confidence_floor=getattr(tree_model, "confidence_floor", None),
                confidence_filter_enabled=getattr(tree_model, "confidence_filter_enabled", True),
            )
        except ValueError:
            return ()
        if not routes:
            return ()
        return tuple(part.label for part in routes[0].parts)

    parts = []
    for field, _node_type in levels:
        value = getattr(rec, field, "")
        parts.append(str(value))
    return tuple(parts)

def run_draft_tree_refresh(app):
    """Performs a targeted refresh of modified tree branches."""
    from ..core.filter_query import tree_highlight_text, tree_skip_fields
    
    if not app.model or not app.proxy_model:
        return
        
    dc = app.drafting_controller
    if not dc._partial_refresh:
        app.view_controller.do_tree_rebuild()
        return

    branch_paths = {path for path in dc._branch_paths if path}
    dc._branch_paths.clear()
    if not branch_paths:
        return

    query = app.library_tab.edit_search.text()
    highlight = tree_highlight_text(query)
    skip_fields = tree_skip_fields(query)
    
    if highlight or skip_fields:
        app.view_controller.schedule_tree_rebuild(delay_ms=0)
        return

    top_level_names = {path[0] for path in branch_paths if path}
    if not top_level_names:
        return

    tree_views = []
    if app.stack.currentWidget() is app.dock_view and hasattr(app.dock_view, "view_tree"):
        tree_views.append(app.dock_view.view_tree)
    elif app.library_tab.current_view_mode() == "tree":
        tree_views.append(app.library_tab.view_tree)
        
    states = {view: view.snapshot_state() for view in tree_views}

    filtered = [
        app.model.record(app.proxy_model.mapToSource(app.proxy_model.index(r, 0)).row())
        for r in range(app.proxy_model.rowCount())
    ]
    app.library_tab.tree_model.set_search_text(highlight)
    
    for view in tree_views:
        view.setUpdatesEnabled(False)
    try:
        app.library_tab.tree_model.partial_rebuild(filtered, top_level_names, skip_fields=skip_fields)
        for view in tree_views:
            view.restore_state(states[view])
    finally:
        for view in tree_views:
            view.setUpdatesEnabled(True)
