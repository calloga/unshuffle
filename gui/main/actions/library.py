import json
import logging
from pathlib import Path

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QInputDialog, QMessageBox

from ...utils import ui_helpers
from ...utils.history import load_pending_scan_sessions
from ...utils.state import finalize_model_mutation
from .session import load_staging_session


def _recent_scan_sources(app, limit=8):
    raw = app.settings.value("recent_scan_sources_json", "")
    recents = []
    if raw:
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                recents = [str(item).strip() for item in data if str(item).strip()]
        except (TypeError, json.JSONDecodeError):
            recents = []
    existing = []
    seen = set()
    for item in recents:
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        if Path(item).exists():
            existing.append(item)
    return existing[:limit]


def _scan_recent_source(app, path: str, append: bool):
    path = (path or "").strip()
    if not path:
        return
    if hasattr(app.library_tab, "_remember_scan_source"):
        app.library_tab._remember_scan_source(path)
    app.workflow_controller.start_scan([path], append=append)


def _confirm_clear_pending_draft(app, action_text: str) -> bool:
    drafting = getattr(app, "drafting_controller", None)
    return drafting is None or drafting.confirm_clear_pending_draft(action_text)


def refresh_library_menu(app):
    app.custom_menu_bar.menu_library.clear()

    app.custom_menu_bar.menu_library.addAction(app.custom_menu_bar.act_open_library)
    app.custom_menu_bar.menu_library.addSeparator()

    if getattr(app, "act_sync", None) is None:
        app.act_sync = QAction("Load Target Drive Index", app)
        app.act_sync.triggered.connect(lambda: app.workflow_controller.start_refresh([]))

    menu_new_recent = app.custom_menu_bar.menu_library.addMenu("New")
    menu_new_recent.addAction(app.act_new)
    menu_new_recent.addSeparator()
    recent_sources = _recent_scan_sources(app)
    if recent_sources:
        for path_str in recent_sources:
            act = QAction(Path(path_str).name or path_str, app)
            act.setToolTip(path_str)
            act.triggered.connect(lambda chk=False, p=path_str: _scan_recent_source(app, p, append=False))
            menu_new_recent.addAction(act)
    else:
        menu_new_recent.addAction("No recent folders").setEnabled(False)

    menu_add_recent = app.custom_menu_bar.menu_library.addMenu("Expand")
    menu_add_recent.addAction(app.act_add)
    menu_add_recent.addSeparator()
    if recent_sources:
        for path_str in recent_sources:
            act = QAction(Path(path_str).name or path_str, app)
            act.setToolTip(path_str)
            act.triggered.connect(lambda chk=False, p=path_str: _scan_recent_source(app, p, append=True))
            menu_add_recent.addAction(act)
    else:
        menu_add_recent.addAction("No recent folders").setEnabled(False)

    menu_load = app.custom_menu_bar.menu_library.addMenu("Recent")
    tgt = app.settings.value("last_target", "")
    if not tgt:
        menu_load.addAction("Select Target to see scans").setEnabled(False)
    else:
        try:
            pending = load_pending_scan_sessions(tgt, limit=10)
            if not pending:
                menu_load.addAction("No recent pending scans").setEnabled(False)
            else:
                for sess in pending:
                    ts = sess.get("timestamp", "").split("T")[0]
                    src = sess.get("source_path", "Unknown")
                    label = f"{ts}: {Path(src).name}"
                    act = QAction(label, app)
                    act.triggered.connect(lambda chk=False, s=sess: load_staging_session(app, s))
                    menu_load.addAction(act)
        except Exception:
            logging.exception("Failed to load recent scan sessions for target %s", tgt)
            menu_load.addAction("Error loading scans").setEnabled(False)

    app.custom_menu_bar.menu_library.addSeparator()
    app.custom_menu_bar.menu_library.addAction(app.act_refresh)

    app.menu_coherence = app.custom_menu_bar.menu_library.addMenu("Library Health")

    app.act_auto_coherence = QAction("Check Library on Start", app)
    app.act_auto_coherence.setCheckable(True)
    settings_controller = getattr(app, "settings_controller", None)
    is_auto_enabled = (
        settings_controller.get_auto_check_coherence_on_start()
        if settings_controller is not None and hasattr(settings_controller, "get_auto_check_coherence_on_start")
        else False
    )
    app.act_auto_coherence.setChecked(bool(is_auto_enabled))

    def _set_auto_coherence(checked: bool) -> None:
        if settings_controller is not None and hasattr(settings_controller, "set_auto_check_coherence_on_start"):
            settings_controller.set_auto_check_coherence_on_start(checked)

    app.act_auto_coherence.triggered.connect(_set_auto_coherence)
    app.menu_coherence.addAction(app.act_auto_coherence)

    app.custom_menu_bar.menu_library.addSeparator()

    menu_export = app.custom_menu_bar.menu_library.addMenu("Export")
    app.act_export = QAction("To CSV", app)
    app.act_export.triggered.connect(lambda checked=False: ui_helpers.export_csv(app))
    menu_export.addAction(app.act_export)

    app.act_export_session = QAction("Staging Session", app)
    app.act_export_session.triggered.connect(lambda checked=False: export_session(app))
    menu_export.addAction(app.act_export_session)

    menu_import = app.custom_menu_bar.menu_library.addMenu("Import")
    app.act_import_session = QAction("From Staging Session", app)
    app.act_import_session.triggered.connect(lambda checked=False: import_session(app))
    menu_import.addAction(app.act_import_session)

    app.act_import = QAction("From CSV", app)
    app.act_import.triggered.connect(lambda checked=False: ui_helpers.import_csv(app))
    menu_import.addAction(app.act_import)

    if app.worker_manager.is_busy():
        app.act_import.setEnabled(False)
        app.act_export.setEnabled(False)
        app.act_export_session.setEnabled(False)
        app.act_import_session.setEnabled(False)

