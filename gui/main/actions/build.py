from PySide6.QtGui import QAction

from .navigation import is_current_workspace


def refresh_build_menu(app):
    app.custom_menu_bar.menu_build.clear()

    if not is_current_workspace(app, "build"):
        app.act_build = QAction("Build", app)
        app.act_build.setShortcut("Ctrl+B")
        app.act_build.triggered.connect(app.open_build_workspace)
        app.custom_menu_bar.menu_build.addAction(app.act_build)

    has_draft = app.drafting_controller.has_changes()
    if has_draft:
        if app.custom_menu_bar.menu_build.actions():
            app.custom_menu_bar.menu_build.addSeparator()

        act_save_draft = QAction("Save Draft Changes", app)
        act_save_draft.triggered.connect(app.drafting_controller.save_reorg_draft)
        act_save_draft.setEnabled(not app.worker_manager.is_busy())
        app.custom_menu_bar.menu_build.addAction(act_save_draft)

        act_discard_draft = QAction("Discard Draft Changes", app)
        act_discard_draft.triggered.connect(app.drafting_controller.discard_reorg_draft)
        act_discard_draft.setEnabled(not app.worker_manager.is_busy())
        app.custom_menu_bar.menu_build.addAction(act_discard_draft)
