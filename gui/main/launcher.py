import logging
import os
import sys
import threading
import traceback
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QTimer, QLockFile
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox
import shiboken6

_GLOBAL_LOCK = None

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    __package__ = "gui.main"

from unshuffle.diagnostics import enable_launcher_fault_log, write_launcher_crash_log, write_launcher_event_log

from ..core.settings_controller import DOCKED_MODE_KEY, SettingsController, create_app_settings
from ..core.workflow_controller import show_scan_summary_dialog
from ..utils.app_icon import apply_app_icon
from ..utils.constants import MAIN_WINDOW_HEIGHT, MAIN_WINDOW_WIDTH
from ..widgets.startup_splash import StartupSplash
from ..widgets.startup_scan_monitor import StartupScanMonitor
from ..widgets.startup_tray import StartupTrayController
from ..widgets.startup_launcher import StartupLauncherDialog, StartupLaunchRequest
from .window import ModernApp


_ORIGINAL_EXCEPTHOOK = sys.excepthook
_ORIGINAL_THREADING_EXCEPTHOOK = getattr(threading, "excepthook", None)


def _install_launcher_exception_hooks() -> None:
    enable_launcher_fault_log()

    def _handle_exception(exc_type, exc, tb) -> None:
        trace_text = "".join(traceback.format_exception(exc_type, exc, tb))
        logging.error("Uncaught GUI launcher exception:\n%s", trace_text)
        write_launcher_crash_log("gui_launcher", trace_text=trace_text)
        if _ORIGINAL_EXCEPTHOOK is not None:
            _ORIGINAL_EXCEPTHOOK(exc_type, exc, tb)

    def _handle_thread_exception(args) -> None:
        _handle_exception(args.exc_type, args.exc_value, args.exc_traceback)
        if _ORIGINAL_THREADING_EXCEPTHOOK is not None:
            _ORIGINAL_THREADING_EXCEPTHOOK(args)

    sys.excepthook = _handle_exception
    if _ORIGINAL_THREADING_EXCEPTHOOK is not None:
        threading.excepthook = _handle_thread_exception


def _launcher_event(message: str, **fields: object) -> None:
    logging.info("Launcher: %s %s", message, fields)
    write_launcher_event_log(message, **fields)


def _qt_object_alive(obj) -> bool:
    if obj is None:
        return False
    try:
        return shiboken6.isValid(obj)
    except RuntimeError:
        return False
    except TypeError:
        return True


def _configure_multimedia_backend() -> None:
    if sys.platform == "win32":
        os.environ["QT_MULTIMEDIA_BACKEND"] = "windows"
    elif sys.platform == "darwin":
        os.environ["QT_MULTIMEDIA_BACKEND"] = "darwin"


def _is_headless_linux() -> bool:
    return sys.platform.startswith("linux") and not (
        os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
    )


def _startup_request(settings_controller: SettingsController) -> StartupLaunchRequest | None:
    if settings_controller.get_show_startup_launcher():
        _launcher_event("show-startup-dialog")
        dialog = StartupLauncherDialog(settings_controller)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        app = QApplication.instance()
        if app is not None:
            app.processEvents()
        if dialog.exec() != QDialog.DialogCode.Accepted:
            _launcher_event("startup-dialog-dismissed")
            return None
        request = dialog.launch_request()
        _launcher_event("startup-dialog-accepted", mode=request.mode, roots=len(request.roots), target=request.target)
        settings_controller.set_library_view_modes(request.view_modes)
        settings_controller.set_startup_launcher_last_choice(request.to_settings())
        settings_controller.set_show_startup_launcher(request.show_launcher_next_time)
        _persist_request_context(settings_controller, request)
        return request

    request = StartupLaunchRequest.from_settings(
        settings_controller.get_startup_launcher_last_choice(),
        fallback_target=str(
            settings_controller.settings.value("last_library_target", "")
            or settings_controller.settings.value("last_scan_source", "")
            or settings_controller.settings.value("last_target", "")
            or ""
        ),
    )
    _launcher_event("startup-request-from-settings", mode=request.mode, roots=len(request.roots), target=request.target)
    settings_controller.set_library_view_modes(request.view_modes)
    _persist_request_context(settings_controller, request)
    return request


