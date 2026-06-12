from PySide6.QtGui import QAction
from PySide6.QtWidgets import QInputDialog, QMessageBox

from unshuffle.core.constants import CATEGORIES

from ...utils import ui_helpers


def refresh_selection_menu(app):
    app.custom_menu_bar.menu_selection.clear()

    records = selected_records(app)
    busy = app.worker_manager.is_busy()
    has_selection = bool(records)

    if not has_selection:
        act_none = QAction("(No active selection)", app)
        act_none.setEnabled(False)
        app.custom_menu_bar.menu_selection.addAction(act_none)
        return

    act_preview = QAction("Preview", app)
    act_preview.triggered.connect(lambda checked=False: preview_selection(app))
    act_preview.setEnabled(not busy)
    app.custom_menu_bar.menu_selection.addAction(act_preview)

    act_explorer = QAction("Show in Explorer", app)
    act_explorer.setShortcut("Ctrl+E")
    act_explorer.triggered.connect(lambda checked=False: open_selection_in_explorer(app))
    act_explorer.setEnabled(not busy)
    app.custom_menu_bar.menu_selection.addAction(act_explorer)

    if hasattr(app, "act_delete"):
        app.act_delete.setEnabled(not busy)
        app.custom_menu_bar.menu_selection.addAction(app.act_delete)

    app.custom_menu_bar.menu_selection.addSeparator()

    type_menu = app.custom_menu_bar.menu_selection.addMenu("Set Type")
    for label in ("Oneshots", "Loops", "Non-Audio Assets"):
        act = QAction(label, app)
        act.triggered.connect(lambda checked=False, value=label: set_selection_type(app, value))
        act.setEnabled(not busy)
        type_menu.addAction(act)

    category_menu = app.custom_menu_bar.menu_selection.addMenu("Set Category")
    for category in CATEGORIES:
        act = QAction(category, app)
        act.triggered.connect(lambda checked=False, value=category: set_selection_category(app, value))
        act.setEnabled(not busy)
        category_menu.addAction(act)

    act_pack = QAction("Set Pack...", app)
    act_pack.triggered.connect(lambda checked=False: prompt_set_selection_pack(app))
    act_pack.setEnabled(not busy)
    app.custom_menu_bar.menu_selection.addAction(act_pack)


def selected_records(app):
    return app.selected_records()


def selection_target(app):
    return app.selected_record()


def preview_selection(app):
    target = selection_target(app)
    if target is not None:
        app.audio_controller.handle_play_request(target, app.model, app.proxy_model)


def open_selection_in_explorer(app):
    target = selection_target(app)
    if target is not None:
        ui_helpers.open_explorer(app, target)


def delete_selection_from_disk(app):
    records = selected_records(app)
    if records:
        app.workflow_controller.delete_records_physically(records)


def set_selection_type(app, value):
    records = selected_records(app)
    if records:
        app.drafting_controller.apply_bulk_type(value, records)


def set_selection_category(app, value):
    records = selected_records(app)
    if records:
        app.drafting_controller.apply_bulk_category(value, records)


def prompt_set_selection_pack(app):
    records = selected_records(app)
    if not records:
        return

    current = ""
    packs = {str(getattr(rec, "pack", "") or "") for rec in records}
    if len(packs) == 1:
        current = next(iter(packs))

    value, ok = QInputDialog.getText(app, "Set Pack", "Pack name for selection:", text=current)
    if ok:
        value = (value or "").strip()
        if value:
            app.drafting_controller.apply_bulk_pack(value, records)
