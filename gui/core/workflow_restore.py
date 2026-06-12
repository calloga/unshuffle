import logging
from pathlib import Path

import shiboken6

from .settings_controller import (
    DOCKED_MODE_KEY,
    SHOW_STARTUP_LAUNCHER_KEY,
    STARTUP_LAUNCHER_LAST_CHOICE_KEY,
)

def qt_object_alive(obj) -> bool:
    if obj is None:
        return False
    try:
        return shiboken6.isValid(obj)
    except RuntimeError:
        return False
    except TypeError:
        return True


def _clear_stale_startup_restore(controller, *, target: str = "", session_id: str = "") -> None:
    """Drop invalid restore replay state while preserving reusable source/target hints."""
    app = getattr(controller, "app", None)
    settings = getattr(app, "settings", None)
    if settings is not None:
        for key in ("last_scan_session_id", STARTUP_LAUNCHER_LAST_CHOICE_KEY):
            try:
                settings.remove(key)
            except AttributeError:
                pass
        try:
            settings.setValue(SHOW_STARTUP_LAUNCHER_KEY, True)
            settings.setValue(DOCKED_MODE_KEY, False)
        except AttributeError:
            pass
    try:
        if getattr(app, "stack", None) is not None and getattr(app, "dock_view", None) is not None:
            if app.stack.currentWidget() is app.dock_view and getattr(app, "view_controller", None) is not None:
                app.view_controller.toggle_docked(False)
    except Exception:
        logging.debug("Could not undock after stale startup restore.", exc_info=True)
    try:
        message = "Previous session could not be restored. Choose folders and scan again."
        if session_id:
            message = f"Previous session {session_id} could not be restored. Choose folders and scan again."
        controller._surface_workflow_error(message)
    except Exception:
        logging.debug("Could not surface stale startup restore message.", exc_info=True)
    logging.info("Cleared stale startup restore state.", extra={"target": target, "session_id": session_id})


def restore_previous_session(controller, *, frontload: bool = False, bridge_factory=None) -> None:
    """Restore the engine and staging records from the last session."""
    if bridge_factory is None:
        from unshuffle.bridge.workflow_bridge import create_workflow_bridge as bridge_factory

    if not qt_object_alive(controller) or not qt_object_alive(controller.app):
        return
    from . import workflow_handover

    workflow_handover.clear_build_handover_state(controller, preserve_persisted=True)
    if getattr(controller.app, "_is_closing", False):
        controller._emit_restore_finished(False)
        return
    target = (
        controller.app.settings.value("last_library_target", "")
        or controller.app.settings.value("last_scan_source", "")
        or controller.app.settings.value("last_target", "")
    )
    if not target:
        restored_handover = workflow_handover.restore_build_handover_state(controller)
        if not restored_handover:
            _clear_stale_startup_restore(controller)
        controller._emit_restore_finished(False)
        return

    try:
        sid = controller.app.settings.value("last_scan_session_id", "")
        from .workers import StartupRestoreWorker
        from ..utils import ui_helpers

        ui_helpers.set_ui_busy(controller.app, True)

        worker = StartupRestoreWorker(target, sid)
        controller.app._restore_session_worker = worker

        def _finish_restore(payload):
            from ..utils import ui_helpers

            if not qt_object_alive(controller) or not qt_object_alive(controller.app):
                return
            if controller.app._restore_session_worker is not worker:
                return
            controller.app._restore_session_worker = None
            restored_session_id = str(payload.get("session_id") or sid or "")
            restored_target = str(payload.get("target") or target)
            sources = payload.get("sources") or []
            plan = payload.get("plan") or []
            if not plan:
                ui_helpers.set_ui_busy(controller.app, False)
                restored_handover = workflow_handover.restore_build_handover_state(controller)
                if not restored_handover:
                    _clear_stale_startup_restore(
                        controller,
                        target=restored_target,
                        session_id=restored_session_id,
                    )
                controller._emit_restore_finished(False)
                return
            engine = payload.get("engine")
            if engine is None:
                try:
                    engine = bridge_factory(
                        Path(restored_target),
                        session_id=restored_session_id if restored_session_id else None,
                    )
                except Exception:
                    logging.exception("Failed to create restored workflow bridge.")
                    ui_helpers.set_ui_busy(controller.app, False)
                    controller._surface_workflow_error("Startup restore failed.")
                    controller._emit_restore_finished(False)
                    return
            if payload.get("db_scope") == "local":
                try:
                    raw_engine = getattr(engine, "engine", engine)
                    global_db = getattr(raw_engine, "db", None)
                    local_db = getattr(raw_engine, "local_db", None)
                    if local_db is not None and global_db is not local_db:
                        if global_db is not None:
                            global_db.close()
                        raw_engine.db = local_db
                except Exception:
                    logging.exception("Failed to align restored workflow with local staging database.")
            controller.set_engine(engine)
            if sources:
                engine.session_source_roots = [Path(s) for s in sources]
                engine.session_source_root = Path(sources[0])
            if hasattr(controller.app.settings, "setValue"):
                if restored_session_id:
                    controller.app.settings.setValue("last_scan_session_id", restored_session_id)
                if restored_target:
                    controller.app.settings.setValue("last_library_target", restored_target)
            if (
                hasattr(controller.app.settings, "remove")
                and restored_session_id
                and (payload.get("db_scope") == "local" or restored_session_id != str(sid or ""))
            ):
                controller.app.settings.remove(f"tagging_pass/{restored_session_id}/state_key")
                controller.app.settings.remove(f"tagging_pass/{restored_session_id}/duplicate_count")
            stats = {
                "total_scanned": len(plan),
                "added_count": len(plan),
                "lib_dupe_count": 0,
                "session_dupe_count": 0,
                "total_dupe_count": 0,
            }
            if hasattr(controller.app, "_restoring_library_page_state"):
                controller.app._restoring_library_page_state = True

            def _finish_restored_session() -> None:
                from ..utils import ui_helpers

                if not qt_object_alive(controller) or not qt_object_alive(controller.app):
                    return
                ui_helpers.set_ui_busy(controller.app, False)
                if hasattr(controller.app, "_restoring_library_page_state"):
                    controller.app._restoring_library_page_state = False
                try:
                    if hasattr(controller.app, "restore_library_page_state"):
                        controller.app.restore_library_page_state()
                    workflow_handover.restore_build_handover_state(controller)
                finally:
                    controller._emit_restore_finished(True)

            controller.finalize_scan_data(
                plan,
                False,
                stats,
                show_summary=False,
                persist_staging=False,
                defer_background_work=True,
                schedule_background_work=not frontload,
                on_ready=_finish_restored_session,
            )

        def _restore_error(message):
            from ..utils import ui_helpers

            if not qt_object_alive(controller) or not qt_object_alive(controller.app):
                return
            if controller.app._restore_session_worker is not worker:
                return
            controller.app._restore_session_worker = None
            ui_helpers.set_ui_busy(controller.app, False)
            controller._surface_workflow_error(f"Startup restore failed: {message}")
            controller._emit_restore_finished(False)

        worker.finished.connect(_finish_restore)
        worker.finished.connect(worker.deleteLater)
        worker.error.connect(_restore_error)
        worker.error.connect(worker.deleteLater)
        worker.start()
    except Exception:
        from ..utils import ui_helpers

        logging.exception("Failed to restore previous session engine.")
        ui_helpers.set_ui_busy(controller.app, False)
        controller._surface_workflow_error("Startup restore failed.")
        controller._emit_restore_finished(False)