def _persist_request_context(settings_controller: SettingsController, request: StartupLaunchRequest) -> None:
    settings = settings_controller.settings
    if request.target:
        if request.mode in {"restore", "refresh"}:
            settings.setValue("last_library_target", request.target)
        else:
            settings.setValue("last_target", request.target)
    if request.session_id:
        settings.setValue("last_scan_session_id", request.session_id)
    if request.roots:
        settings.setValue("last_scan_source", request.roots[0])


def _dismiss_splash(splash: StartupSplash | None) -> None:
    if splash is None or not _qt_object_alive(splash):
        return
    try:
        if hasattr(splash, "hide"):
            splash.hide()
        if hasattr(splash, "close"):
            splash.close()
        if hasattr(splash, "deleteLater"):
            splash.deleteLater()
    except Exception:
        pass
    app = QApplication.instance()
    if app is not None:
        app.processEvents()


def _show_window(window: ModernApp, splash: StartupSplash | None = None) -> None:
    if not _qt_object_alive(window):
        _launcher_event("show-main-window-skipped-deleted-window", splash=bool(splash))
        _dismiss_splash(splash)
        return
    _launcher_event("show-main-window", splash=bool(splash))
    app = QApplication.instance()
    if app is not None:
        app.setQuitOnLastWindowClosed(False)
    window._defer_window_show = False
    _normalize_main_window_for_presentation(window)

    def _present_window() -> None:
        if not _qt_object_alive(window):
            return
        _normalize_main_window_for_presentation(window)
        try:
            window.setWindowState(window.windowState() & ~Qt.WindowMinimized)
        except TypeError:
            pass
        window.showNormal()
        window.show()
        window.raise_()
        window.activateWindow()

    _present_window()
    _dismiss_splash(splash)
    if app is not None:
        app.processEvents()

    def _focus_window() -> None:
        if not _qt_object_alive(window):
            return
        dialogs = getattr(window, "findChildren", lambda _t: [])(QDialog)
        if not isinstance(dialogs, list):
            dialogs = []
        if getattr(window, "_scan_finalizing", False) is True or any(isinstance(w, QDialog) and w.isModal() for w in dialogs):
            return
        if not window.isVisible():
            _present_window()
        window.raise_()
        window.activateWindow()
        focus_target = getattr(getattr(window, "library_tab", None), "edit_search", None)
        if focus_target is not None:
            focus_target.setFocus()

    QTimer.singleShot(0, _focus_window)
    QTimer.singleShot(250, _focus_window)
    QTimer.singleShot(1000, _focus_window)


def _normalize_main_window_for_presentation(window: ModernApp) -> None:
    if not _qt_object_alive(window):
        return
    if getattr(window, "stack", None) is not None and getattr(window, "dock_view", None) is not None:
        if window.stack.currentWidget() is window.dock_view:
            return
    try:
        if hasattr(window, "setWindowFlags") and hasattr(window, "windowFlags"):
            window.setWindowFlags((window.windowFlags() & ~Qt.WindowStaysOnTopHint) | Qt.WindowCloseButtonHint)
        if hasattr(window, "setMaximumWidth"):
            window.setMaximumWidth(16777215)
        if hasattr(window, "setMinimumWidth"):
            window.setMinimumWidth(MAIN_WINDOW_WIDTH)
        if hasattr(window, "width") and hasattr(window, "height") and hasattr(window, "resize"):
            width = window.width()
            height = window.height()
            if width < MAIN_WINDOW_WIDTH or height < MAIN_WINDOW_HEIGHT:
                window.resize(max(width, MAIN_WINDOW_WIDTH), max(height, MAIN_WINDOW_HEIGHT))
    except Exception:
        pass


