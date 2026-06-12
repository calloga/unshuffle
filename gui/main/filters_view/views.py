from unshuffle.core.constants import TREE_REBUILD_DEBOUNCE_MS


def schedule_tree_rebuild(app, delay_ms=TREE_REBUILD_DEBOUNCE_MS):
    app.view_controller.schedule_tree_rebuild(delay_ms)


def do_tree_rebuild(app):
    app.view_controller.do_tree_rebuild()


def update_library_views(app, tree_delay_ms=TREE_REBUILD_DEBOUNCE_MS):
    app.view_controller.update_library_views(tree_delay_ms)
