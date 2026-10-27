import logging
from pathlib import Path

import shiboken6
from PySide6.QtWidgets import QMessageBox

from unshuffle.bridge.persistence_bridge import PersistenceBridge
from unshuffle.bridge.search_bridge import SearchBridge
from unshuffle.bridge.workflow_bridge import create_workflow_bridge
from ...core.session_relink import prepare_saved_session_sources


def request_new_session(app) -> bool:
    reply = QMessageBox.question(
        app,
        "Start New Session",
        "Close Unshuffle and open the startup launcher to begin a new session?",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    )
    if reply != QMessageBox.StandardButton.Yes:
        return False

    settings_controller = getattr(app, "settings_controller", None)
    if settings_controller is not None:
        settings_controller.set_show_startup_launcher(True)
        settings = getattr(settings_controller, "settings", None)
        if settings is not None and hasattr(settings, "sync"):
            settings.sync()
    app._restart_with_startup_launcher = True
    if not app.close():
        app._restart_with_startup_launcher = False
        return False
    return True

def load_staging_session(app, sess, show_errors=True):
    drafting = getattr(app, "drafting_controller", None)
    if drafting is not None and not drafting.confirm_clear_pending_draft("load another session"):
        return
    sid = str(sess.get("session_id") or "").strip()
    if not sid:
        if show_errors:
            QMessageBox.warning(app, "Session Resume Error", "This session entry is missing a session ID.")
        else:
            logging.error("Session resume blocked: missing session ID.")
        return
    tgt = app.settings.value("last_target", "")
    if not tgt:
        return

    app.footer.log_output.clear()
    app.footer.log(f"<b>Resuming Scan Session: {sid}</b>")

    old_engine = getattr(app, "engine", None)

    src = sess.get("source_path")
    from ...core.workers import SessionLoadWorker

    worker = SessionLoadWorker(
        tgt,
        sid,
    )
    app._session_load_worker = worker

    def _finish_resume(payload):
        if not _is_qobject_valid(app):
            return
        if app._session_load_worker is not worker:
            return

        plan = payload.get("plan") or []
        record_count = int(payload.get("record_count") or len(plan))
        if record_count <= 0:
            app._session_load_worker = None
            QMessageBox.warning(app, "Error", "No staging data found for this session.")
            return

        new_engine = None
        try:
            payload = prepare_saved_session_sources(app, dict(payload, target=tgt))
            if app._session_load_worker is not worker:
                return
            app._session_load_worker = None
            if payload is None:
                return
            resolved_sid = str(payload.get("session_id") or sid)
            new_engine = create_workflow_bridge(Path(payload["target"]), session_id=resolved_sid)
            if payload.get("db_scope") == "local":
                raw_engine = getattr(new_engine, "engine", new_engine)
                if raw_engine.db is not raw_engine.local_db:
                    raw_engine.db.close()
                    raw_engine.db = raw_engine.local_db
        except Exception as exc:
            if app._session_load_worker is worker:
                app._session_load_worker = None
            if new_engine is not None:
                try:
                    new_engine.close()
                except Exception:
                    logging.exception("Failed to close unloaded session engine.")
            if show_errors:
                QMessageBox.warning(app, "Session Resume Error", str(exc))
            else:
                logging.exception("Session resume failed for target %s", tgt)
            return
        plan = payload.get("plan") or []
        sources_strs = payload.get("sources") or []
        if sources_strs:
            new_engine.session_source_roots = [Path(s) for s in sources_strs]
            new_engine.session_source_root = Path(sources_strs[0])
        elif src:
            new_engine.session_source_root = Path(src)
            new_engine.session_source_roots = [Path(src)]

        if old_engine:
            try:
                app.worker_manager.cancel_all()
                if getattr(app, "search_controller", None):
                    app.search_controller.invalidate_pending_searches()
                old_engine.close()
            except Exception:
                logging.exception("Failed to close previous engine after resuming session.")
        app.engine = new_engine
        app.undo_stack.clear()

        app.search_controller.search_engine.set_bridge(SearchBridge(new_engine))
        app.data_manager.set_bridge(PersistenceBridge(new_engine))
        app.worker_manager.set_engine(new_engine)

        stats = {
            "total_scanned": record_count,
            "added_count": record_count,
            "lib_dupe_count": 0,
            "session_dupe_count": 0,
            "total_dupe_count": 0,
        }
        app.workflow_controller.finalize_scan_data(
            plan,
            False,
            stats,
            show_summary=False,
            persist_staging=False,
        )
        app.footer.log(f"Successfully loaded {record_count} files from session.")

    def _handle_error(message):
        if not _is_qobject_valid(app):
            return
        if app._session_load_worker is not worker:
            return
        app._session_load_worker = None
        if show_errors:
            QMessageBox.warning(app, "Session Resume Error", message)
        else:
            logging.error("Session resume worker failed for target %s: %s", tgt, message)

    worker.finished.connect(_finish_resume)
    worker.finished.connect(worker.deleteLater)
    worker.error.connect(_handle_error)
    worker.error.connect(worker.deleteLater)
    worker.start()


def _is_qobject_valid(obj) -> bool:
    try:
        return bool(obj is not None and shiboken6.isValid(obj))
    except TypeError:
        return obj is not None