def _reset_window_for_startup_relaunch(window: Any) -> None:
    if not _qt_object_alive(window):
        return
    try:
        if getattr(window, "stack", None) is not None and getattr(window, "dock_view", None) is not None:
            if window.stack.currentWidget() is window.dock_view:
                window.view_controller.toggle_docked(False)
        _normalize_main_window_for_presentation(window)
        window.hide()
    except Exception:
        pass


def _undock_startup_refresh_window(window: Any) -> None:
    if not _qt_object_alive(window):
        return
    try:
        settings_controller = getattr(window, "settings_controller", None)
        settings = getattr(settings_controller, "settings", None)
        if settings is not None:
            settings.setValue(DOCKED_MODE_KEY, False)
        if (
            getattr(window, "stack", None) is not None
            and getattr(window, "dock_view", None) is not None
            and getattr(window, "view_controller", None) is not None
            and window.stack.currentWidget() is window.dock_view
        ):
            window.view_controller.toggle_docked(False)
        _normalize_main_window_for_presentation(window)
    except Exception:
        pass


def _launch_restore(window: ModernApp, app: QApplication) -> None:
    app.setQuitOnLastWindowClosed(False)
    _launcher_event("launch-restore-start")
    splash: StartupSplash | None = None
    try:
        splash = StartupSplash()
        splash.set_status("Restoring previous library...")
        splash.show_centered()
        app.processEvents()
        window._startup_launch_refs = {"splash": splash}

        def _set_restore_status(text: str) -> None:
            _launcher_event("restore-status", text=text)
            try:
                if _qt_object_alive(splash):
                    splash.set_status(text)
                elif _qt_object_alive(window) and hasattr(window, "set_search_status"):
                    window.set_search_status(text)
            except RuntimeError:
                pass

        def _finish_restore() -> None:
            _launcher_event("restore-frontload-finished")
            _show_window(window, splash)
            if _qt_object_alive(window):
                window._startup_launch_refs = {}

        _launcher_event("restore-frontload-start")
        window.frontload_startup(_set_restore_status, _finish_restore)
    except Exception:
        trace_text = traceback.format_exc()
        logging.error("Startup restore failed:\n%s", trace_text)
        write_launcher_crash_log("gui_launcher", trace_text=trace_text)
        _show_window(window, splash)


