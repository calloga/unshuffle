import logging
from pathlib import Path

import shiboken6
from PySide6.QtWidgets import QMessageBox

from unshuffle.bridge.persistence_bridge import PersistenceBridge
from unshuffle.bridge.search_bridge import SearchBridge
from unshuffle.bridge.workflow_bridge import create_workflow_bridge

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

    try:
        new_engine = create_workflow_bridge(Path(tgt), session_id=sid)
    except Exception as e:
        if show_errors:
            QMessageBox.warning(app, "Session Resume Error", str(e))
        else:
            logging.exception("Session resume failed for target %s", tgt)
        return

    src = sess.get("source_path")
    from ...core.workers import SessionLoadWorker

    worker = SessionLoadWorker(tgt, sid)
    app._session_load_worker = worker

    def _finish_resume(payload):
        if not _is_qobject_valid(app):
            try:
                new_engine.close()
            except Exception:
                logging.exception("Failed to close abandoned session engine.")
            return
        if app._session_load_worker is worker:
            app._session_load_worker = None

        records = payload.get("records") or []
        if not records:
            try:
                new_engine.close()
            except Exception:
                logging.exception("Failed to close unloaded session engine.")
            QMessageBox.warning(app, "Error", "No staging data found for this session.")
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
                old_engine.close()
            except Exception:
                logging.exception("Failed to close previous engine after resuming session.")
        app.engine = new_engine
        app.undo_stack.clear()

        app.search_controller.search_engine.set_bridge(SearchBridge(new_engine))
        app.data_manager.set_bridge(PersistenceBridge(new_engine))
        app.worker_manager.set_engine(new_engine)

        app.workflow_controller.handle_scan_finished(plan, True, None)
        app.footer.log(f"Successfully loaded {len(plan)} files from session.")

    def _handle_error(message):
        if not _is_qobject_valid(app):
            try:
                new_engine.close()
            except Exception:
                logging.exception("Failed to close failed session resume engine.")
            return
        if app._session_load_worker is worker:
            app._session_load_worker = None
        try:
            new_engine.close()
        except Exception:
            logging.exception("Failed to close failed session resume engine.")
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
