from PySide6.QtCore import QTimer
import logging
import os
from html import escape
from pathlib import Path

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMessageBox

from ...utils.history import (
    clear_migration_history,
    invalidate_history_cache,
    load_executed_sessions,
    resolve_history_target,
    reset_learning_weights,
)


def _normalized_path(path: str) -> str:
    if not path:
        return ""
    try:
        value = str(Path(path).resolve())
    except OSError:
        value = str(Path(path))
    return os.path.normcase(os.path.normpath(value))


def _history_target(app) -> str:
    return resolve_history_target(app.settings)


def refresh_history_menu(app):
    app.custom_menu_bar.menu_history.clear()

    app.custom_menu_bar.menu_history.addAction(app.custom_menu_bar.act_open_history)
    app.custom_menu_bar.menu_history.addSeparator()

    tgt = _history_target(app)
    if not tgt:
        app.custom_menu_bar.menu_history.addAction("Select Target to view history").setEnabled(False)
        return


    try:
        sessions = load_executed_sessions(tgt, limit=10)
    except Exception:
        logging.exception("Failed to load history menu for target %s", tgt)
        app.custom_menu_bar.menu_history.addAction("No history found").setEnabled(False)
        return

    if not sessions:
        app.custom_menu_bar.menu_history.addAction("No recent migrations").setEnabled(False)
    else:
        for sess in sessions:
            mode = sess.get("mode", "move").title()
            count = sess.get("file_count", 0)
            ts = sess.get("timestamp", "")
            date_str = ts.split("T")[0] if "T" in ts else ts
            state = str(sess.get("history_state") or "undoable").lower()

            label = f"{'Undone' if state == 'undone' else 'Undo'} {mode}: {date_str} ({count} files)"
            act = QAction(label, app)
            if state == "undone":
                act.setEnabled(False)
            else:
                act.triggered.connect(lambda chk=False, s=sess: confirm_undo(app, s))
            app.custom_menu_bar.menu_history.addAction(act)

    app.custom_menu_bar.menu_history.addSeparator()
    act_clear_history = QAction("Clear Migration History...", app)
    act_clear_history.triggered.connect(lambda checked=False: clear_history(app))
    app.custom_menu_bar.menu_history.addAction(act_clear_history)


def confirm_undo(app, sess):
    sid = str(sess.get("session_id") or "").strip()
    if not sid:
        QMessageBox.warning(app, "Undo Unavailable", "This history entry is missing a session ID.")
        return
    if str(sess.get("history_state") or "").lower() == "undone":
        QMessageBox.information(app, "Undo Unavailable", "This migration has already been undone.")
        return
    mode = sess.get("mode", "move").upper()
    src = sess.get("source_path", "Unknown")
    tgt = sess.get("target_root", "Unknown")
    count = sess.get("file_count", 0)
    ts = sess.get("timestamp", "")
    selected_target = _history_target(app)
    if _normalized_path(str(tgt)) != _normalized_path(str(selected_target)):
        QMessageBox.warning(
            app,
            "Undo Target Mismatch",
            "This history entry does not belong to the selected target library. Undo was blocked.",
        )
        return

    warn_msg = (
        "<h3>Confirm Session Revert</h3>"
        f"<p><b>Action:</b> {escape(str(mode))}<br>"
        f"<b>Date:</b> {escape(str(ts))}<br>"
        f"<b>Files:</b> {escape(str(count))}</p>"
        "<p>Are you sure you want to REVERT this migration?</p>"
        f"<p><b>Session:</b> {escape(str(sid))}<br>"
        f"<b>Mode:</b> {escape(str(mode))}<br>"
        f"<b>Source:</b> {escape(str(src))}<br>"
        f"<b>Target:</b> {escape(str(tgt))}</p>"
        "<p>All moved/copied files will be returned to their original locations.</p>"
    )
    if QMessageBox.warning(app, "Confirm Undo", warn_msg, QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
        invalidate_history_cache(_history_target(app), sid)
        app.worker_manager.start_undo(sid)


def reset_learning(app):
    tgt = app.settings.value("last_target", "")
    if not tgt:
        QMessageBox.information(app, "Reset Learned Weights", "Select a target library first.")
        return

    msg = "Reset learned keyword weight adjustments for this library?"
    if QMessageBox.question(app, "Reset Learned Weights", msg) != QMessageBox.Yes:
        return

    try:
        reset_learning_weights(tgt)
        QMessageBox.information(app, "Reset Learned Weights", "Learned weights were reset.")
    except Exception as e:
        QMessageBox.critical(app, "Reset Learned Weights", f"Could not reset learned weights: {e}")


def clear_history(app):
    tgt = _history_target(app)
    if not tgt:
        QMessageBox.information(app, "Clear Migration History", "Select a target library first.")
        return

    msg = "Clear migration/session history for this library? This does not remove the file cache or learned weights."
    if QMessageBox.warning(app, "Clear Migration History", msg, QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
        return

    try:
        clear_migration_history(tgt)
        QTimer.singleShot(0, lambda: refresh_history_menu(app))
        QMessageBox.information(app, "Clear Migration History", "Migration history was cleared.")
    except Exception as e:
        QMessageBox.critical(app, "Clear Migration History", f"Could not clear migration history: {e}")