def _launch_refresh(window: Any, request: StartupLaunchRequest, app: QApplication) -> None:
    app.setQuitOnLastWindowClosed(False)
    _launcher_event("launch-refresh-start", roots=len(request.roots), target=request.target)
    _undock_startup_refresh_window(window)
    try:
        monitor = StartupScanMonitor()
    except Exception:
        trace_text = traceback.format_exc()
        logging.error("Startup scan monitor failed:\n%s", trace_text)
        write_launcher_crash_log("gui_launcher", trace_text=trace_text)
        _show_window(window)
        return
    monitor.set_status("Preparing selected session...")
    monitor.show_near_center()
    app.processEvents()
    launched = {"done": False}
    startup_cancel_requested = {"value": False}
    summary_stats: dict[str, object] = {}
    splash_ref: dict[str, StartupSplash | None] = {"splash": None}
    tray_ref: dict[str, StartupTrayController | None] = {"tray": None}
    window._startup_launch_refs = {
        "monitor": monitor,
        "splash": None,
        "tray": None,
        "request": request,
    }

    def _dispatch_launch_request(next_request: StartupLaunchRequest) -> None:
        if next_request.mode == "restore":
            _launch_restore(window, app)
        elif next_request.mode == "refresh":
            _launch_refresh(window, next_request, app)
        elif next_request.mode == "import_session":
            _show_window(window)
            QTimer.singleShot(150, lambda: window.data_manager.import_session_from_folder(next_request.import_path, parent_widget=window))
        elif next_request.mode == "import_csv":
            _show_window(window)

            def _do_import_csv():
                recs = window.data_manager.import_from_csv(next_request.import_path)
                if recs:
                    window.workflow_controller.handle_scan_finished(recs, False, {})

            QTimer.singleShot(150, _do_import_csv)
        else:
            _show_window(window)

    def _reopen_startup_launcher() -> None:
        settings_controller = getattr(window, "settings_controller", None)
        if settings_controller is None:
            app.quit()
            return
        _reset_window_for_startup_relaunch(window)
        _launcher_event("startup-scan-cancel-reopen-launcher")
        dialog = StartupLauncherDialog(settings_controller, force_refresh=True)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        app.processEvents()
        if dialog.exec() != QDialog.DialogCode.Accepted:
            _launcher_event("startup-scan-cancel-launcher-dismissed")
            app.quit()
            return
        next_request = dialog.launch_request()
        if next_request.mode == "restore":
            next_request = StartupLaunchRequest(
                mode="refresh",
                target=next_request.target,
                session_id="",
                roots=next_request.roots,
                view_modes=next_request.view_modes,
                show_launcher_next_time=next_request.show_launcher_next_time,
            )
        settings_controller.set_library_view_modes(next_request.view_modes)
        settings_controller.set_startup_launcher_last_choice(next_request.to_settings())
        settings_controller.set_show_startup_launcher(next_request.show_launcher_next_time)
        _persist_request_context(settings_controller, next_request)
        _dispatch_launch_request(next_request)

    def _finish_launch(*, reopen_launcher: bool = False) -> None:
        if launched["done"]:
            return
        launched["done"] = True
        try:
            window.worker_manager.progress.disconnect(_set_scan_status)
        except (RuntimeError, TypeError):
            pass
        try:
            window.worker_manager.error.disconnect(_scan_error)
        except (RuntimeError, TypeError):
            pass
        try:
            window.workflow_controller.scanFinished.disconnect(_scan_finished)
        except (RuntimeError, TypeError, AttributeError):
            pass
        tray = tray_ref["tray"]
        if tray is not None:
            tray.finish()
        if _qt_object_alive(monitor):
            monitor.close()
        if reopen_launcher:
            _reopen_startup_launcher()
            return
        _show_window(window, splash_ref["splash"])
        if summary_stats and _qt_object_alive(window):
            window.workflow_controller._last_scan_stats = summary_stats
            if hasattr(window.footer, "show_scan_summary_button"):
                window.footer.show_scan_summary_button()
        if _qt_object_alive(window):
            window._startup_launch_refs = {}
        _launcher_event("launch-refresh-finished")

    def _fail_open(trace_text: str) -> None:
        logging.error("Startup refresh failed:\n%s", trace_text)
        write_launcher_crash_log("gui_launcher", trace_text=trace_text)
        _finish_launch()

    def _set_scan_status(payload) -> None:
        if launched["done"]:
            return
        if _qt_object_alive(monitor):
            monitor.set_status(payload)
        tray = tray_ref["tray"]
        if tray is not None:
            tray.update_status(payload)

    def _set_warmup_status(text: str) -> None:
        if launched["done"]:
            return
        splash = splash_ref["splash"]
        if _qt_object_alive(splash):
            splash.set_status(text)

    def _start_warmup_splash() -> None:
        if launched["done"]:
            return
        splash = StartupSplash()
        splash.set_status("Preparing library...")
        splash.show_centered()
        splash_ref["splash"] = splash
        if _qt_object_alive(window):
            window._startup_launch_refs["splash"] = splash
        tray = tray_ref["tray"]
        if tray is not None:
            tray.finish()
        if _qt_object_alive(monitor):
            monitor.close()
        app.processEvents()

    def _scan_error(_message) -> None:
        _finish_launch(reopen_launcher=startup_cancel_requested["value"])

    def _scan_finished(stats) -> None:
        if isinstance(stats, dict) and stats.get("cancelled") and startup_cancel_requested["value"]:
            _finish_launch(reopen_launcher=True)

    def _cancel_startup_scan() -> None:
        startup_cancel_requested["value"] = True
        if _qt_object_alive(monitor):
            monitor.set_status({"message": "Canceling scan..."})
        window.worker_manager.request_cancel()

    def _start() -> None:
        try:
            roots = [root for root in request.roots if root.strip()]
            if not roots:
                _finish_launch()
                return
            window.worker_manager.progress.connect(_set_scan_status)
            window.worker_manager.error.connect(_scan_error)
            window.workflow_controller.scanFinished.connect(_scan_finished)
            tray = StartupTrayController(
                monitor,
                cancel_callback=_cancel_startup_scan,
                quit_callback=app.quit,
                parent=app,
            )
            tray_ref["tray"] = tray
            window._startup_launch_refs["tray"] = tray
            tray.start()
            if hasattr(monitor, "set_cancel_handler"):
                monitor.set_cancel_handler(_cancel_startup_scan)
            started = window.workflow_controller.start_scan(
                roots,
                append=False,
                last_target=request.target or None,
                require_clear_draft=False,
                finalize_options={
                    "show_summary": False,
                    "summary_callback": lambda stats: summary_stats.update(dict(stats or {})),
                    "defer_background_work": False,
                    "schedule_background_work": True,
                    "on_background_work_start": _start_warmup_splash,
                    "status_callback": _set_warmup_status,
                    "on_ready": _finish_launch,
                },
            )
            if not started:
                _finish_launch()
        except Exception:
            _fail_open(traceback.format_exc())

    QTimer.singleShot(0, _start)


