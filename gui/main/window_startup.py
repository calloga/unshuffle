from __future__ import annotations

from PySide6.QtCore import QTimer

from unshuffle.diagnostics import write_launcher_event_log


def frontload_startup(window, status_callback=None, done_callback=None) -> None:
    """Restore the previous session and prepare first-use surfaces before showing the app."""
    startup_done = {"value": False}
    watchdog = QTimer(window)
    watchdog.setSingleShot(True)
    window._frontloading_startup = True

    def _status(text: str) -> None:
        write_launcher_event_log("frontload-status", text=text)
        if callable(status_callback):
            status_callback(text)

    def _done() -> None:
        if startup_done["value"]:
            return
        write_launcher_event_log("frontload-done")
        startup_done["value"] = True
        window._frontloading_startup = False
        if watchdog.isActive():
            watchdog.stop()
        if getattr(window, "footer", None):
            window.footer.set_busy_state(False)
        if callable(done_callback):
            done_callback()

    def _timeout_release() -> None:
        _status("Startup is taking longer than expected...")
        if getattr(window, "footer", None):
            window.footer.set_status("Startup frontload timed out; continuing in the main window.")
        _done()

    def _prepare_views() -> None:
        write_launcher_event_log("frontload-prepare-views")
        if startup_done["value"]:
            return
        if not getattr(window, "model", None):
            _done()
            return
        _status("Preparing library views...")
        if getattr(window, "view_controller", None):
            window.view_controller.frontload_library_views(include_map=False)
        QTimer.singleShot(0, _prepare_metadata)

    def _prepare_metadata() -> None:
        write_launcher_event_log("frontload-prepare-metadata")
        if startup_done["value"]:
            return
        if not getattr(window, "model", None):
            _done()
            return
        tagging = getattr(window, "tagging_controller", None)
        coherence = getattr(window, "coherence_controller", None) if window._should_auto_check_coherence_on_start() else None
        if tagging is None:
            _prepare_final_map()
            return

        _status("Checking duplicate tags...")

        def _after_tagging() -> None:
            if startup_done["value"]:
                return
            try:
                tagging.taggingFinished.disconnect(_after_tagging)
            except (RuntimeError, TypeError):
                pass
            if coherence is None:
                _prepare_final_map()
                return
            engine = getattr(window, "engine", None)
            if not engine or not getattr(engine, "db", None):
                _prepare_final_map()
                return
            _status("Checking library coherence...")

            def _after_coherence() -> None:
                if startup_done["value"]:
                    return
                try:
                    coherence.coherenceFinished.disconnect(_after_coherence)
                except (RuntimeError, TypeError):
                    pass
                _prepare_final_map()

            coherence.coherenceFinished.connect(_after_coherence)
            if getattr(coherence, "_running_workers", None):
                return
            coherence.start_coherence_audit(mode="background")

        tagging.taggingFinished.connect(_after_tagging)
        tagging.start_tagging_pass(schedule_coherence=False)

    def _prepare_final_map() -> None:
        write_launcher_event_log("frontload-prepare-final-map")
        if startup_done["value"]:
            return
        _status("Ready.")
        QTimer.singleShot(120, _done)

    def _after_restore(restored: bool) -> None:
        write_launcher_event_log("frontload-after-restore", restored=restored)
        if startup_done["value"]:
            return
        try:
            window.workflow_controller.restoreFinished.disconnect(_after_restore)
        except (RuntimeError, TypeError):
            pass
        if restored:
            QTimer.singleShot(0, _prepare_views)
        else:
            _done()

    _status("Restoring previous library...")
    watchdog.timeout.connect(_timeout_release)
    watchdog.start(90_000)
    window.workflow_controller.restoreFinished.connect(_after_restore)
    window.workflow_controller.restore_session(frontload=True)