def _default_dialog_dir(app) -> str:
    from pathlib import Path
    last_tgt = app.settings.value("last_target", "")
    if last_tgt and Path(last_tgt).exists():
        return str(Path(last_tgt))
    return str(Path.home())

def export_session(app):
    if not app.data_manager.bridge or not app.data_manager.bridge.has_session():
        QMessageBox.warning(app, "Export Session", "No active staging session is loaded.")
        return
    target = getattr(getattr(app, "engine", None), "target_dir", None) or app.settings.value("last_target", "")
    if not str(target or "").strip():
        QMessageBox.warning(app, "Export Session", "No target folder is available for this staging session.")
        return
    app.data_manager.export_session_to_folder(target, parent_widget=app)

def import_session(app):
    from PySide6.QtWidgets import QFileDialog
    if not _confirm_clear_pending_draft(app, "import a session"):
        return
    default_dir = _default_dialog_dir(app)
    path, _ = QFileDialog.getOpenFileName(app, "Import Staging Session File", default_dir, "Unshuffle Session Files (*.unshuffle);;All Files (*)")
    if path:
        app.data_manager.import_session_from_folder(path, parent_widget=app)


def remove_folder_clicked(app):
    if not _confirm_clear_pending_draft(app, "remove a folder"):
        return
    if not app.engine or not app.engine.session_source_roots:
        QMessageBox.information(app, "Remove Folder", "No folders currently loaded in this session.")
        return

    roots = [str(r) for r in app.engine.session_source_roots]
    item, ok = QInputDialog.getItem(app, "Remove Folder", "Select folder to remove from workbench:", roots, 0, False)
    if ok and item:
        do_remove_folder(app, Path(item))


def do_remove_folder(app, root: Path):
    if not app.engine or not getattr(app.engine, "db", None):
        QMessageBox.warning(app, "Remove Folder", "The current library session is not available. Start or load a session first.")
        return
    original_roots = list(getattr(app.engine, "session_source_roots", []) or [])
    original_records = list(getattr(getattr(app, "model", None), "records", []) or [])
    model_reset = False
    try:
        root = Path(root).resolve()
        app.workflow_controller.detach_source_root(root)
        root_prefix = root.as_posix().lower()

        if app.model:
            app.model.beginResetModel()
            model_reset = True
            new_records = []
            removed_count = 0
            for row, rec in enumerate(app.model.records):
                if hasattr(app.model, "normalized_source_path"):
                    rec_path = app.model.normalized_source_path(row)
                else:
                    rec_path = Path(rec.source_path).resolve().as_posix().lower()
                if rec_path == root_prefix or rec_path.startswith(root_prefix + "/"):
                    removed_count += 1
                else:
                    new_records.append(rec)

            app.model.records = new_records
            _refresh_model_indexes(app.model)
            app.model.endResetModel()
            model_reset = False

            if hasattr(app.library_tab, "set_sources"):
                app.library_tab.set_sources(app.engine.session_source_roots)

            if app.engine.session_source_roots:
                app.footer.log(
                    f"<b>Removed:</b> {root.name} ({removed_count} staged files removed). Rebuilding remaining sources for accuracy..."
                )
                app.workflow_controller.start_refresh(app.engine.session_source_roots)
            else:
                app.footer.log(f"<b>Removed:</b> {root.name} ({removed_count} files removed from workbench)")
                finalize_model_mutation(app, resort=True, refresh_search=True)
    except Exception as exc:
        logging.exception("Failed to remove source folder from workbench.")
        if model_reset:
            try:
                app.model.endResetModel()
            except Exception:
                logging.debug("Could not end failed model reset.", exc_info=True)
        if getattr(app, "model", None) is not None:
            try:
                app.model.beginResetModel()
                app.model.records = original_records
                _refresh_model_indexes(app.model)
                app.model.endResetModel()
            except Exception:
                logging.debug("Could not restore model records after remove-folder failure.", exc_info=True)
        if getattr(app, "engine", None) is not None:
            app.engine.session_source_roots = original_roots
            if original_roots:
                app.engine.session_source_root = original_roots[0]
        if hasattr(app.library_tab, "set_sources"):
            app.library_tab.set_sources(original_roots)
        QMessageBox.warning(app, "Remove Folder", f"Could not remove folder: {exc}")


def _refresh_model_indexes(model) -> None:
    if hasattr(model, "_invalidate_unique_values"):
        model._invalidate_unique_values()
    if hasattr(model, "_rebuild_row_and_color_caches"):
        model._rebuild_row_and_color_caches()
    else:
        if hasattr(model, "_rebuild_path_row_cache"):
            model._rebuild_path_row_cache()
        model._precalculate_colors()


def remove_folder_clicked_via_pill(app, root: Path):
    if not _confirm_clear_pending_draft(app, "remove a folder"):
        return
    reply = QMessageBox.question(
        app,
        "Remove Folder",
        f"Remove '{root.name}' and all its files from workbench?",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
    )
    if reply == QMessageBox.StandardButton.Yes:
        do_remove_folder(app, root)


def handle_refresh_all(app):
    if not _confirm_clear_pending_draft(app, "rescan the library"):
        return
    if not app.engine or not app.engine.session_source_roots:
        return

    roots = app.engine.session_source_roots
    msg = f"Re-scan {len(roots)} active library folder{'s' if len(roots) > 1 else ''} and replace their staged results?"
    if QMessageBox.question(app, "Refresh Library", msg) == QMessageBox.Yes:
        app.workflow_controller.start_refresh(roots)