def main() -> int:
    try:
        _install_launcher_exception_hooks()
        _launcher_event("main-enter", argv=" ".join(sys.argv))
        if _is_headless_linux():
            _launcher_event("headless-linux-exit")
            print("Unshuffle GUI requires a graphical desktop session on Linux (DISPLAY/WAYLAND_DISPLAY).")
            return 1

        _configure_multimedia_backend()
        _launcher_event("create-qapplication")
        app = QApplication(sys.argv)

        import tempfile
        global _GLOBAL_LOCK
        lock_path = Path(tempfile.gettempdir()) / "unshuffle_app.lock"
        _GLOBAL_LOCK = QLockFile(str(lock_path))
        if not _GLOBAL_LOCK.tryLock(100):
            _launcher_event("already-running-exit")
            apply_app_icon()
            msg = QMessageBox()
            msg.setIcon(QMessageBox.Warning)
            msg.setWindowTitle("Unshuffle Already Running")
            msg.setText("Another instance of Unshuffle is already running.")
            msg.setInformativeText("Please close the existing window before opening a new instance.")
            msg.exec()
            return 0

        app.setQuitOnLastWindowClosed(False)
        app.aboutToQuit.connect(lambda: _launcher_event("qapplication-about-to-quit"))
        apply_app_icon()
        settings_controller = SettingsController(create_app_settings())
        request = _startup_request(settings_controller)
        if request is None:
            logging.info("Startup launcher was dismissed; exiting without opening the main window.")
            _launcher_event("startup-dialog-dismissed-exit")
            return 0

        _launcher_event("create-main-window", mode=request.mode)
        window = ModernApp(defer_startup_restore=True)
        if request.mode == "restore":
            _launch_restore(window, app)
        elif request.mode == "refresh":
            _launch_refresh(window, request, app)
        elif request.mode == "import_session":
            _show_window(window)
            QTimer.singleShot(150, lambda: window.data_manager.import_session_from_folder(request.import_path, parent_widget=window))
        elif request.mode == "import_csv":
            _show_window(window)
            def _do_import_csv():
                recs = window.data_manager.import_from_csv(request.import_path)
                if recs:
                    window.workflow_controller.handle_scan_finished(recs, False, {})
            QTimer.singleShot(150, _do_import_csv)
        else:
            _show_window(window)
        _launcher_event("enter-event-loop", mode=request.mode)
        result = app.exec()
        _launcher_event("event-loop-returned", result=result)
        return result
    except Exception:
        import traceback

        trace_text = traceback.format_exc()
        logging.error("GUI launcher crashed:\n%s", trace_text)
        write_launcher_crash_log("gui_launcher", trace_text=trace_text)
        print(trace_text)
        return 1
