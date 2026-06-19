from typing import Any
import json
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from typing import cast

from PySide6.QtCore import QAbstractItemModel, QCoreApplication, QEvent, QObject, Qt, Signal

from gui.core import main_window_scan_flow
from gui.utils.ui_helpers import on_undo_stack_changed
from unshuffle.runtime.engine import RuntimeUnshuffler as Unshuffler
from unshuffle.core import PlanRecord
from unshuffle.core.features import FEATURE_VECTOR_SIZE, feature_blob_from_vector


def _close_qt_window(window, app=None) -> None:
    active_app = app or QCoreApplication.instance()
    if hasattr(window, "_is_closing"):
        window._is_closing = True
    if active_app is not None:
        active_app.processEvents()
        active_app.processEvents()
    view_controller = getattr(window, "view_controller", None)
    timer = getattr(view_controller, "_tree_rebuild_timer", None)
    if timer is not None:
        try:
            timer.stop()
        except RuntimeError:
            pass
    restore_worker = getattr(window, "_restore_session_worker", None)
    if restore_worker is not None:
        try:
            if restore_worker.isRunning():
                restore_worker.wait(1000)
        except (AttributeError, RuntimeError):
            pass
    window.close()
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    if active_app is not None:
        active_app.processEvents()
        active_app.processEvents()


def tearDownModule() -> None:
    app = QCoreApplication.instance()
    if app is None:
        return
    for widget in list(getattr(app, "topLevelWidgets", lambda: [])()):
        widget.close()
    app.processEvents()
    app.processEvents()


class MainWindowDebounceTests(unittest.TestCase):
    def test_startup_launcher_new_session_lives_under_directories(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        from gui.widgets.startup_launcher import StartupLauncherDialog

        app = QApplication.instance() or QApplication([])

        settings = SimpleNamespace(
            value=mock.Mock(
                side_effect=lambda key, default="": {
                    "last_scan_session_id": "last-session",
                    "last_target": "D:/Music/Drum Kits",
                    "last_scan_source": "D:/Music/Drum Kits",
                }.get(key, default)
            )
        )
        settings_controller = SimpleNamespace(
            settings=settings,
            get_library_view_modes=mock.Mock(return_value=("table", "tree", "map")),
        )

        with mock.patch("gui.widgets.startup_launcher.load_session_sources", return_value=["D:/Music/Drum Kits"]), \
             mock.patch("gui.widgets.startup_launcher.QFileDialog.getExistingDirectory", return_value="D:/Samples/New Pack"):
            dialog = StartupLauncherDialog(settings_controller)
            self.assertFalse(hasattr(dialog, "pending_combo"))
            self.assertEqual(dialog.view_checks["table"].text(), "Table")
            self.assertFalse(dialog.view_checks["table"].icon().isNull())
            dialog._new_session()

        request = dialog.launch_request()
        self.assertEqual(request.mode, "refresh")
        self.assertEqual(request.session_id, "")
        self.assertEqual(request.roots, ("D:/Samples/New Pack",))
        self.assertEqual(request.view_modes, ("table", "tree", "map"))
        _close_qt_window(dialog, app)

    def test_startup_launcher_uses_theme_without_logo_pixmap(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtGui import QColor
        from PySide6.QtWidgets import QApplication, QLabel
        from gui.styles import SUNSET_THEME_KEY, THEMES
        from gui.utils.color_helpers import make_qcolor
        from gui.widgets.startup_launcher import StartupLauncherDialog

        app = QApplication.instance() or QApplication([])
        settings = SimpleNamespace(
            value=mock.Mock(
                side_effect=lambda key, default="": {
                    "last_scan_session_id": "",
                    "last_target": "",
                    "last_scan_source": "",
                }.get(key, default)
            )
        )
        settings_controller = SimpleNamespace(
            settings=settings,
            get_theme_key=mock.Mock(return_value=SUNSET_THEME_KEY),
            get_zoom_percent=mock.Mock(return_value=100),
            get_library_view_modes=mock.Mock(return_value=("table", "tree", "map")),
        )

        dialog = StartupLauncherDialog(settings_controller)
        try:
            labels_with_pixmaps = [label for label in dialog.findChildren(QLabel) if label.pixmap() is not None and not label.pixmap().isNull()]
            self.assertEqual(labels_with_pixmaps, [])
            self.assertIn(make_qcolor(THEMES[SUNSET_THEME_KEY].bg_darker).name(), dialog.styleSheet())
            self.assertIn(make_qcolor(THEMES[SUNSET_THEME_KEY].surface_card).darker(112).name(), dialog.styleSheet())
            self.assertNotIn("#080d17", dialog.styleSheet().lower())
        finally:
            _close_qt_window(dialog, app)

    def test_startup_launcher_keeps_light_theme_surface_values_unmodified(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtGui import QColor
        from PySide6.QtWidgets import QApplication
        from gui.styles import OCEAN_THEME_KEY, THEMES
        from gui.utils.color_helpers import make_qcolor
        from gui.widgets.startup_launcher import StartupLauncherDialog

        app = QApplication.instance() or QApplication([])
        settings = SimpleNamespace(value=mock.Mock(return_value=""))
        settings_controller = SimpleNamespace(
            settings=settings,
            get_theme_key=mock.Mock(return_value=OCEAN_THEME_KEY),
            get_zoom_percent=mock.Mock(return_value=100),
            get_library_view_modes=mock.Mock(return_value=("table", "tree", "map")),
        )

        dialog = StartupLauncherDialog(settings_controller)
        try:
            style = dialog.styleSheet()
            self.assertIn(f"QDialog {{ background: {make_qcolor(THEMES[OCEAN_THEME_KEY].bg_darker).name()};", style)
            self.assertIn(make_qcolor(THEMES[OCEAN_THEME_KEY].surface_card).name(), style)
            self.assertIn(make_qcolor(THEMES[OCEAN_THEME_KEY].action_secondary).name(), style)
            self.assertIn(f"QPushButton:hover {{ background: {make_qcolor(THEMES[OCEAN_THEME_KEY].action_secondary).name()}; }}", style)
            self.assertIn(
                f"QPushButton[launcherPanelButton=\"true\"]:hover {{ background: {make_qcolor(THEMES[OCEAN_THEME_KEY].primary).name()};",
                style,
            )
            self.assertNotIn(
                f"QPushButton[launcherPanelButton=\"true\"] {{ background: {make_qcolor(THEMES[OCEAN_THEME_KEY].bg_hover).name()}; }}",
                style,
            )
            self.assertIn("QPushButton[launcherPanelButton=\"true\"]:hover", style)
        finally:
            _close_qt_window(dialog, app)

    def test_startup_launcher_force_refresh_keeps_roots_but_clears_restore(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        from gui.widgets.startup_launcher import StartupLauncherDialog

        app = QApplication.instance() or QApplication([])
        settings = SimpleNamespace(
            value=mock.Mock(
                side_effect=lambda key, default="": {
                    "last_scan_session_id": "last-session",
                    "last_target": "D:/Music/Drum Kits",
                    "last_scan_source": "D:/Music/Drum Kits",
                }.get(key, default)
            )
        )
        settings_controller = SimpleNamespace(
            settings=settings,
            get_library_view_modes=mock.Mock(return_value=("table", "tree", "map")),
        )

        with mock.patch("gui.widgets.startup_launcher.load_session_sources", return_value=["D:/Music/Drum Kits"]):
            dialog = StartupLauncherDialog(settings_controller, force_refresh=True)

        request = dialog.launch_request()

        self.assertEqual(request.mode, "refresh")
        self.assertEqual(request.session_id, "")
        self.assertEqual(request.roots, ("D:/Music/Drum Kits",))
        _close_qt_window(dialog, app)

    def test_startup_tray_notice_waits_until_monitor_is_hidden(self):
        from gui.widgets import startup_tray

        messages = []

        class _Signal:
            def connect(self, _callback):
                pass

        class _FakeTrayIcon:
            class MessageIcon:
                Information = object()

            class ActivationReason:
                DoubleClick = object()

            @staticmethod
            def isSystemTrayAvailable():
                return True

            @staticmethod
            def supportsMessages():
                return True

            def __init__(self, _icon, _parent):
                self.activated = _Signal()

            def setContextMenu(self, _menu):
                pass

            def setToolTip(self, _text):
                pass

            def show(self):
                pass

            def hide(self):
                pass

            def showMessage(self, title, body, _icon, _timeout):
                messages.append((title, body))

        monitor = mock.Mock()
        with mock.patch("gui.widgets.startup_tray.QSystemTrayIcon", _FakeTrayIcon), \
            mock.patch("gui.widgets.startup_tray.QMenu", mock.Mock):
            tray = startup_tray.StartupTrayController(monitor)

            tray.start()
            self.assertEqual(messages, [])

            tray.hide_monitor()
            self.assertEqual(len(messages), 1)

    def test_startup_show_window_shows_main_window_before_closing_splash(self):
        from gui.main import launcher

        calls = []
        window = mock.Mock()
        window.library_tab.edit_search = mock.Mock()
        splash = mock.Mock()

        window.showNormal.side_effect = lambda: calls.append("show")
        window.show.side_effect = lambda: calls.append("show-visible")
        window.raise_.side_effect = lambda: calls.append("raise")
        window.activateWindow.side_effect = lambda: calls.append("activate")
        window.isVisible.return_value = True
        splash.close.side_effect = lambda: calls.append("close")

        with mock.patch("gui.main.launcher.QTimer.singleShot", side_effect=lambda _delay, func: func()):
            launcher._show_window(window, splash)

        self.assertEqual(calls[0], "show")
        self.assertIn("close", calls)
        self.assertIn("show", calls)
        self.assertIn("show-visible", calls)
        self.assertIn("activate", calls)
        self.assertLess(calls.index("show-visible"), calls.index("close"))
        window.library_tab.edit_search.setFocus.assert_called()

    def test_startup_refresh_clears_persisted_docked_mode(self):
        from gui.main import launcher
        from gui.core.settings_controller import DOCKED_MODE_KEY

        class _Settings:
            def __init__(self):
                self.values = {}

            def setValue(self, key, value):
                self.values[key] = value

        class _Stack:
            def __init__(self, dock_view, library_tab):
                self._current = dock_view
                self._library_tab = library_tab

            def currentWidget(self):
                return self._current

            def setCurrentWidget(self, widget):
                self._current = widget

        dock_view = object()
        library_tab = object()
        settings = _Settings()
        window = SimpleNamespace(
            settings_controller=SimpleNamespace(settings=settings),
            dock_view=dock_view,
            library_tab=library_tab,
            stack=_Stack(dock_view, library_tab),
            view_controller=SimpleNamespace(toggle_docked=mock.Mock()),
            setWindowFlags=mock.Mock(),
            windowFlags=mock.Mock(return_value=Qt.WindowStaysOnTopHint),
            setMaximumWidth=mock.Mock(),
            setMinimumWidth=mock.Mock(),
            width=mock.Mock(return_value=320),
            height=mock.Mock(return_value=400),
            resize=mock.Mock(),
        )
        window.view_controller.toggle_docked.side_effect = lambda checked: window.stack.setCurrentWidget(library_tab)

        launcher._undock_startup_refresh_window(window)

        self.assertEqual(settings.values[DOCKED_MODE_KEY], False)
        window.view_controller.toggle_docked.assert_called_once_with(False)
        self.assertIs(window.stack.currentWidget(), library_tab)
        window.setMaximumWidth.assert_called_with(16777215)
        window.resize.assert_called()

    def test_launcher_disables_last_window_quit_before_startup_dialog(self):
        from gui.main import launcher
        from gui.widgets.startup_launcher import StartupLaunchRequest

        app = mock.Mock()
        request = StartupLaunchRequest(mode="restore", target="D:/Samples", roots=("D:/Samples",))

        with mock.patch("gui.main.launcher._is_headless_linux", return_value=False), \
            mock.patch("gui.main.launcher._configure_multimedia_backend"), \
            mock.patch("gui.main.launcher.QApplication", return_value=app), \
            mock.patch("gui.main.launcher.QLockFile"), \
            mock.patch("gui.main.launcher.apply_app_icon"), \
            mock.patch("gui.main.launcher.SettingsController"), \
            mock.patch("gui.main.launcher.create_app_settings"), \
            mock.patch("gui.main.launcher._startup_request", return_value=request), \
            mock.patch("gui.main.launcher.ModernApp") as modern_app, \
            mock.patch("gui.main.launcher._launch_restore") as launch_restore:
            app.exec.return_value = 0

            result = launcher.main()

        self.assertEqual(result, 0)
        app.setQuitOnLastWindowClosed.assert_any_call(False)
        launch_restore.assert_called_once_with(modern_app.return_value, app)

    def test_launcher_dismissed_startup_dialog_exits_without_empty_window(self):
        from gui.main import launcher

        app = mock.Mock()

        with mock.patch("gui.main.launcher._install_launcher_exception_hooks"), \
            mock.patch("gui.main.launcher._is_headless_linux", return_value=False), \
            mock.patch("gui.main.launcher._configure_multimedia_backend"), \
            mock.patch("gui.main.launcher.QApplication", return_value=app), \
            mock.patch("gui.main.launcher.QLockFile"), \
            mock.patch("gui.main.launcher.apply_app_icon"), \
            mock.patch("gui.main.launcher.SettingsController"), \
            mock.patch("gui.main.launcher.create_app_settings"), \
            mock.patch("gui.main.launcher._startup_request", return_value=None), \
            mock.patch("gui.main.launcher.ModernApp") as modern_app, \
            mock.patch("gui.main.launcher._show_window") as show_window:
            app.exec.return_value = 0

            result = launcher.main()

        self.assertEqual(result, 0)
        modern_app.assert_not_called()
        show_window.assert_not_called()

    def test_refresh_launch_uses_scan_monitor_before_warmup_splash(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        from gui.main import launcher
        from gui.widgets.startup_launcher import StartupLaunchRequest

        class _WorkerManager(QObject):
            progress = Signal(dict)
            error = Signal(str)

        events = []

        class _FakeMonitor:
            def __init__(self):
                events.append("monitor-created")

            def set_status(self, _payload):
                events.append("monitor-status")

            def show_near_center(self):
                events.append("monitor-show")

            def close(self):
                events.append("monitor-close")

        class _FakeSplash:
            def __init__(self):
                events.append("splash-created")

            def set_status(self, _text):
                events.append("splash-status")

            def show_centered(self):
                events.append("splash-show")

            def close(self):
                events.append("splash-close")

        class _FakeTray:
            def __init__(self, *_args, **_kwargs):
                events.append("tray-created")

            def start(self):
                events.append("tray-start")

            def update_status(self, _payload):
                events.append("tray-status")

            def finish(self):
                events.append("tray-finish")

        captured_options = {}

        def _start_scan(_roots, **kwargs):
            captured_options.update(kwargs["finalize_options"])
            return True

        window = mock.Mock()
        window.worker_manager = _WorkerManager()
        window.workflow_controller.start_scan.side_effect = _start_scan
        window.footer.show_scan_summary_button.side_effect = lambda: events.append("summary-dialog")
        app = QApplication.instance() or QApplication([])
        assert isinstance(app, QApplication)
        request = StartupLaunchRequest(mode="refresh", target="D:/Samples", roots=("D:/Samples",))

        with (
            mock.patch("gui.main.launcher.StartupScanMonitor", _FakeMonitor),
            mock.patch("gui.main.launcher.StartupTrayController", _FakeTray),
            mock.patch("gui.main.launcher.StartupSplash", _FakeSplash),
            mock.patch("gui.main.launcher.QTimer.singleShot", side_effect=lambda _delay, func: func()),
            mock.patch("gui.main.launcher._show_window", side_effect=lambda _window, _splash=None: events.append("show-window")),
            mock.patch("gui.main.launcher.show_scan_summary_dialog", side_effect=lambda _window, _stats: events.append("summary-dialog")),
        ):
            launcher._launch_refresh(window, request, app)

            self.assertIn("on_background_work_start", captured_options)
            self.assertIn("status_callback", captured_options)
            self.assertFalse(captured_options["show_summary"])
            self.assertIn("summary_callback", captured_options)
            self.assertEqual(events[:2], ["monitor-created", "monitor-status"])

            captured_options["on_background_work_start"]()
            captured_options["status_callback"]("Checking library suggestions...")
            captured_options["summary_callback"]({"total_scanned": 1, "added_count": 1})
            captured_options["on_ready"]()

        self.assertIn("splash-created", events)
        self.assertIn("splash-show", events)
        self.assertIn("show-window", events)
        self.assertGreater(events.index("summary-dialog"), events.index("show-window"))
        self.assertFalse(app.quitOnLastWindowClosed())

    def test_restore_launch_waits_for_frontload_before_showing_window(self):
        from gui.main import launcher

        app = mock.Mock()
        window = mock.Mock()
        events = []
        done_ref = {}

        class _FakeSplash:
            def set_status(self, _text):
                events.append("splash-status")

            def show_centered(self):
                events.append("splash-show")

        def _frontload(_status, _done):
            events.append("frontload")
            done_ref["done"] = _done

        window.frontload_startup.side_effect = _frontload

        with mock.patch("gui.main.launcher.StartupSplash", _FakeSplash), \
             mock.patch("gui.main.launcher._show_window", side_effect=lambda _window, _splash=None: events.append("show-window")) as show_window:
            launcher._launch_restore(window, app)
            show_window.assert_not_called()
            done_ref["done"]()

        show_window.assert_called_once()
        self.assertIs(show_window.call_args.args[0], window)
        self.assertEqual(events, ["splash-status", "splash-show", "frontload", "show-window"])

    def test_refresh_launch_failures_fall_back_to_main_window(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtCore import QObject, Signal
        from PySide6.QtWidgets import QApplication
        from gui.main import launcher
        from gui.widgets.startup_launcher import StartupLaunchRequest

        class _WorkerManager(QObject):
            progress = Signal(dict)
            error = Signal(str)

        window = mock.Mock()
        window.worker_manager = _WorkerManager()
        app = QApplication.instance() or QApplication([])
        assert isinstance(app, QApplication)
        request = StartupLaunchRequest(mode="refresh", target="D:/Samples", roots=("D:/Samples",))

        with mock.patch("gui.main.launcher.StartupScanMonitor", side_effect=RuntimeError("monitor failed")), \
            mock.patch("gui.main.launcher.write_launcher_crash_log") as write_log, \
            mock.patch("gui.main.launcher._show_window") as show_window:
            launcher._launch_refresh(window, request, app)

        write_log.assert_called()
        show_window.assert_called_once_with(window)

    def test_refresh_launch_can_hide_scan_monitor_to_tray(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        from gui.main import launcher
        from gui.widgets.startup_launcher import StartupLaunchRequest

        class _WorkerManager(QObject):
            progress = Signal(dict)
            error = Signal(str)

            def request_cancel(self):
                events.append("cancel")

        events = []
        monitors = []

        class _FakeMonitor:
            def __init__(self):
                events.append("monitor-created")
                self.minimize_handler = None
                monitors.append(self)

            def set_status(self, _payload):
                events.append("monitor-status")

            def show_near_center(self):
                events.append("monitor-show")

            def set_background_minimize_handler(self, handler):
                self.minimize_handler = handler
                events.append("monitor-minimize-handler")

            def close(self):
                events.append("monitor-close")

        class _FakeTray:
            def __init__(self, monitor, *, cancel_callback=None, quit_callback=None, parent=None):
                self.monitor = monitor
                self.cancel_callback = cancel_callback
                events.append("tray-created")

            def start(self):
                events.append("tray-start")
                self.monitor.set_background_minimize_handler(self.hide_monitor)

            def is_available(self):
                return True

            def hide_monitor(self):
                events.append("tray-hide")

            def update_status(self, _payload):
                events.append("tray-status")

            def finish(self):
                events.append("tray-finish")

        captured_options = {}

        def _start_scan(_roots, **kwargs):
            captured_options.update(kwargs["finalize_options"])
            return True

        window = mock.Mock()
        window.worker_manager = _WorkerManager()
        window.workflow_controller.start_scan.side_effect = _start_scan
        app = QApplication.instance() or QApplication([])
        assert isinstance(app, QApplication)
        request = StartupLaunchRequest(mode="refresh", target="D:/Samples", roots=("D:/Samples",))

        with (
            mock.patch("gui.main.launcher.StartupScanMonitor", _FakeMonitor),
            mock.patch("gui.main.launcher.StartupTrayController", _FakeTray),
            mock.patch("gui.main.launcher.StartupSplash", mock.Mock()),
            mock.patch("gui.main.launcher.QTimer.singleShot", side_effect=lambda _delay, func: func()),
            mock.patch("gui.main.launcher._show_window", side_effect=lambda _window, _splash=None: events.append("show-window")),
        ):
            launcher._launch_refresh(window, request, app)
            assert monitors and monitors[0].minimize_handler is not None
            monitors[0].minimize_handler()
            window.worker_manager.progress.emit({"message": "Scanning 12 files"})
            captured_options["on_background_work_start"]()

        self.assertIn("tray-created", events)
        self.assertIn("tray-start", events)
        self.assertIn("tray-hide", events)
        self.assertIn("tray-status", events)
        self.assertIn("tray-finish", events)

    def test_startup_scan_monitor_cancel_button_calls_handler(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        from gui.widgets.startup_scan_monitor import StartupScanMonitor
        from gui.utils.styles import ColorPalette

        app = QApplication.instance() or QApplication([])
        assert isinstance(app, QApplication)
        monitor = StartupScanMonitor()
        calls = []
        monitor.set_cancel_handler(lambda: calls.append("cancel"))

        monitor.btn_cancel.click()

        self.assertEqual(calls, ["cancel"])
        self.assertFalse(monitor.btn_cancel.isEnabled())
        self.assertEqual(monitor.btn_cancel.text(), "Stopping")
        self.assertEqual(monitor.status_label.text(), "Canceling scan...")
        self.assertEqual(monitor.btn_cancel.objectName(), "danger")
        self.assertIn(f"QPushButton#danger {{\n                background: {ColorPalette.DANGER};", monitor.styleSheet())

    def test_startup_scan_monitor_buttons_are_below_progress_bar(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        from gui.widgets.startup_scan_monitor import StartupScanMonitor

        app = QApplication.instance() or QApplication([])
        assert isinstance(app, QApplication)
        monitor = StartupScanMonitor()

        root_layout = monitor.layout()

        self.assertIs(root_layout.itemAt(2).widget(), monitor.progress)
        self.assertIs(root_layout.itemAt(3).layout(), monitor.button_row)
        self.assertIs(monitor.button_row.itemAt(1).widget(), monitor.btn_cancel)

    def test_startup_launcher_title_stays_plain_after_summary_refresh(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        from gui.widgets.startup_launcher import StartupLauncherDialog

        app = QApplication.instance() or QApplication([])
        assert isinstance(app, QApplication)

        class _Settings:
            def value(self, _key, default=""):
                return default

        class _SettingsController:
            settings = _Settings()

            def get_library_view_modes(self):
                return ["table", "tree", "map"]

        dialog = StartupLauncherDialog(_SettingsController())
        dialog._set_roots([str(Path("C:/Samples"))])

        self.assertEqual(dialog.windowTitle(), "Unshuffle Launcher")

    def test_startup_relaunch_reset_restores_normal_window_bounds(self):
        from gui.main import launcher
        from gui.utils.constants import MAIN_WINDOW_HEIGHT, MAIN_WINDOW_WIDTH

        class _FakeWindow:
            def __init__(self):
                self._flags = Qt.Window | Qt.WindowStaysOnTopHint
                self._width = 520
                self._height = 720
                self.maximum_width = 320
                self.minimum_width = 0
                self.hidden = False

            def windowFlags(self):
                return self._flags

            def setWindowFlags(self, flags):
                self._flags = flags

            def setMaximumWidth(self, width):
                self.maximum_width = width

            def setMinimumWidth(self, width):
                self.minimum_width = width

            def width(self):
                return self._width

            def height(self):
                return self._height

            def resize(self, width, height):
                self._width = width
                self._height = height

            def hide(self):
                self.hidden = True

        window = _FakeWindow()

        launcher._reset_window_for_startup_relaunch(window)

        self.assertFalse(bool(window.windowFlags() & Qt.WindowStaysOnTopHint))
        self.assertGreater(window.maximum_width, MAIN_WINDOW_WIDTH)
        self.assertEqual(window.minimum_width, MAIN_WINDOW_WIDTH)
        self.assertEqual(window.width(), MAIN_WINDOW_WIDTH)
        self.assertEqual(window.height(), MAIN_WINDOW_HEIGHT)
        self.assertTrue(window.hidden)

    def test_refresh_launch_cancel_reopens_startup_launcher(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication, QDialog
        from gui.main import launcher
        from gui.widgets.startup_launcher import StartupLaunchRequest

        class _WorkerManager(QObject):
            progress = Signal(dict)
            error = Signal(str)

            def request_cancel(self):
                events.append("cancel-requested")
                return True

        events = []
        monitors = []
        start_roots = []

        class _WorkflowController(QObject):
            scanFinished = Signal(dict)

            def start_scan(self, _roots, **kwargs):
                events.append("start-scan")
                start_roots.append(tuple(_roots))
                return True

        class _FakeMonitor:
            def __init__(self):
                self.cancel_handler = None
                monitors.append(self)
                events.append("monitor-created")

            def set_status(self, _payload):
                events.append("monitor-status")

            def show_near_center(self):
                events.append("monitor-show")

            def set_cancel_handler(self, handler):
                self.cancel_handler = handler
                events.append("monitor-cancel-handler")

            def close(self):
                events.append("monitor-close")

        class _FakeTray:
            def __init__(self, *_args, **_kwargs):
                events.append("tray-created")

            def start(self):
                events.append("tray-start")

            def update_status(self, _payload):
                pass

            def finish(self):
                events.append("tray-finish")

        class _FakeLauncherDialog:
            def __init__(self, _settings_controller, *, force_refresh=False):
                self.force_refresh = force_refresh
                events.append("launcher-created")

            def show(self):
                pass

            def raise_(self):
                pass

            def activateWindow(self):
                pass

            def exec(self):
                events.append("launcher-exec")
                return QDialog.DialogCode.Accepted

            def launch_request(self):
                return StartupLaunchRequest(
                    mode="refresh" if self.force_refresh else "restore",
                    target="D:/Samples",
                    session_id="" if self.force_refresh else "old-session",
                    roots=("D:/Samples",),
                )

        app = QApplication.instance() or QApplication([])
        assert isinstance(app, QApplication)
        workflow = _WorkflowController()
        window = SimpleNamespace(
            worker_manager=_WorkerManager(),
            workflow_controller=workflow,
            settings_controller=mock.Mock(),
            _startup_launch_refs={},
        )
        request = StartupLaunchRequest(mode="refresh", target="D:/Samples", roots=("D:/Samples",))

        with (
            mock.patch("gui.main.launcher.StartupScanMonitor", _FakeMonitor),
            mock.patch("gui.main.launcher.StartupTrayController", _FakeTray),
            mock.patch("gui.main.launcher.StartupLauncherDialog", _FakeLauncherDialog),
            mock.patch("gui.main.launcher.QTimer.singleShot", side_effect=lambda _delay, func: func()),
            mock.patch("gui.main.launcher._show_window", side_effect=lambda _window, _splash=None: events.append("show-window")),
        ):
            launcher._launch_refresh(window, request, app)
            assert monitors and monitors[0].cancel_handler is not None
            monitors[0].cancel_handler()
            workflow.scanFinished.emit({"cancelled": True})

        self.assertIn("cancel-requested", events)
        self.assertIn("launcher-created", events)
        self.assertIn("launcher-exec", events)
        self.assertEqual(events.count("start-scan"), 2)
        self.assertEqual(start_roots[-1], ("D:/Samples",))
        self.assertNotIn("show-window", events)

    def test_show_window_keeps_last_window_quit_disabled_after_main_is_visible(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        from gui.main import launcher

        app = QApplication.instance() or QApplication([])
        app.setQuitOnLastWindowClosed(False)
        window = mock.Mock()

        with mock.patch("gui.main.launcher.QTimer.singleShot", side_effect=lambda _delay, func: None):
            launcher._show_window(window, None)

        self.assertFalse(app.quitOnLastWindowClosed())

    def test_main_window_close_explicitly_quits_app(self):
        from PySide6.QtWidgets import QApplication
        from gui.main import window as window_module

        app = QApplication.instance() or QApplication([])
        main_window = window_module.ModernApp.__new__(window_module.ModernApp)
        main_window._is_closing = False
        main_window.drafting_controller = mock.Mock()
        main_window.drafting_controller.has_changes.return_value = False
        main_window.settings_controller = mock.Mock()
        main_window.engine = None

        event = mock.Mock()
        with mock.patch.object(window_module.QMainWindow, "closeEvent") as close_event, \
            mock.patch("gui.main.window.QTimer.singleShot", side_effect=lambda _delay, func: func()) as single_shot, \
            mock.patch.object(app, "quit") as quit_app:
            window_module.ModernApp.closeEvent(main_window, event)

        close_event.assert_called_once_with(event)
        single_shot.assert_called()
        quit_app.assert_called_once_with()

    def test_promoting_anchor_confirms_and_explains_removed_candidate(self):
        from gui.core.system_controller import SystemController
        from PySide6.QtWidgets import QMessageBox

        page = mock.Mock()
        app = mock.Mock()
        app.data_manager.bridge = mock.Mock()
        app.engine.session_id = "session-1"
        app.engine.db.list_anchor_candidates.return_value = []
        app.engine.db.get_staging_records.return_value = []
        app.engine.db.list_coherence_results.return_value = []
        app.engine.db.repair_anchor_profile_json.return_value = []

        controller = SystemController(app, page)

        with mock.patch(
            "gui.core.system_controller.QMessageBox.question",
            return_value=QMessageBox.Yes,
        ) as question:
            controller.promote_anchors(["anchor-1", "anchor-2"])
            controller.save_anchor_candidate_draft()

        self.assertEqual(question.call_count, 2)
        app.engine.db.set_anchor_candidate_state.assert_called_once_with(
            "session-1",
            ["anchor-1", "anchor-2"],
            "verified",
        )
        page.set_anchor_candidates.assert_called_with([])
        message = page.set_anchor_status.call_args.args[0]
        self.assertIn("Saved 2 anchor candidate draft actions", message)
        self.assertGreaterEqual(app.set_search_status.call_count, 1)

    def test_anchor_promotion_status_counts_all_staged_promotions(self):
        from gui.core.system_controller import SystemController
        from PySide6.QtWidgets import QMessageBox

        page = mock.Mock()
        app = mock.Mock()
        app.data_manager.bridge = mock.Mock()
        app.engine.session_id = "session-1"
        app.engine.db.list_anchor_candidates.return_value = []
        app.engine.db.get_staging_records.return_value = []
        app.engine.db.list_coherence_results.return_value = []

        controller = SystemController(app, page)

        with mock.patch(
            "gui.core.system_controller.QMessageBox.question",
            return_value=QMessageBox.Yes,
        ):
            controller.promote_anchors(["anchor-1"])
            first_message = page.set_anchor_status.call_args.args[0]
            controller.promote_anchors(["anchor-2"])
            second_message = page.set_anchor_status.call_args.args[0]

        self.assertIn("Staged 1 anchor promotion", first_message)
        self.assertIn("Staged 2 anchor promotions across 2 draft actions", second_message)
        self.assertIn("Save and Apply", second_message)

    def test_anchor_action_draft_can_be_changed_before_save(self):
        from gui.core.system_controller import SystemController
        from PySide6.QtWidgets import QMessageBox

        page = mock.Mock()
        app = mock.Mock()
        app.data_manager.bridge = mock.Mock()
        app.engine.session_id = "session-1"
        app.engine.db.list_anchor_candidates.return_value = []
        app.engine.db.get_staging_records.return_value = []
        app.engine.db.list_coherence_results.return_value = []

        controller = SystemController(app, page)

        with mock.patch(
            "gui.core.system_controller.QMessageBox.question",
            return_value=QMessageBox.Yes,
        ):
            controller.promote_anchors(["anchor-1"])
            controller.update_anchor_candidate_action("anchor-1", "ignore")
            controller.save_anchor_candidate_draft()

        app.engine.db.set_anchor_candidate_state.assert_called_once_with(
            "session-1",
            ["anchor-1"],
            "ignored",
        )

    def test_anchor_draft_is_preserved_when_save_callback_fails(self):
        from gui.core.system_controller import SystemController
        from PySide6.QtWidgets import QMessageBox

        page = mock.Mock()
        app = mock.Mock()
        app.data_manager.bridge = mock.Mock()
        controller = SystemController(app, page)

        def fail():
            raise RuntimeError("write failed")

        controller._queue_anchor_draft_action("Failing anchor write", fail, kind="promotion", count=1)

        with mock.patch(
            "gui.core.system_controller.QMessageBox.question",
            return_value=QMessageBox.Yes,
        ):
            controller.save_anchor_candidate_draft()

        self.assertEqual(controller._anchor_draft_count(), 1)
        page.set_anchor_draft_state.assert_called_with(1)
        page.set_anchor_status.assert_called_with("Could not save anchor candidate draft: write failed", "error")
        app.set_search_status.assert_called_with("Taxonomy: anchor candidate draft was not saved.")

    def test_anchor_sound_group_update_can_be_cleared_before_save(self):
        from types import SimpleNamespace

        from gui.core.system_controller import SystemController
        from unshuffle.logic.coherence.anchor_profiles import build_anchor_payload

        payload = build_anchor_payload(
            cluster_id="cluster-1",
            audio_type="Oneshots",
            category="Bass",
            subcategory="Sub",
            medoid_vector=[0.1] * FEATURE_VECTOR_SIZE,
            cluster_centroid=[0.1] * FEATURE_VECTOR_SIZE,
            cluster_std=[0.01] * FEATURE_VECTOR_SIZE,
            coherence_radius=0.4,
            n_reference_items=8,
        )
        page = mock.Mock()
        app = mock.Mock()
        app.engine = SimpleNamespace(session_id="session-1", db=mock.Mock())
        app.engine.db.list_anchor_candidates.return_value = [
            {"anchor_id": payload["anchor_id"], "state": "candidate", "profile_json": json.dumps(payload)}
        ]
        app.engine.db.get_staging_records.return_value = []
        app.engine.db.list_coherence_results.return_value = []
        controller = SystemController(app, page)

        controller.update_anchor_sound_group(payload["anchor_id"], "Loops", "Full Drums", "Breaks")
        controller.update_anchor_candidate_action(payload["anchor_id"], "")

        page.set_anchor_draft_state.assert_called_with(0)
        app.engine.db.upsert_anchor_profiles.assert_not_called()

    def test_anchor_mixed_draft_status_keeps_promotion_context(self):
        from gui.core.system_controller import SystemController

        page = mock.Mock()
        app = mock.Mock()
        app.data_manager.bridge = mock.Mock()

        controller = SystemController(app, page)
        controller._queue_anchor_draft_action("Promote 1 anchor", mock.Mock(), kind="promotion", count=1)
        controller._queue_anchor_draft_action("Update anchor group", mock.Mock(), kind="update", count=1)

        message = page.set_anchor_status.call_args.args[0]
        self.assertIn("1 anchor promotion", message)
        self.assertIn("1 sound group edit", message)
        self.assertIn("across 2 draft actions", message)
        self.assertNotIn("anchor draft action(s) pending", message)

    def test_export_anchors_exports_verified_profiles_only(self):
        from gui.core.system_controller import SystemController
        from unshuffle.logic.coherence.anchor_profiles import build_anchor_payload

        payload = build_anchor_payload(
            cluster_id="oneshots_bass_sub_000",
            audio_type="Oneshots",
            category="Bass",
            subcategory="Sub",
            medoid_vector=[0.1] * FEATURE_VECTOR_SIZE,
            cluster_centroid=[0.1] * FEATURE_VECTOR_SIZE,
            cluster_std=[0.01] * FEATURE_VECTOR_SIZE,
            coherence_radius=0.2,
            n_reference_items=8,
        )
        page = mock.Mock()
        app = mock.Mock()
        app.data_manager.bridge = mock.Mock()
        app.engine.session_id = "session-1"
        app.engine.target_dir = ""
        app.engine.db.list_anchor_candidates.return_value = [
            {"anchor_id": payload["anchor_id"], "profile_json": json.dumps(payload)}
        ]

        controller = SystemController(app, page)
        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = str(Path(tmpdir) / "anchors.json")
            with mock.patch("gui.core.system_controller.QFileDialog.getSaveFileName", return_value=(out_path, "")):
                controller.export_anchors([])

            exported = json.loads(Path(out_path).read_text(encoding="utf-8"))

        app.engine.db.list_anchor_candidates.assert_called_once_with("session-1", state="verified")
        self.assertEqual(exported, [payload])
        app.set_search_status.assert_called_with("Taxonomy: exported 1 verified anchor profile(s).")

    def test_remove_verified_anchors_uses_durable_removal(self):
        from gui.core.system_controller import SystemController
        from PySide6.QtWidgets import QMessageBox

        page = mock.Mock()
        app = mock.Mock()
        app.data_manager.bridge = mock.Mock()
        app.engine.session_id = "session-1"
        app.engine.db.list_anchor_candidates.return_value = []
        app.engine.db.get_staging_records.return_value = []
        app.engine.db.list_coherence_results.return_value = []

        controller = SystemController(app, page)
        with mock.patch("gui.core.system_controller.QMessageBox.question", return_value=QMessageBox.Yes):
            controller.remove_verified_anchors(["anchor-1"])

        app.engine.db.remove_verified_anchor_profiles.assert_called_once_with("session-1", ["anchor-1"])
        app.engine.db.set_anchor_candidate_state.assert_not_called()
        app.set_search_status.assert_called()

    def test_import_anchors_inserts_valid_profiles_as_verified(self):
        from gui.core.system_controller import SystemController
        from PySide6.QtWidgets import QMessageBox
        from unshuffle.logic.coherence.anchor_profiles import build_anchor_payload

        payload = build_anchor_payload(
            cluster_id="oneshots_kicks_punchy_000",
            audio_type="Oneshots",
            category="Kicks",
            subcategory="Punchy",
            medoid_vector=[0.2] * FEATURE_VECTOR_SIZE,
            cluster_centroid=[0.2] * FEATURE_VECTOR_SIZE,
            cluster_std=[0.01] * FEATURE_VECTOR_SIZE,
            coherence_radius=0.3,
            n_reference_items=9,
        )
        page = mock.Mock()
        app = mock.Mock()
        app.data_manager.bridge = mock.Mock()
        app.engine.session_id = "session-1"
        app.engine.target_dir = ""
        app.engine.db.list_anchor_candidates.return_value = []
        app.engine.db.get_staging_records.return_value = []
        app.engine.db.list_coherence_results.return_value = []

        controller = SystemController(app, page)
        with tempfile.TemporaryDirectory() as tmpdir:
            in_path = Path(tmpdir) / "anchors.json"
            in_path.write_text(json.dumps([payload]), encoding="utf-8")
            with mock.patch("gui.core.system_controller.QFileDialog.getOpenFileName", return_value=(str(in_path), "")):
                with mock.patch("gui.core.system_controller.QMessageBox.warning", return_value=QMessageBox.Yes):
                    controller.import_anchors()

        args = app.engine.db.upsert_anchor_profiles.call_args.args
        self.assertEqual(args[0], "session-1")
        imported = args[1][0]
        self.assertEqual(imported.anchor_id, payload["anchor_id"])
        self.assertEqual(imported.category, "Kicks")
        self.assertEqual(imported.state, "verified")
        page.set_my_anchors.assert_called_once()
        app.set_search_status.assert_called_with("Taxonomy: imported 1 verified anchor profile(s). Run coherence to use them.")

    def test_anchor_sound_group_edit_updates_profile_payload(self):
        from types import SimpleNamespace

        from gui.core.system_controller import SystemController
        from unshuffle.logic.coherence.anchor_profiles import build_anchor_payload

        payload = build_anchor_payload(
            cluster_id="cluster-1",
            audio_type="Oneshots",
            category="Bass",
            subcategory="Sub",
            medoid_vector=[0.1] * FEATURE_VECTOR_SIZE,
            cluster_centroid=[0.1] * FEATURE_VECTOR_SIZE,
            cluster_std=[0.01] * FEATURE_VECTOR_SIZE,
            coherence_radius=0.4,
            n_reference_items=8,
        )
        page = mock.Mock()
        app = mock.Mock()
        app.engine = SimpleNamespace(session_id="session-1", db=mock.Mock())
        app.engine.db.list_anchor_candidates.side_effect = [
            [{"anchor_id": payload["anchor_id"], "state": "candidate", "profile_json": json.dumps(payload)}],
            [],
            [],
            [],
            [],
        ]
        app.engine.db.get_staging_records.return_value = []
        app.engine.db.list_coherence_results.return_value = []
        controller = SystemController(app, page)

        controller.update_anchor_sound_group(payload["anchor_id"], "Loops", "Full Drums", "Breaks")

        page.set_anchor_draft_state.assert_called_with(1)
        page.set_anchor_candidate_actions.assert_called_with({payload["anchor_id"]: "update"})
        from PySide6.QtWidgets import QMessageBox
        with mock.patch("gui.core.system_controller.QMessageBox.question", return_value=QMessageBox.Yes):
            controller.save_anchor_candidate_draft(False)
        args = app.engine.db.upsert_anchor_profiles.call_args.args
        updated = args[1][0]
        self.assertEqual(args[0], "session-1")
        self.assertEqual(updated.state, "candidate")
        self.assertEqual(updated.audio_type, "Loops")
        self.assertEqual(updated.category, "Full Drums")
        self.assertEqual(updated.subcategory, "Breaks")
        self.assertEqual(updated.profile_payload["category"], "Full Drums")
        page.set_anchor_candidates.assert_called()
        app.set_search_status.assert_called_with("Taxonomy: saved 1 anchor candidate draft action.")

    def test_anchor_candidates_show_examples_and_emit_preview_path(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication
        from gui.widgets.system_page import ANCHOR_COHESION_RATIO_ROLE, ANCHOR_ID_ROLE, SystemPage

        app = QApplication.instance() or QApplication([])
        page = SystemPage()
        emitted = []
        page.previewAnchorRequested.connect(emitted.append)
        try:
            page.set_anchor_candidates(
                [
                    {
                        "anchor_id": "anchor-1",
                        "audio_type": "Oneshots",
                        "category": "Bass",
                        "subcategory": "Sub",
                        "n_reference_items": 12,
                        "coherence_radius": 0.45,
                        "consistency_ratio": 0.45,
                        "consistency_text": "Strong",
                        "density_text": "Dense 1.40x",
                        "density_ratio": 1.4,
                        "state": "candidate",
                        "preview_path": "D:/Samples/bass-one.wav",
                        "examples": [
                            {"name": "bass-one.wav", "path": "D:/Samples/bass-one.wav"},
                            {"name": "bass-two.wav", "path": "D:/Samples/bass-two.wav"},
                        ],
                    }
                ]
            )

            self.assertEqual(page.anchors_table.horizontalHeaderItem(0).text(), "Sound group")
            self.assertEqual(page.anchors_table.horizontalHeaderItem(1).text(), "Examples")
            self.assertEqual(page.anchors_table.horizontalHeaderItem(2).text(), "Cohesion")
            self.assertEqual(page.anchors_table.horizontalHeaderItem(3).text(), "Action")
            self.assertEqual(page.anchors_table.columnCount(), 4)
            self.assertFalse(page.btn_discard_anchor_draft.isEnabled())
            self.assertFalse(page.btn_save_anchor_draft.isEnabled())
            self.assertFalse(page.btn_apply_anchor_draft.isEnabled())
            page.set_mode(True)
            page.set_anchor_draft_state(1)
            self.assertTrue(page.btn_discard_anchor_draft.isEnabled())
            self.assertTrue(page.btn_save_anchor_draft.isEnabled())
            self.assertTrue(page.btn_apply_anchor_draft.isEnabled())
            self.assertEqual(page.anchors_table.item(0, 0).text(), "1s/Bass/Sub")
            self.assertIn("bass-one.wav", page.anchors_table.item(0, 1).text())
            
            # Check UserRole values populated for SystemTableDelegate pill drawing
            self.assertEqual(page.anchors_table.item(0, 0).data(Qt.UserRole), "Bass")
            self.assertEqual(page.anchors_table.item(0, 0).data(Qt.UserRole + 1), "Oneshots")
            self.assertEqual(page.anchors_table.item(0, 0).data(Qt.UserRole + 2), "Sub")
            self.assertEqual(page.anchors_table.item(0, 0).data(ANCHOR_ID_ROLE), "anchor-1")
            self.assertFalse(page.anchors_table.item(0, 0).flags() & Qt.ItemIsEditable)
            self.assertFalse(page.anchors_table.item(0, 1).flags() & Qt.ItemIsEditable)
            self.assertIsNotNone(page.anchors_table.cellWidget(0, 0))

            from gui.widgets.system_page import SystemTableDelegate
            self.assertIsInstance(page.anchors_table.itemDelegate(), SystemTableDelegate)

            self.assertEqual(page.anchors_table.item(0, 2).text(), "")
            self.assertEqual(page.anchors_table.item(0, 2).data(ANCHOR_COHESION_RATIO_ROLE), 0.45)
            self.assertIsNotNone(page.anchors_table.cellWidget(0, 3))
            self.assertEqual(page.selected_anchors(), [])

            page.anchors_table.selectRow(0)
            self.assertEqual(page.selected_anchors(), ["anchor-1"])

            page._preview_anchor_row(page.anchors_table, 0)
            page._preview_anchor_row(page.anchors_table, 0)
            self.assertEqual(emitted, ["D:/Samples/bass-one.wav", "D:/Samples/bass-two.wav"])
        finally:
            page.deleteLater()

    def test_anchor_sound_group_edits_emit_draft_change(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        from gui.widgets.system_page import SystemPage

        app = QApplication.instance() or QApplication([])
        page = SystemPage()
        emitted = []
        page.anchorSoundGroupChanged.connect(lambda *args: emitted.append(args))
        try:
            page.set_anchor_candidates(
                [
                    {
                        "anchor_id": "anchor-1",
                        "audio_type": "Oneshots",
                        "category": "Bass",
                        "subcategory": "Sub",
                        "n_reference_items": 12,
                    }
                ]
            )

            combo = page.anchors_table.cellWidget(0, 0)
            combo.set_value("Full Drums", "Loops", "Breaks")

            self.assertEqual(page.anchors_table.item(0, 0).text(), "Lp/Full Drums/Breaks")
            self.assertEqual(emitted, [("anchor-1", "Loops", "Full Drums", "Breaks")])
        finally:
            page.deleteLater()

    def test_anchor_candidate_action_column_emits_editable_action(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        from gui.widgets.system_page import SystemPage

        app = QApplication.instance() or QApplication([])
        page = SystemPage()
        emitted = []
        page.anchorCandidateActionChanged.connect(lambda *args: emitted.append(args))
        try:
            page.set_anchor_candidates(
                [
                    {
                        "anchor_id": "anchor-1",
                        "audio_type": "Oneshots",
                        "category": "Bass",
                        "subcategory": "Sub",
                        "n_reference_items": 12,
                    }
                ]
            )
            page.set_anchor_candidate_actions({"anchor-1": "promotion"})
            combo = page.anchors_table.cellWidget(0, 3)
            from PySide6.QtWidgets import QComboBox
            if not isinstance(combo, QComboBox) and combo is not None:
                combo = combo.findChild(QComboBox)
            self.assertEqual(combo.currentText(), "Promote")

            page.set_anchor_candidate_actions({"anchor-1": "update"})
            self.assertEqual(combo.currentText(), "Update")

            combo.setCurrentText("Ignore")
            combo.setCurrentIndex(0)
            self.assertEqual(combo.currentText(), "None")

            self.assertEqual(emitted, [("anchor-1", "ignore"), ("anchor-1", "")])
        finally:
            page.deleteLater()

    def test_taxonomy_pill_layout_reserves_space_for_subcategory(self):
        from gui.widgets.refinement_taxonomy import _pill_widths_for_available

        class _Metrics:
            def horizontalAdvance(self, text):
                return len(str(text)) * 7

        widths = _pill_widths_for_available(_Metrics(), ["O", "Percussion", "Top"], 116)

        self.assertEqual(len(widths), 3)
        self.assertGreaterEqual(widths[2], 22)

    def test_anchor_candidates_context_menu_promotes_or_ignores_selected_rows(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtCore import QPoint
        from PySide6.QtWidgets import QApplication
        from gui.widgets.system_page import SystemPage

        app = QApplication.instance() or QApplication([])
        page = SystemPage()
        promoted = []
        ignored = []
        page.promoteAnchorsRequested.connect(promoted.append)
        page.ignoreAnchorsRequested.connect(ignored.append)
        try:
            page.set_anchor_candidates(
                [
                    {"anchor_id": "anchor-1", "category": "Kicks", "n_reference_items": 6},
                    {"anchor_id": "anchor-2", "category": "Snares", "n_reference_items": 7},
                ]
            )
            actions = []
            exec_positions = []

            class _Menu:
                def __init__(self, _parent=None):
                    pass

                def setStyleSheet(self, _style):
                    pass

                def addAction(self, action):
                    actions.append(action)

                def exec(self, pos):
                    exec_positions.append(pos)

            with mock.patch("gui.widgets.system_page.QMenu", _Menu):
                page._show_anchor_candidates_menu(QPoint(2, 2))

            self.assertEqual(page.selected_anchors(), ["anchor-2"])
            self.assertEqual([action.text() for action in actions], ["Promote", "Ignore"])
            self.assertEqual(exec_positions, [page.anchors_table.viewport().mapToGlobal(QPoint(2, 2))])
            actions[0].trigger()
            actions[1].trigger()
            self.assertEqual(promoted, [["anchor-2"]])
            self.assertEqual(ignored, [["anchor-2"]])
        finally:
            page.deleteLater()

    def test_discard_anchor_candidate_draft_clears_local_actions(self):
        from types import SimpleNamespace

        from PySide6.QtWidgets import QMessageBox
        from gui.core.system_controller import SystemController

        page = mock.Mock()
        app = mock.Mock()
        app.engine = SimpleNamespace(session_id="session-1", db=mock.Mock())
        app.engine.db.list_anchor_candidates.return_value = []
        app.engine.db.get_staging_records.return_value = []
        app.engine.db.list_coherence_results.return_value = []
        controller = SystemController(app, page)
        controller._queue_anchor_draft_action("Ignore 1 anchor", mock.Mock())

        with mock.patch("gui.core.system_controller.QMessageBox.question", return_value=QMessageBox.Yes):
            controller.discard_anchor_candidate_draft()

        page.set_anchor_draft_state.assert_called_with(0)
        page.set_anchor_status.assert_called_with("Anchor candidate draft discarded.", "info")
        app.set_search_status.assert_called_with("Taxonomy: discarded anchor candidate draft.")

    def test_anchor_candidates_hide_total_samples_column(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication
        from gui.widgets.system_page import ANCHOR_ID_ROLE, SystemPage

        app = QApplication.instance() or QApplication([])
        page = SystemPage()
        try:
            page.set_anchor_candidates(
                [
                    {
                        "anchor_id": "dense",
                        "audio_type": "Loops",
                        "category": "Bass",
                        "n_reference_items": 10,
                        "consistency_ratio": 0.5,
                        "density_text": "Dense 2.00x",
                        "density_ratio": 2.0,
                    },
                    {
                        "anchor_id": "many",
                        "audio_type": "Oneshots",
                        "category": "Claps",
                        "n_reference_items": 101,
                        "consistency_ratio": 2.0,
                        "density_text": "Sparse 0.50x",
                        "density_ratio": 0.5,
                    },
                ]
            )

            headers = [page.anchors_table.horizontalHeaderItem(col).text() for col in range(page.anchors_table.columnCount())]
            self.assertEqual(headers, ["Sound group", "Examples", "Cohesion", "Action"])
            page.anchors_table.sortItems(2, Qt.AscendingOrder)
            self.assertEqual(page.anchors_table.item(0, 0).data(ANCHOR_ID_ROLE), "dense")
            page.anchors_table.sortItems(2, Qt.DescendingOrder)
            self.assertEqual(page.anchors_table.item(0, 0).data(ANCHOR_ID_ROLE), "many")
        finally:
            page.deleteLater()

    def test_anchor_consistency_uses_category_radius_baseline(self):
        from gui.core.system_controller import SystemController

        controller = SystemController(mock.Mock(), mock.Mock())
        rows = controller._add_anchor_consistency(
            [
                {"anchor_id": "tight", "audio_type": "Oneshots", "category": "Bass", "coherence_radius": 0.5, "n_reference_items": 21},
                {"anchor_id": "typical", "audio_type": "Oneshots", "category": "Bass", "coherence_radius": 1.0, "n_reference_items": 21},
                {"anchor_id": "loose", "audio_type": "Oneshots", "category": "Bass", "coherence_radius": 2.0, "n_reference_items": 101},
                {"anchor_id": "loop", "audio_type": "Loops", "category": "Bass", "coherence_radius": 10.0, "n_reference_items": 31},
                {"anchor_id": "loop2", "audio_type": "Loops", "category": "Bass", "coherence_radius": 20.0, "n_reference_items": 31},
            ]
        )

        by_id = {row["anchor_id"]: row for row in rows}
        self.assertEqual(by_id["tight"]["consistency_text"], "Strong")
        self.assertEqual(by_id["typical"]["consistency_text"], "Medium")
        self.assertEqual(by_id["loose"]["consistency_text"], "Too broad")
        self.assertEqual(by_id["loose"]["anchor_quality"], "too_broad")
        self.assertEqual(by_id["loose"]["consistency_baseline_scope"], "type/category")
        self.assertEqual(by_id["loop"]["consistency_text"], "Strong")
        self.assertEqual(by_id["typical"]["density_text"], "Sparse 0.50x")
        self.assertEqual(by_id["loose"]["density_text"], "Dense 1.25x")

    def test_anchor_candidate_examples_are_medoid_nearest(self):
        from gui.core.system_controller import SystemController

        medoid = [0.0] * FEATURE_VECTOR_SIZE
        near = [0.05] * FEATURE_VECTOR_SIZE
        far = [1.0] * FEATURE_VECTOR_SIZE

        class _DB:
            def get_staging_records(self, _session_id):
                return [
                    {
                        "row_id": 1,
                        "sample_name": "z_far.wav",
                        "source_path": "D:/Samples/z_far.wav",
                        "feature_vector": feature_blob_from_vector(far),
                    },
                    {
                        "row_id": 2,
                        "sample_name": "a_near.wav",
                        "source_path": "D:/Samples/a_near.wav",
                        "feature_vector": feature_blob_from_vector(near),
                    },
                ]

            def list_coherence_results(self, _session_id):
                return [
                    {"record_id": 1, "cluster_id": "cluster-1"},
                    {"record_id": 2, "cluster_id": "cluster-1"},
                ]

        engine = SimpleNamespace(db=_DB(), session_id="session-1")
        controller = SystemController(mock.Mock(), mock.Mock())
        rows = controller._enrich_anchor_candidate_rows(
            engine,
            [
                {
                    "anchor_id": "anchor-1",
                    "cluster_id": "cluster-1",
                    "medoid_vector": feature_blob_from_vector(medoid),
                }
            ],
        )

        self.assertEqual([example["name"] for example in rows[0]["examples"]], ["a_near.wav", "z_far.wav"])
        self.assertEqual(rows[0]["preview_path"], "D:/Samples/a_near.wav")

    def test_my_anchors_section_lists_verified_anchor_rows(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        from gui.widgets.system_page import SystemPage

        app = QApplication.instance() or QApplication([])
        page = SystemPage()
        try:
            page.set_my_anchors(
                [
                    {
                        "anchor_id": "verified-1",
                        "audio_type": "Oneshots",
                        "category": "Claps",
                        "subcategory": "Snaps",
                        "n_reference_items": 18,
                        "coherence_radius": 1.077,
                        "state": "verified",
                        "examples": [{"name": "snap.wav", "path": "D:/Samples/snap.wav"}],
                        "preview_path": "D:/Samples/snap.wav",
                    }
                ]
            )

            page._set_section("my_anchors")
            self.assertIs(page.stack.currentWidget(), page.my_anchors_panel)
            self.assertEqual(page.my_anchors_table.item(0, 0).text(), "1s/Claps/Snaps")
            self.assertEqual(page.my_anchors_table.columnCount(), 3)

            page.my_anchors_table.selectRow(0)
            self.assertEqual(page.selected_my_anchors(), ["verified-1"])
        finally:
            page.deleteLater()

    def test_page_navigation_buttons_follow_workspace_history(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication, QWidget
        from gui.main.launcher import ModernApp

        app = QApplication.instance() or QApplication([])
        window = ModernApp()
        try:
            window._page_history = []
            window._page_history_index = -1
            window.open_library_workspace()
            self.assertEqual(window.btn_previous_page.text(), "Previous")
            self.assertEqual(window.btn_next_page.text(), "Next")
            self.assertEqual(
                window.page_carousel.options,
                [
                    ("Library", "library"),
                    ("Build", "build"),
                    ("System", "system"),
                    ("History", "history"),
                ],
            )
            self.assertFalse(window.btn_previous_page.isEnabled())

            window.open_system_workspace("additions")
            panel = QWidget()
            window.system_page.set_tree_organization_panel(panel)
            window.open_history_workspace()

            self.assertTrue(window.btn_previous_page.isEnabled())
            self.assertFalse(window.btn_next_page.isEnabled())

            window.go_to_previous_page()
            self.assertIs(window.stack.currentWidget(), window.system_page)
            self.assertIs(window.system_page.stack.currentWidget(), panel)
            self.assertTrue(window.btn_next_page.isEnabled())

            window.go_to_previous_page()
            self.assertIs(window.stack.currentWidget(), window.system_page)
            self.assertIs(window.system_page.stack.currentWidget(), window.system_page.additions_panel)

            window.go_to_previous_page()
            self.assertIs(window.stack.currentWidget(), window.library_tab)
            self.assertFalse(window.btn_previous_page.isEnabled())

            window.go_to_next_page()
            self.assertIs(window.stack.currentWidget(), window.system_page)
            self.assertIs(window.system_page.stack.currentWidget(), window.system_page.additions_panel)
        finally:
            if getattr(window, "engine", None):
                try:
                    window.engine.close()
                except Exception:
                    pass
            _close_qt_window(window, app)

    def test_unknown_system_section_restores_to_current_default(self):
        from gui.main import window as window_module

        window = window_module.ModernApp.__new__(window_module.ModernApp)
        window.stack = mock.Mock()
        window.library_tab = object()  # type: ignore
        window.dock_view = object()  # type: ignore
        window.build_page = None
        window.system_page = mock.Mock()
        window._suppress_page_history = False
        window._page_history = []
        window._page_history_index = -1
        window._record_current_page = mock.Mock()

        window_module.ModernApp._restore_current_page(
            window,
            {"current_page": "system", "current_system_section": "coherence_analyzer"},
        )

        window.stack.setCurrentWidget.assert_called_once_with(window.system_page)
        window.system_page._set_section.assert_called_once_with("tree_organization")
        window._record_current_page.assert_called_once_with()

    def test_removed_help_page_setting_restores_to_library(self):
        from gui.main import window as window_module

        window = window_module.ModernApp.__new__(window_module.ModernApp)
        window.stack = mock.Mock()
        window.library_tab = object()  # type: ignore
        window.dock_view = object()  # type: ignore
        window.build_page = None
        window.system_page = mock.Mock()
        window._suppress_page_history = False
        window._page_history = []
        window._page_history_index = -1
        window._record_current_page = mock.Mock()

        window_module.ModernApp._restore_current_page(
            window,
            {"current_page": "help", "current_system_section": "tree_organization"},
        )

        window.stack.setCurrentWidget.assert_called_once_with(window.library_tab)
        window._record_current_page.assert_called_once_with()

    def test_dock_page_setting_without_docked_mode_restores_to_library(self):
        from gui.main import window as window_module

        window = window_module.ModernApp.__new__(window_module.ModernApp)
        window.stack = mock.Mock()
        window.library_tab = object()  # type: ignore
        window.dock_view = object()  # type: ignore
        window.build_page = None
        window.system_page = mock.Mock()
        window._suppress_page_history = False
        window._page_history = []
        window._page_history_index = -1
        window._record_current_page = mock.Mock()

        window_module.ModernApp._restore_current_page(
            window,
            {"current_page": "dock", "docked_mode": False},
        )

        window.stack.setCurrentWidget.assert_called_once_with(window.library_tab)
        window._record_current_page.assert_called_once_with()

    def test_docked_mode_sets_and_clears_maximum_width(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        from gui.main.launcher import ModernApp
        from gui.utils.constants import DOCKED_MAXIMUM_WIDTH, DOCKED_WINDOW_WIDTH

        app = QApplication.instance() or QApplication([])
        window = ModernApp()
        try:
            window.footer.set_docked_presentation(False)
            window.footer.set_coherence_state("Coherence looks stable. Library is ready to build.", True, can_build=True)
            self.assertFalse(window.footer.btn_build.isHidden())

            window.view_controller.toggle_docked(True)
            self.assertEqual(window.maximumWidth(), DOCKED_MAXIMUM_WIDTH)
            self.assertEqual(window.width(), DOCKED_WINDOW_WIDTH)
            self.assertTrue(window.footer.btn_build.isHidden())
            self.assertEqual(window.footer.lbl_status.text(), "Ready")
            dock_view = window.dock_view
            self.assertEqual(dock_view.scroll_area.verticalScrollBarPolicy(), Qt.ScrollBarAlwaysOff)
            options_bottom = dock_view.options_section.y() + dock_view.options_section.height()
            self.assertLessEqual(options_bottom, dock_view.scroll_area.viewport().height())
            tree_height = window.height()
            dock_view.options_section.set_expanded(False)

            window.dock_view.set_docked_view_mode("map")
            self.assertEqual(window.width(), DOCKED_WINDOW_WIDTH)
            self.assertLessEqual(tree_height, window.height() + 80)
            self.assertFalse(window.dock_view.options_section.is_expanded)
            self.assertLessEqual(
                window.dock_view.map_page.map_stage.height(),
                window.dock_view.view_stack.height(),
            )
            self.assertFalse(window.dock_view.map_page.zoom_combo.isVisible())
            self.assertEqual(window.dock_view.map_page.zoom_combo.currentText(), "4")
            self.assertFalse(window.dock_view.map_page.status.isVisible())

            window.resize(DOCKED_WINDOW_WIDTH, 720)
            window.library_tab._visible_record_ids_from_proxy = mock.Mock(return_value={"record-1", "record-2"})
            window.sync_search_ui_state(
                query='category:"Melodics"',
                active_saved_filters=set(),
                active_source_filters=set(),
                active_categories={"Melodics"},
                confidence_range=(0.0, 1.0),
            )
            self.assertEqual(window.height(), 720)
            self.assertEqual(window.dock_view.map_page.map._visible_record_ids, {"record-1", "record-2"})

            window.view_controller.toggle_docked(False)
            self.assertGreater(window.maximumWidth(), DOCKED_MAXIMUM_WIDTH)
            self.assertFalse(window.footer.btn_build.isHidden())
        finally:
            if getattr(window, "engine", None):
                try:
                    window.engine.close()
                except Exception:
                    pass
            _close_qt_window(window, app)

    def test_saved_docked_mode_restores_dock_view_after_page_restore(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        from gui.main.launcher import ModernApp

        app = QApplication.instance() or QApplication([])
        window = ModernApp()
        try:
            window.apply_app_settings(
                {
                    "geometry": None,
                    "docked_mode": True,
                    "classification_range_min": 0.0,
                    "library_view_modes": ("table", "tree", "map"),
                    "default_view_tree": False,
                    "theme_key": "ash",
                    "zoom_percent": 100,
                    "current_page": "library",
                    "current_system_section": "tree_organization",
                }
            )

            self.assertIs(window.stack.currentWidget(), window.dock_view)
            self.assertTrue(window.footer._docked_presentation)
            self.assertFalse(window.page_nav_bar.isVisible())
        finally:
            if getattr(window, "engine", None):
                try:
                    window.engine.close()
                except Exception:
                    pass
            _close_qt_window(window, app)

    def test_system_default_section_is_tree_organization_when_available(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication, QWidget
        from gui.widgets.system_page import SystemPage

        app = QApplication.instance() or QApplication([])
        page = SystemPage()
        panel = QWidget()
        try:
            page.set_tree_organization_panel(panel)
            page._set_section("")
            self.assertIs(page.stack.currentWidget(), panel)
        finally:
            page.deleteLater()

    def test_open_system_defaults_to_tree_organization_section(self):
        from gui.main import window as window_module

        window = window_module.ModernApp.__new__(window_module.ModernApp)
        window._suppress_page_history = False
        window.system_controller = mock.Mock()
        window.system_page = mock.Mock()
        window.system_page.tree_organization_panel = object()
        window.tree_organization_controller = mock.Mock()
        window._record_current_page = mock.Mock()

        window_module.ModernApp.open_system_workspace(window)

        window.tree_organization_controller.show_profile_list.assert_called_once_with()
        window.system_controller.open_workspace.assert_called_once_with()
        window.system_page._set_section.assert_called_once_with("tree_organization")
        window.tree_organization_controller.open_editor.assert_not_called()
        window._record_current_page.assert_called_once_with()

    def test_open_system_defers_section_refresh_to_current_section(self):
        from gui.main import window as window_module
        from gui.core.system_controller import SystemController

        window = window_module.ModernApp.__new__(window_module.ModernApp)
        window.stack = mock.Mock()
        window.data_manager = SimpleNamespace(bridge=None)  # type: ignore[assignment]
        page = mock.Mock()
        page.stack.currentWidget.return_value = object()
        page.discovery_panel = object()
        page.additions_panel = object()
        page.corrections_panel = object()
        page.anchors_panel = object()
        page.my_anchors_panel = object()
        controller = SystemController.__new__(SystemController)
        controller.app = window
        controller.page = page
        controller._anchor_draft_actions = []

        with mock.patch("gui.core.system_controller.QTimer.singleShot") as single_shot:
            SystemController.open_workspace(controller)

        page.set_mode.assert_called_once_with(False)
        page.set_anchor_draft_state.assert_called_once_with(0)
        window.stack.setCurrentWidget.assert_called_once_with(page)
        single_shot.assert_called_once()

    def test_open_system_creates_tree_organization_panel_when_missing(self):
        from gui.main import window as window_module

        window = window_module.ModernApp.__new__(window_module.ModernApp)
        window.system_page = mock.Mock()
        window.system_page.tree_organization_panel = None
        window.tree_organization_controller = mock.Mock()
        window.system_controller = mock.Mock()
        window._record_current_page = mock.Mock()

        window_module.ModernApp.open_system_workspace(window)

        window.tree_organization_controller.open_editor.assert_called_once_with()
        window.system_controller.open_workspace.assert_not_called()
        window._record_current_page.assert_not_called()

    def test_open_build_workspace_reuses_cached_page_for_same_inputs(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtCore import Signal
        from PySide6.QtWidgets import QApplication, QWidget
        from gui.main import window as window_module

        app = QApplication.instance() or QApplication([])

        class FakeBuildPage(QWidget):
            accepted = Signal()
            rejected = Signal()

            def __init__(self, *_args, **_kwargs):
                super().__init__()

            def get_options(self):
                return None

        window = window_module.ModernApp.__new__(window_module.ModernApp)
        record = PlanRecord(Path("D:/Samples/Pack/kick.wav"), "Pack", "Kicks", "Oneshots", "0.9", staging_row_id=1)
        window.engine = SimpleNamespace(session_source_roots=[Path("D:/Samples")])
        window.model = SimpleNamespace(records=[record])
        window.settings = mock.Mock()
        window.tree_organization_controller = SimpleNamespace(active_profile=None)  # type: ignore[assignment]
        window.stack = mock.Mock()
        window._record_current_page = mock.Mock()
        window.workflow_controller = mock.Mock()
        window.library_tab = object()  # type: ignore[assignment]
        window.build_page = None
        window._build_page_signature = None

        with mock.patch("gui.main.window.BuildPage", FakeBuildPage):
            window_module.ModernApp.open_build_workspace(window)
            first_page = window.build_page
            window_module.ModernApp.open_build_workspace(window)

        self.assertIs(window.build_page, first_page)
        self.assertEqual(window.stack.addWidget.call_count, 1)
        self.assertEqual(window.stack.setCurrentWidget.call_count, 2)
        _close_qt_window(first_page, app)

    def test_current_page_is_not_persisted_before_settings_restore(self):
        from gui.main import window as window_module

        window = window_module.ModernApp.__new__(window_module.ModernApp)
        window._page_persistence_enabled = False
        window.settings_controller = mock.Mock()

        window_module.ModernApp._persist_current_page(window, ("library", None))

        window.settings_controller.set_current_page.assert_not_called()

    def test_update_library_views_schedules_small_debounce(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        from gui.main.launcher import ModernApp

        app = QApplication.instance() or QApplication([])
        window = ModernApp()
        try:
            class _DummyModel:
                def record(self, row):
                    return None

            class _DummyProxy:
                def rowCount(self):
                    return 0

            window.model = _DummyModel()
            window.proxy_model =  cast(Any, _DummyProxy())

            with mock.patch.object(window._tree_rebuild_timer, "start") as start_mock:
                window._update_library_views()
                start_mock.assert_called_once_with(60)
        finally:
            if getattr(window, "engine", None):
                try:
                    window.engine.close()
                except Exception:
                    pass
            _close_qt_window(window, app)

    def test_confidence_range_min_restore_resets_on_startup(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        from PySide6.QtCore import QSettings
        from gui.main.launcher import ModernApp

        app = QApplication.instance() or QApplication([])
        settings = QSettings("UmU", "Unshuffle")
        settings.remove("classification_range_min")
        settings.remove("classification_range_max")
        settings.remove("last_scan_session_id")
        settings.remove("last_target")
        settings.setValue("classification_range_min", 0.42)

        window = ModernApp()
        try:
            control = window.library_tab.sidebar.signal_floor_control
            self.assertEqual(control.slider.value(), 0)
            self.assertEqual(window.library_tab.tree_model.confidence_floor, 0.0)
        finally:
            if getattr(window, "engine", None):
                try:
                    window.engine.close()
                except Exception:
                    pass
            _close_qt_window(window, app)
            settings.remove("classification_range_min")
            settings.remove("classification_range_max")
            settings.remove("last_scan_session_id")
            settings.remove("last_target")


class UndoRefreshTests(unittest.TestCase):
    def test_undo_stack_change_uses_lightweight_view_refresh_without_query(self):
        app = mock.Mock()
        app._is_closing = False
        app.model = object()
        app.view_controller = mock.Mock()
        app.search_controller = mock.Mock()
        app.search_controller.current_query = ""

        on_undo_stack_changed(app, 1)

        app.view_controller.apply_current_sort_state.assert_called_once_with()
        app.view_controller.update_library_views.assert_called_once_with(tree_delay_ms=0)
        app.search_controller.execute_search.assert_not_called()

    def test_full_tree_rebuild_preserves_visible_tree_state(self):
        from gui.core.view_controller import ViewController

        class _ProxyIndex:
            def row(self):
                return 0

        class _Proxy:
            def rowCount(self):
                return 0

            def index(self, row, column):
                return _ProxyIndex()

            def mapToSource(self, index):
                return index

        view = mock.Mock()
        view.snapshot_state.return_value = {"expanded": {("Oneshots",)}, "selected": set(), "current": None}

        class _App(QObject):
            pass

        app = _App()
        app.model = mock.Mock()
        app.proxy_model = _Proxy()
        app.stack = mock.Mock()
        app.stack.currentWidget.return_value = object()
        app.dock_view = object()
        app.library_tab = mock.Mock()
        app.library_tab.lib_stack.currentIndex.return_value = 1
        app.library_tab.view_tree = view

        controller = ViewController(app)
        controller.do_tree_rebuild()

        view.snapshot_state.assert_called_once_with()
        app.library_tab.tree_model.rebuild.assert_called_once_with([])
        view.restore_state.assert_called_once_with(view.snapshot_state.return_value)
        view.setUpdatesEnabled.assert_has_calls([mock.call(False), mock.call(True)])

    def test_tree_common_parent_uses_path_components_for_shared_prefix_siblings(self):
        from gui.views.library_tree import LibraryTreeView

        view = LibraryTreeView.__new__(LibraryTreeView)
        records = [
            SimpleNamespace(source_path=Path(r"C:\Samples\Pack1\kick.wav")),
            SimpleNamespace(source_path=Path(r"C:\Samples\Pack1b\snare.wav")),
        ]

        self.assertEqual(view._find_common_parent(records), Path(r"C:\Samples"))

    def test_preserved_dialog_rejects_path_outside_source_roots(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        from gui.dialogs.preserved import PreservedDialog

        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "Source"
            outside = root / "Outside"
            source.mkdir()
            outside.mkdir()
            dialog = PreservedDialog(outside, source_roots=[source])
            try:
                with mock.patch("gui.dialogs.preserved.QMessageBox.warning") as warning:
                    dialog.accept()

                warning.assert_called_once()
                self.assertEqual(dialog.result(), 0)
            finally:
                dialog.close()


class SettingsControllerSavedFilterScopeTests(unittest.TestCase):
    def test_auto_check_coherence_on_start_persists(self):
        from PySide6.QtCore import QSettings
        from gui.core.settings_controller import SettingsController

        org = "UmUTests"
        app_name = f"Unshuffle-{uuid.uuid4().hex}"
        settings = QSettings(org, app_name)
        settings.clear()
        try:
            class _Parent(QObject):
                def __init__(self):
                    super().__init__()
                    self.engine = None

            controller = SettingsController(settings, _Parent())

            self.assertTrue(controller.get_auto_check_coherence_on_start())
            controller.set_auto_check_coherence_on_start(False)

            self.assertFalse(controller.get_auto_check_coherence_on_start())
        finally:
            settings.clear()

    def test_startup_import_request_serializes_as_non_replay_restore(self):
        from gui.widgets.startup_launcher import StartupLaunchRequest

        request = StartupLaunchRequest(
            mode="import_csv",
            import_path="D:/Exports/session.csv",
            target="D:/Library",
            roots=("D:/Source",),
            session_id="session-1",
            view_modes=("table", "tree"),
            show_launcher_next_time=False,
        )

        stored = request.to_settings()

        self.assertEqual(stored["mode"], "restore")
        self.assertEqual(stored["import_path"], "")
        self.assertEqual(stored["roots"], [])
        self.assertEqual(stored["session_id"], "")

    def test_runtime_context_skips_auto_coherence_while_scan_finalizes(self):
        from gui.main.window import ModernApp

        class _SettingsController:
            def get_auto_check_coherence_on_start(self):
                return True

        class _LibraryTab:
            def is_view_available(self, mode):
                return mode == "map"

            def current_view_mode(self):
                return "table"

        app = SimpleNamespace(
            engine=SimpleNamespace(db=object(), session_id="s1"),
            model=None,
            coherence_controller=SimpleNamespace(clear_state=mock.Mock(), schedule_after_render=mock.Mock()),
            tagging_controller=SimpleNamespace(clear_state=mock.Mock()),
            drafting_controller=SimpleNamespace(apply_table_edit=mock.Mock(), apply_table_bulk_updates=mock.Mock()),
            search_controller=SimpleNamespace(model=None),
            acoustic_controller=SimpleNamespace(model=None),
            settings_controller=_SettingsController(),
            library_tab=_LibraryTab(),
            stack=SimpleNamespace(currentWidget=lambda: object()),
            view_controller=SimpleNamespace(refresh_library_map=mock.Mock()),
            _frontloading_startup=False,
            _scan_finalizing=True,
            _UNSET=ModernApp._UNSET,
        )
        app_for_method = cast(ModernApp, app)
        app._should_auto_check_coherence_on_start = lambda: ModernApp._should_auto_check_coherence_on_start(app_for_method)

        ModernApp.set_runtime_context(app_for_method, model=SimpleNamespace())

        app.coherence_controller.schedule_after_render.assert_not_called()

    def test_startup_sound_map_prepare_requires_map_and_auto_coherence(self):
        from gui.main.window import ModernApp

        class _LibraryTab:
            def __init__(self, available: bool):
                self.available = available

            def is_view_available(self, mode):
                return mode == "map" and self.available

        class _SettingsController:
            def __init__(self, enabled: bool):
                self.enabled = enabled

            def get_auto_check_coherence_on_start(self):
                return self.enabled

        app = SimpleNamespace(
            engine=SimpleNamespace(db=object()),
            model=object(),
            coherence_controller=object(),
            settings_controller=_SettingsController(True),
            library_tab=_LibraryTab(True),
        )
        app_for_method = cast(ModernApp, app)
        app._is_library_map_enabled = lambda: ModernApp._is_library_map_enabled(app_for_method)
        app._should_auto_check_coherence_on_start = lambda: ModernApp._should_auto_check_coherence_on_start(app_for_method)

        self.assertTrue(ModernApp._should_prepare_sound_map(app_for_method))

        app.settings_controller.enabled = False
        self.assertTrue(ModernApp._should_prepare_sound_map(app_for_method))

        app.settings_controller.enabled = True
        app.library_tab.available = False
        self.assertFalse(ModernApp._should_prepare_sound_map(app_for_method))

    def test_library_menu_has_coherence_actions(self):
        from PySide6.QtGui import QAction
        from PySide6.QtWidgets import QApplication
        from gui.main.actions.library import refresh_library_menu
        from gui.widgets.menu_bar import ModernMenuBar

        _app = QApplication.instance() or QApplication([])

        class _Settings:
            def value(self, _key, default=""):
                return default

        class _SettingsController:
            def __init__(self):
                self.values = []

            def get_auto_check_coherence_on_start(self):
                return True

            def set_auto_check_coherence_on_start(self, enabled):
                self.values.append(bool(enabled))

        settings_controller = _SettingsController()
        coherence_controller = SimpleNamespace(
            start_coherence_audit=mock.Mock(),
            start_continuous_refinement=mock.Mock(),
        )
        menu_bar = ModernMenuBar()
        class _App(QObject):
            def __init__(self):
                super().__init__()
                self.custom_menu_bar = menu_bar
                self.settings = _Settings()
                self.settings_controller = settings_controller
                self.workflow_controller = SimpleNamespace(start_refresh=mock.Mock(), start_scan=mock.Mock())
                self.library_tab = SimpleNamespace(_remember_scan_source=mock.Mock())
                self.worker_manager = SimpleNamespace(is_busy=lambda: False)
                self.act_new = QAction("New Staging Session...", self)
                self.act_add = QAction("Expand Current Session...", self)
                self.act_refresh = QAction("Refresh All Staged Folders", self)
                self.engine = object()
                self.model = object()
                self.coherence_controller = coherence_controller
                self.open_coherence_map = mock.Mock()

        app = _App()

        refresh_library_menu(app)

        coherence_menu = app.menu_coherence
        self.assertEqual(coherence_menu.title(), "Library Health")
        auto_action = next(action for action in coherence_menu.actions() if action.text() == "Check Library on Start")
        self.assertFalse(any(action.text() == "Open Sound Map" for action in coherence_menu.actions()))
        self.assertFalse(any(action.text() == "Run Coherence Check" for action in coherence_menu.actions()))
        self.assertFalse(any(action.text() == "Continuous Refinement..." for action in coherence_menu.actions()))

        auto_action.trigger()

        app.open_coherence_map.assert_not_called()
        coherence_controller.start_coherence_audit.assert_not_called()
        coherence_controller.start_continuous_refinement.assert_not_called()
        self.assertEqual(settings_controller.values, [False])

    def test_saved_filters_are_scoped_to_active_session(self):
        from PySide6.QtCore import QSettings
        from gui.core.settings_controller import SettingsController

        org = "UmUTests"
        app_name = f"Unshuffle-{uuid.uuid4().hex}"
        settings = QSettings(org, app_name)
        settings.clear()
        try:
            class _Parent(QObject):
                def __init__(self):
                    super().__init__()
                    self.engine = SimpleNamespace(session_id="session-a")

            parent = _Parent()
            controller = SettingsController(settings, parent)

            self.assertTrue(controller.add_filter("Kicks", 'category:"Kicks"'))
            self.assertEqual(
                controller.get_saved_filters(),
                [{"name": "Kicks", "query": 'category:"Kicks"'}],
            )

            parent.engine = SimpleNamespace(session_id="session-b")
            self.assertEqual(controller.get_saved_filters(), [])

            self.assertTrue(controller.add_filter("Snares", 'category:"Snares"'))
            self.assertEqual(
                controller.get_saved_filters(),
                [{"name": "Snares", "query": 'category:"Snares"'}],
            )

            parent.engine = SimpleNamespace(session_id="session-a")
            self.assertEqual(
                controller.get_saved_filters(),
                [{"name": "Kicks", "query": 'category:"Kicks"'}],
            )
        finally:
            settings.clear()

    def test_saved_top_level_page_is_ignored_on_startup(self):
        from PySide6.QtCore import QSettings
        from gui.core.settings_controller import SettingsController

        org = "UmUTests"
        app_name = f"Unshuffle-{uuid.uuid4().hex}"
        settings = QSettings(org, app_name)
        settings.clear()
        try:
            settings.setValue("current_page", "system")
            settings.setValue("current_system_section", "anchors")
            controller = SettingsController(settings, QObject())

            state = controller.build_app_settings_state()

            self.assertEqual(state["current_page"], "library")
            self.assertEqual(state["current_system_section"], "anchors")
        finally:
            settings.clear()

    def test_fresh_settings_default_library_view_is_table(self):
        from PySide6.QtCore import QSettings
        from gui.core.settings_controller import SettingsController

        org = "UmUTests"
        app_name = f"Unshuffle-{uuid.uuid4().hex}"
        settings = QSettings(org, app_name)
        settings.clear()
        try:
            controller = SettingsController(settings, QObject())

            state = controller.build_app_settings_state()

            self.assertFalse(state["default_view_tree"])
            self.assertEqual(state["default_view_mode"], "table")
        finally:
            settings.clear()

    def test_current_view_default_persists_full_view_mode(self):
        from PySide6.QtCore import QSettings
        from gui.core.settings_controller import SettingsController

        org = "UmUTests"
        app_name = f"Unshuffle-{uuid.uuid4().hex}"
        settings = QSettings(org, app_name)
        settings.clear()
        try:
            controller = SettingsController(settings, QObject())

            controller.save_view_default("map")
            state = controller.build_app_settings_state()

            self.assertEqual(state["default_view_mode"], "map")
            self.assertFalse(state["default_view_tree"])
        finally:
            settings.clear()

    def test_apply_app_settings_uses_default_view_mode(self):
        from gui.main.window_state import apply_app_settings

        window = SimpleNamespace(
            width=mock.Mock(return_value=1200),
            restoreGeometry=mock.Mock(),
            resize=mock.Mock(),
            library_tab=SimpleNamespace(
                set_confidence_floor=mock.Mock(),
                tree_model=SimpleNamespace(confidence_floor=None),
                set_available_view_modes=mock.Mock(),
                is_view_available=mock.Mock(return_value=True),
            ),
            dock_view=SimpleNamespace(set_map_available=mock.Mock()),
            custom_menu_bar=SimpleNamespace(
                set_library_view_available=mock.Mock(),
                set_startup_launcher_visible=mock.Mock(),
            ),
            settings_controller=SimpleNamespace(get_show_startup_launcher=mock.Mock(return_value=True)),
            view_controller=SimpleNamespace(set_view_mode=mock.Mock(), toggle_docked=mock.Mock()),
            apply_theme=mock.Mock(),
            apply_zoom=mock.Mock(),
            _activate_page_key=mock.Mock(),
            _record_current_page=mock.Mock(),
            _page_history=[],
            _page_history_index=-1,
            _suppress_page_history=False,
            _page_persistence_enabled=False,
        )

        apply_app_settings(cast(Any, window), {"default_view_mode": "map", "library_view_modes": ("table", "tree", "map")})

        window.view_controller.set_view_mode.assert_called_once_with("map")

    def test_save_current_view_default_shows_confirmation(self):
        from gui.utils.ui_helpers import _save_current_view_default

        app = SimpleNamespace(
            library_tab=SimpleNamespace(current_view_mode=mock.Mock(return_value="tree")),
            settings_controller=SimpleNamespace(save_view_default=mock.Mock()),
        )

        with mock.patch("PySide6.QtWidgets.QMessageBox.information") as info:
            _save_current_view_default(cast(Any, app))

        app.settings_controller.save_view_default.assert_called_once_with("tree")
        info.assert_called_once_with(app, "Default View", "Tree view will open by default.")

    def test_library_page_state_is_scoped_to_active_session(self):
        from PySide6.QtCore import QSettings
        from gui.core.settings_controller import SettingsController

        org = "UmUTests"
        app_name = f"Unshuffle-{uuid.uuid4().hex}"
        settings = QSettings(org, app_name)
        settings.clear()
        try:
            class _Parent(QObject):
                def __init__(self):
                    super().__init__()
                    self.engine = SimpleNamespace(session_id="session-a")

            parent = _Parent()
            controller = SettingsController(settings, parent)
            controller.save_library_page_state(
                {
                    "query": 'category:"Kicks"',
                    "audio_types": ["Oneshots"],
                    "view_mode": "tree",
                }
            )

            self.assertEqual(
                controller.get_library_page_state(),
                {
                    "query": 'category:"Kicks"',
                    "audio_types": ["Oneshots"],
                    "view_mode": "tree",
                },
            )

            parent.engine = SimpleNamespace(session_id="session-b")
            self.assertEqual(controller.get_library_page_state(), {})
        finally:
            settings.clear()

    def test_restore_library_page_state_applies_query_type_and_view(self):
        from gui.main.window import ModernApp

        search_controller = SimpleNamespace(
            _audio_types=None,
            _current_query="",
            sync_search_ui=mock.Mock(),
            execute_search=mock.Mock(),
        )
        app = SimpleNamespace(
            settings_controller=SimpleNamespace(
                get_library_page_state=mock.Mock(
                    return_value={
                        "query": 'category:"Kicks"',
                        "audio_types": ["Loops"],
                        "view_mode": "map",
                    }
                )
            ),
            search_controller=search_controller,
            library_tab=SimpleNamespace(),
            proxy_model=SimpleNamespace(set_audio_types=mock.Mock()),
            view_controller=SimpleNamespace(set_view_mode=mock.Mock()),
            sync_type_filter_state=mock.Mock(),
            _restoring_library_page_state=False,
        )

        ModernApp.restore_library_page_state(cast(ModernApp, app))

        self.assertEqual(search_controller._audio_types, {"Loops"})
        app.proxy_model.set_audio_types.assert_called_once_with({"Loops"})
        app.sync_type_filter_state.assert_called_once_with()
        search_controller.sync_search_ui.assert_called_once_with('category:"Kicks"')
        app.view_controller.set_view_mode.assert_called_once_with("map")
        search_controller.execute_search.assert_called_once_with()

    def test_pre_v1_saved_filters_key_is_ignored_for_v1(self):
        from PySide6.QtCore import QSettings
        from gui.core.settings_controller import SettingsController

        org = "UmUTests"
        app_name = f"Unshuffle-{uuid.uuid4().hex}"
        settings = QSettings(org, app_name)
        settings.clear()
        try:
            class _Parent(QObject):
                def __init__(self):
                    super().__init__()
                    self.engine = SimpleNamespace(session_id="session-a")

            settings.setValue("saved_filters", [{"name": "Old", "query": 'category:"Old"'}])
            settings.setValue("last_scan_session_id", "session-a")
            controller = SettingsController(settings, _Parent())

            self.assertEqual(controller.get_saved_filters(), [])
        finally:
            settings.clear()

    def test_scalar_confidence_floor_key_is_ignored_for_v1(self):
        from PySide6.QtCore import QSettings
        from gui.core.settings_controller import SettingsController

        org = "UmUTests"
        app_name = f"Unshuffle-{uuid.uuid4().hex}"
        settings = QSettings(org, app_name)
        settings.clear()
        try:
            settings.setValue("classification_floor", 0.42)
            controller = SettingsController(settings, QObject())

            self.assertEqual(controller.build_app_settings_state()["classification_range_min"], 0.0)
            self.assertEqual(controller.get_classification_range(), (0.0, 1.0, 0.0))
        finally:
            settings.clear()

    def test_undo_stack_change_skips_refresh_while_closing(self):
        app = mock.Mock()
        app._is_closing = True
        app.model = object()
        app.view_controller = mock.Mock()
        app.search_controller = mock.Mock()

        on_undo_stack_changed(app, 1)

        app.view_controller.apply_current_sort_state.assert_not_called()
        app.search_controller.execute_search.assert_not_called()


class WorkflowControllerRestoreTests(unittest.TestCase):
    def test_append_scan_skips_expensive_work_only_for_current_workbench_hashes(self):
        from gui.core.workflow_controller import WorkflowController

        existing = mock.Mock(spec=PlanRecord)
        existing.hash = "hash-existing"

        class _Parent(QObject):
            def __init__(self):
                super().__init__()
                self.model = SimpleNamespace(records=[existing])
                self.tagging_controller = mock.Mock()

        engine = mock.Mock()
        engine.db.get_committed_hashes.return_value = {"hash-committed"}
        worker_manager = mock.Mock()
        controller = WorkflowController(engine, worker_manager, mock.Mock(), _Parent())

        controller.start_scan([Path("D:/Samples")], append=True)

        worker_manager.start_scan.assert_called_once()
        kwargs = worker_manager.start_scan.call_args.kwargs
        self.assertEqual(kwargs["skip_expensive_hashes"], {"hash-existing"})
        self.assertEqual(kwargs["lib_hashes"], set())
        engine.db.get_committed_hashes.assert_not_called()

    def test_fresh_scan_does_not_filter_against_committed_build_history(self):
        from gui.core.workflow_controller import WorkflowController

        class _Parent(QObject):
            def __init__(self):
                super().__init__()
                self.model = None
                self.tagging_controller = mock.Mock()
                self.tree_organization_controller = None

        engine = mock.Mock()
        engine.db.get_committed_hashes.return_value = {"hash-committed"}
        worker_manager = mock.Mock()
        controller = WorkflowController(engine, worker_manager, mock.Mock(), _Parent())

        controller.start_scan([Path("D:/Samples")], append=False)

        worker_manager.start_scan.assert_called_once()
        kwargs = worker_manager.start_scan.call_args.kwargs
        self.assertEqual(kwargs["skip_expensive_hashes"], set())
        self.assertEqual(kwargs["lib_hashes"], set())
        engine.db.get_committed_hashes.assert_not_called()

    def test_scan_dedupe_keeps_records_when_committed_hashes_are_not_passed(self):
        from gui.core.workflow_records import dedupe_plan_records

        record = mock.Mock(spec=PlanRecord)
        record.hash = "hash-committed"
        record.source_path = Path("D:/Samples/kick.wav")

        new_records, lib_dupes, session_dupes = dedupe_plan_records([record], set(), set())

        self.assertEqual(new_records, [record])
        self.assertEqual(lib_dupes, 0)
        self.assertEqual(session_dupes, 0)

    def test_new_scan_clears_active_custom_tree(self):
        from gui.core.workflow_controller import WorkflowController

        class _Parent(QObject):
            def __init__(self):
                super().__init__()
                self.model = None
                self.tagging_controller = mock.Mock()
                self.tree_organization_controller = mock.Mock()

        engine = mock.Mock()
        worker_manager = mock.Mock()
        controller = WorkflowController(engine, worker_manager, mock.Mock(), _Parent())

        controller.start_scan([Path("D:/Fresh")], append=False)

        controller.app.tree_organization_controller.disable_profile.assert_called_once_with(refresh=False)

    def test_preserved_undo_confirmation_restarts_with_confirmation_flag(self):
        from PySide6.QtWidgets import QMessageBox
        from gui.core.workflow_controller import WorkflowController

        worker_manager = mock.Mock()
        controller = WorkflowController(None, worker_manager, mock.Mock(), QObject())
        result = {
            "requires_preserved_confirmation": True,
            "session_id": "session-1",
            "items": [
                {
                    "action": "delete_from_target",
                    "source_path": "D:/Source/HANDSOFF",
                    "target_path": "D:/Target/HANDSOFF",
                }
            ],
        }

        with mock.patch.object(QMessageBox, "warning", return_value=QMessageBox.Yes):
            controller.handle_undo_finished(result)

        worker_manager.start_undo.assert_called_once_with("session-1", confirm_preserved=True)

    def test_scan_signal_uses_pending_finalize_options(self):
        from gui.core.workflow_controller import WorkflowController

        parent = QObject()
        controller = WorkflowController(None, mock.Mock(), mock.Mock(), parent)
        ready = mock.Mock()
        controller._pending_finalize_options = {
            "show_summary": False,
            "defer_background_work": False,
            "on_ready": ready,
        }

        with mock.patch.object(controller, "finalize_scan_data") as finalize_mock:
            controller.finalize_scan_data_from_signal(["record"], False, {"total_scanned": 1})

        finalize_mock.assert_called_once_with(
            ["record"],
            False,
            {"total_scanned": 1},
            show_summary=False,
            defer_background_work=False,
            on_ready=ready,
            persist_staging=False,
        )
        self.assertEqual(controller._pending_finalize_options, {})

    def test_cancelled_scan_signal_does_not_finalize_partial_results(self):
        from gui.core.workflow_controller import WorkflowController

        class _Footer:
            def __init__(self):
                self.status = None

            def set_status(self, text):
                self.status = text

            def set_busy_state(self, _busy):
                pass

        from types import SimpleNamespace

        app = SimpleNamespace(
            footer=_Footer(),
            _scan_finalizing=True,
            library_tab=SimpleNamespace(set_busy=mock.Mock()),
            audio_controller=SimpleNamespace(toggle_audio_bar=mock.Mock()),
            stack=None,
            dock_view=None,
        )
        parent = QObject()
        controller = WorkflowController(None, mock.Mock(), mock.Mock(), parent)
        controller.app = app
        finished = []
        controller.scanFinished.connect(lambda stats: finished.append(stats))

        with mock.patch.object(controller, "finalize_scan_data") as finalize_mock:
            controller.handle_scan_finished(["partial"], False, {"cancelled": True})

        finalize_mock.assert_not_called()
        self.assertEqual(app.footer.status, "Scan canceled.")
        self.assertFalse(app._scan_finalizing)
        self.assertEqual(finished, [{"cancelled": True}])

    def test_cancelled_refresh_restores_previous_session_instead_of_clearing(self):
        from gui.core.workflow_controller import WorkflowController

        class _Footer:
            def __init__(self):
                self.status = None

            def set_status(self, text):
                self.status = text

        app = SimpleNamespace(
            footer=_Footer(),
            _scan_finalizing=True,
        )
        parent = QObject()
        controller = WorkflowController(None, mock.Mock(), mock.Mock(), parent)
        controller.app = app
        controller._pending_finalize_options = {"restore_previous_session_on_cancel": True}
        finished = []
        controller.scanFinished.connect(lambda stats: finished.append(stats))

        with mock.patch.object(controller, "finalize_scan_data") as finalize_mock, \
             mock.patch.object(controller, "_clear_workbench_for_cancelled_scan") as clear_mock, \
             mock.patch.object(controller, "restore_session") as restore_mock:
            controller.handle_scan_finished(["partial"], False, {"cancelled": True})

        finalize_mock.assert_not_called()
        clear_mock.assert_not_called()
        restore_mock.assert_called_once_with()
        self.assertEqual(app.footer.status, "Refresh canceled. Restoring previous session...")
        self.assertFalse(app._scan_finalizing)
        self.assertEqual(controller._pending_finalize_options, {})
        self.assertEqual(finished, [{"cancelled": True}])

    def test_start_refresh_marks_scan_to_restore_previous_session_on_cancel(self):
        from gui.core.workflow_controller import WorkflowController

        app = SimpleNamespace(
            drafting_controller=None,
        )
        parent = QObject()
        controller = WorkflowController(None, mock.Mock(), mock.Mock(), parent)
        controller.app = app
        controller._engine = SimpleNamespace(session_source_roots=[Path("D:/Samples")])
        controller.clear_build_handover_state = mock.Mock()

        with mock.patch.object(controller, "start_scan", return_value=True) as start_scan_mock:
            controller.start_refresh([])

        start_scan_mock.assert_called_once_with(
            ["D:\\Samples"],
            append=False,
            require_clear_draft=False,
            finalize_options={"restore_previous_session_on_cancel": True},
        )

    def test_finalize_scan_data_from_signal_drops_cancel_only_restore_option(self):
        from gui.core.workflow_controller import WorkflowController

        app = mock.Mock()
        controller = WorkflowController(app, mock.Mock(), mock.Mock(), QObject())
        controller._pending_finalize_options = {
            "restore_previous_session_on_cancel": True,
            "persist_staging": True,
        }

        with mock.patch.object(controller, "finalize_scan_data") as finalize:
            controller.finalize_scan_data_from_signal(["record"], False, {"total_scanned": 1})

        finalize.assert_called_once_with(["record"], False, {"total_scanned": 1}, persist_staging=True)

    def test_scan_summary_text_includes_duplicate_breakdown(self):
        from gui.core.workflow_controller import scan_summary_text

        self.assertEqual(
            scan_summary_text(
                {
                    "total_scanned": 12,
                    "added_count": 9,
                    "lib_dupe_count": 2,
                    "session_dupe_count": 1,
                    "total_dupe_count": 3,
                }
            ),
            "Scanned 12 files.\nAdded 9 new files.\nSkipped 3 duplicates.",
        )

    def test_scan_summary_chart_pixmap_is_drawn(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        from gui.core.workflow_controller import scan_summary_chart_pixmap

        _app = QApplication.instance() or QApplication([])

        pixmap = scan_summary_chart_pixmap(
            {
                "total_scanned": 12,
                "added_count": 9,
                "lib_dupe_count": 2,
                "session_dupe_count": 1,
                "total_dupe_count": 3,
            }
        )

        self.assertFalse(pixmap.isNull())
        self.assertGreater(pixmap.width(), 300)

    def test_scan_chart_segments_prefer_categories(self):
        from gui.core.workflow_controller import _chart_segments

        segments = _chart_segments(
            {
                "added_count": 20,
                "total_dupe_count": 5,
                "category_counts": {"Kicks": 8, "Snares": 4},
            }
        )

        self.assertEqual(segments, [("Kicks", 8), ("Snares", 4)])

    def test_finalize_scan_data_clears_sticky_scan_status(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        from gui.core.workflow_controller import WorkflowController
        from unshuffle.core import PlanRecord

        _app = QApplication.instance() or QApplication([])
        record = PlanRecord(Path("D:/Samples/kick.wav"), "Pack", "Kicks", "Oneshots", "0.9")

        class _LineEdit:
            def blockSignals(self, _blocked):
                pass

            def clear(self):
                pass

        class _LibraryTab:
            def __init__(self):
                self.edit_search = _LineEdit()
                self._refresh_search_button_state = mock.Mock()
                self._capture_column_width_ratios = mock.Mock()
                self._apply_proportional_column_widths = mock.Mock()

            def set_sources(self, _sources):
                pass

        class _App(QObject):
            def __init__(self):
                super().__init__()
                self.model = None
                self.engine = None
                self.undo_stack = mock.Mock()
                self.data_manager = mock.Mock()
                self.proxy_model = mock.Mock()
                self.library_tab = _LibraryTab()
                self.footer = mock.Mock()
                self.search_controller = mock.Mock()
                self.view_controller = mock.Mock()
                self._scan_finalizing = False
                self._view_headers_initialized = True

            def set_runtime_context(self, *, model):
                self.model = model

        app = _App()
        controller = WorkflowController(None, mock.Mock(), app.undo_stack, app)

        with mock.patch("PySide6.QtCore.QTimer.singleShot", side_effect=lambda _delay, callback: callback()):
            controller.finalize_scan_data(
                [record],
                False,
                {"total_scanned": 1, "added_count": 1, "lib_dupe_count": 0, "session_dupe_count": 0, "total_dupe_count": 0},
                show_summary=False,
                persist_staging=False,
                defer_background_work=True,
                schedule_background_work=False,
            )

        app.footer.set_status.assert_called_with("Ready")

    def test_delete_selection_from_disk_deletes_physically(self):
        from gui.main.actions.selection import delete_selection_from_disk

        records = [mock.Mock(source_path=Path("D:/Samples/kick.wav"))]
        app = SimpleNamespace(
            selected_records=mock.Mock(return_value=records),
            workflow_controller=SimpleNamespace(delete_records_physically=mock.Mock()),
        )

        delete_selection_from_disk(app)

        app.workflow_controller.delete_records_physically.assert_called_once_with(records)

    def test_failed_physical_delete_keeps_row_visible(self):
        from PySide6.QtWidgets import QMessageBox
        from gui.core.workflow_controller import WorkflowController

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            deleted_file = root / "deleted.wav"
            failed_file = root / "locked.wav"
            deleted_file.write_bytes(b"ok")
            failed_file.write_bytes(b"locked")
            deleted_record = SimpleNamespace(source_path=deleted_file)
            failed_record = SimpleNamespace(source_path=failed_file)

            class _Model:
                def __init__(self):
                    self.records = [deleted_record, failed_record]
                    self.beginResetModel = mock.Mock()
                    self.endResetModel = mock.Mock()
                    self._invalidate_unique_values = mock.Mock()
                    self._rebuild_row_and_color_caches = mock.Mock()

                def normalized_source_path(self, row):
                    return str(self.records[row].source_path.resolve()).replace("\\", "/").lower()

            db = mock.Mock()
            engine = SimpleNamespace(db=db, session_id="delete-session")
            app = SimpleNamespace(
                model=_Model(),
                search_controller=SimpleNamespace(execute_search=mock.Mock()),
                footer=SimpleNamespace(log=mock.Mock()),
            )
            controller = WorkflowController(engine, mock.Mock(), mock.Mock(), None)
            controller.app = app

            original_unlink = Path.unlink

            def _unlink(path_self):
                if Path(path_self) == failed_file:
                    raise OSError("locked")
                return original_unlink(path_self)

            with mock.patch("PySide6.QtWidgets.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes), \
                 mock.patch("PySide6.QtWidgets.QMessageBox.warning") as warning, \
                 mock.patch.object(Path, "unlink", _unlink):
                controller.delete_records_physically([deleted_record, failed_record])

            self.assertEqual(app.model.records, [failed_record])
            db.remove_staging_by_source.assert_called_once_with("delete-session", deleted_file.as_posix())
            app.search_controller.execute_search.assert_called_once()
            warning.assert_called_once()

    def test_physical_delete_refuses_records_outside_active_source_roots(self):
        from PySide6.QtWidgets import QMessageBox
        from gui.core.workflow_controller import WorkflowController

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_root = root / "source"
            outside_root = root / "outside"
            source_root.mkdir()
            outside_root.mkdir()
            safe_file = source_root / "kick.wav"
            unsafe_file = outside_root / "snare.wav"
            safe_file.write_bytes(b"ok")
            unsafe_file.write_bytes(b"no")
            safe_record = SimpleNamespace(source_path=safe_file)
            unsafe_record = SimpleNamespace(source_path=unsafe_file)

            class _Model:
                def __init__(self):
                    self.records = [safe_record, unsafe_record]
                    self.beginResetModel = mock.Mock()
                    self.endResetModel = mock.Mock()
                    self._invalidate_unique_values = mock.Mock()
                    self._rebuild_row_and_color_caches = mock.Mock()

                def normalized_source_path(self, row):
                    return str(self.records[row].source_path.resolve()).replace("\\", "/").lower()

            db = mock.Mock()
            engine = SimpleNamespace(
                db=db,
                session_id="delete-session",
                session_source_roots=[source_root],
                session_source_root=source_root,
            )
            app = SimpleNamespace(
                model=_Model(),
                search_controller=SimpleNamespace(execute_search=mock.Mock()),
                footer=SimpleNamespace(log=mock.Mock()),
            )
            controller = WorkflowController(engine, mock.Mock(), mock.Mock(), None)
            controller.app = app

            with mock.patch("PySide6.QtWidgets.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes), \
                 mock.patch("PySide6.QtWidgets.QMessageBox.warning") as warning:
                controller.delete_records_physically([safe_record, unsafe_record])

            self.assertFalse(safe_file.exists())
            self.assertTrue(unsafe_file.exists())
            self.assertEqual(app.model.records, [unsafe_record])
            db.remove_staging_by_source.assert_called_once_with("delete-session", safe_file.as_posix())
            app.search_controller.execute_search.assert_called_once()
            warning.assert_called_once()

    def test_restore_session_uses_persisted_staging_rows_without_dedupe(self):
        from gui.core.workflow_controller import WorkflowController

        record = mock.Mock(spec=PlanRecord)
        record.hash = "abc123"
        record.source_path = Path("Source/kick.wav")

        class _Settings:
            def value(self, key, default=""):
                values = {
                    "last_target": "D:/Library",
                    "last_scan_session_id": "session-1",
                }
                return values.get(key, default)

        class _Parent(QObject):
            def __init__(self):
                super().__init__()
                self.settings = _Settings()
                self._restore_session_worker = None

        parent = _Parent()
        controller = WorkflowController(None, mock.Mock(), mock.Mock(), parent)

        class _FakeWorker(QObject):
            finished = Signal(dict)
            error = Signal(str)

            def __init__(self, target, session_id):
                super().__init__()
                self.target = target
                self.session_id = session_id

            def start(self):
                self.finished.emit(
                    {
                        "session_id": self.session_id,
                        "engine": fake_engine,
                        "records": [{"source_path": "Source/kick.wav"}],
                        "sources": ["Source"],
                        "plan": [record],
                    }
                )

            def deleteLater(self):
                return None

        fake_engine = SimpleNamespace(db=mock.Mock(), session_source_roots=[])

        with mock.patch("gui.core.workers.StartupRestoreWorker", _FakeWorker):
            with mock.patch.object(controller, "finalize_scan_data") as finalize_mock:
                controller.restore_session()

        finalize_mock.assert_called_once()
        restored_records, is_append, stats = finalize_mock.call_args.args[:3]
        self.assertEqual(restored_records, [record])
        self.assertFalse(is_append)
        self.assertEqual(stats["added_count"], 1)
        self.assertEqual(stats["total_dupe_count"], 0)
        self.assertFalse(finalize_mock.call_args.kwargs["persist_staging"])

    def test_restore_session_uses_local_db_as_active_search_db(self):
        from gui.core.workflow_controller import WorkflowController

        record = mock.Mock(spec=PlanRecord)
        record.hash = "abc123"
        record.source_path = Path("Source/kick.wav")
        global_db = mock.Mock()
        local_db = mock.Mock()
        raw_engine = SimpleNamespace(db=global_db, local_db=local_db, session_source_roots=[])

        class _FakeWorkflow:
            engine = raw_engine

            @property
            def db(self):
                return self.engine.db

            @property
            def local_db(self):
                return self.engine.local_db

            @property
            def session_source_roots(self):
                return self.engine.session_source_roots

            @session_source_roots.setter
            def session_source_roots(self, value):
                self.engine.session_source_roots = value

            @property
            def session_source_root(self):
                return getattr(self.engine, "session_source_root", None)

            @session_source_root.setter
            def session_source_root(self, value):
                self.engine.session_source_root = value

        fake_engine = _FakeWorkflow()

        class _Settings:
            def __init__(self):
                self.removed = []

            def value(self, key, default=""):
                values = {
                    "last_library_target": "D:/Library",
                    "last_scan_session_id": "session-1",
                }
                return values.get(key, default)

            def setValue(self, *_args):
                return None

            def remove(self, key):
                self.removed.append(key)

        class _Parent(QObject):
            def __init__(self):
                super().__init__()
                self.settings = _Settings()
                self._restore_session_worker = None

        class _FakeWorker(QObject):
            finished = Signal(dict)
            error = Signal(str)

            def __init__(self, target, session_id):
                super().__init__()

            def start(self):
                self.finished.emit(
                    {
                        "session_id": "session-1",
                        "target": "D:/Library",
                        "engine": fake_engine,
                        "sources": ["Source"],
                        "plan": [record],
                        "db_scope": "local",
                    }
                )

            def deleteLater(self):
                return None

        parent = _Parent()
        controller = WorkflowController(None, mock.Mock(), mock.Mock(), parent)

        with mock.patch("gui.core.workers.StartupRestoreWorker", _FakeWorker), \
             mock.patch.object(controller, "finalize_scan_data"):
            controller.restore_session()

        global_db.close.assert_called_once()
        self.assertIs(raw_engine.db, local_db)
        self.assertIs(fake_engine.db, local_db)
        self.assertIn("tagging_pass/session-1/state_key", parent.settings.removed)
        self.assertIn("tagging_pass/session-1/duplicate_count", parent.settings.removed)

    def test_restore_session_falls_back_to_last_scan_source_when_last_target_missing(self):
        from gui.core.workflow_controller import WorkflowController

        class _Settings:
            def value(self, key, default=""):
                values = {
                    "last_target": "",
                    "last_scan_source": "D:/Samples",
                    "last_scan_session_id": "session-1",
                }
                return values.get(key, default)

        class _Parent(QObject):
            def __init__(self):
                super().__init__()
                self.settings = _Settings()
                self._restore_session_worker = None

        parent = _Parent()
        controller = WorkflowController(None, mock.Mock(), mock.Mock(), parent)

        created = {}

        class _FakeWorker(QObject):
            finished = Signal(dict)
            error = Signal(str)

            def __init__(self, target, session_id):
                super().__init__()
                created["target"] = target
                created["session_id"] = session_id

            def start(self):
                self.finished.emit({"session_id": "session-1", "engine": engine_cls.return_value, "records": [], "sources": [], "plan": []})

            def deleteLater(self):
                return None

        with mock.patch("gui.core.workflow_controller.create_workflow_bridge") as engine_cls, \
             mock.patch("gui.core.workers.StartupRestoreWorker", _FakeWorker):
            engine_cls.return_value = mock.Mock(db=mock.Mock(), session_source_roots=[])
            controller.restore_session()

        self.assertEqual(created, {"target": "D:/Samples", "session_id": "session-1"})

    def test_restore_session_clears_stale_startup_restore_when_no_plan_rows(self):
        from gui.core.workflow_controller import WorkflowController

        class _Settings:
            def __init__(self):
                self.removed = []
                self.values = {}

            def value(self, key, default=""):
                values = {
                    "last_library_target": "D:/Library",
                    "last_scan_session_id": "stale-session",
                }
                return values.get(key, default)

            def remove(self, key):
                self.removed.append(key)

            def setValue(self, key, value):
                self.values[key] = value

        class _Stack:
            def __init__(self, widget):
                self.widget = widget

            def currentWidget(self):
                return self.widget

        class _Parent(QObject):
            def __init__(self):
                super().__init__()
                self.settings = _Settings()
                self._restore_session_worker = None
                self.dock_view = object()
                self.stack = _Stack(self.dock_view)
                self.view_controller = SimpleNamespace(toggle_docked=mock.Mock())
                self.set_search_status = mock.Mock()

        class _FakeWorker(QObject):
            finished = Signal(dict)
            error = Signal(str)

            def __init__(self, target, session_id):
                super().__init__()
                self.target = target
                self.session_id = session_id

            def start(self):
                self.finished.emit(
                    {
                        "session_id": self.session_id,
                        "target": self.target,
                        "records": [],
                        "sources": [],
                        "plan": [],
                    }
                )

            def deleteLater(self):
                return None

        parent = _Parent()
        worker_manager = mock.Mock()
        finished = []
        controller = WorkflowController(None, worker_manager, mock.Mock(), parent)
        controller.restoreFinished.connect(finished.append)

        with mock.patch("gui.core.workers.StartupRestoreWorker", _FakeWorker), \
             mock.patch("gui.core.workflow_controller.create_workflow_bridge") as bridge_factory, \
             mock.patch.object(controller, "finalize_scan_data") as finalize_mock:
            controller.restore_session()

        bridge_factory.assert_not_called()
        finalize_mock.assert_not_called()
        self.assertEqual(finished, [False])
        self.assertIn("last_scan_session_id", parent.settings.removed)
        self.assertIn("startup_launcher_last_choice_json", parent.settings.removed)
        self.assertEqual(parent.settings.values["show_startup_launcher"], True)
        self.assertEqual(parent.settings.values["docked_mode"], False)
        parent.view_controller.toggle_docked.assert_called_once_with(False)
        parent.set_search_status.assert_called_once_with(
            "Previous session stale-session could not be restored. Choose folders and scan again."
        )
        worker_manager.error.emit.assert_called_once_with(
            "Previous session stale-session could not be restored. Choose folders and scan again."
        )

    def test_restore_session_ignores_stale_worker_results(self):
        from gui.core.workflow_controller import WorkflowController

        class _Settings:
            def value(self, key, default=""):
                values = {
                    "last_target": "D:/Library",
                    "last_scan_session_id": "session-1",
                }
                return values.get(key, default)

        class _Parent(QObject):
            def __init__(self):
                super().__init__()
                self.settings = _Settings()
                self._restore_session_worker = None

        workers = []

        class _FakeWorker(QObject):
            finished = Signal(dict)
            error = Signal(str)

            def __init__(self, target, session_id):
                super().__init__()
                workers.append(self)

            def start(self):
                return None

            def deleteLater(self):
                return None

        parent = _Parent()
        controller = WorkflowController(None, mock.Mock(), mock.Mock(), parent)
        record = mock.Mock(spec=PlanRecord)
        record.hash = "abc123"
        record.source_path = Path("Source/kick.wav")

        with mock.patch("gui.core.workers.StartupRestoreWorker", _FakeWorker), \
             mock.patch.object(controller, "finalize_scan_data") as finalize_mock:
            controller.restore_session()
            controller.restore_session()
            workers[0].finished.emit({"engine": mock.Mock(), "sources": ["Old"], "plan": [record]})
            finalize_mock.assert_not_called()
            workers[1].finished.emit({"engine": mock.Mock(), "sources": ["New"], "plan": [record]})

        finalize_mock.assert_called_once()

    def test_restore_session_surfaces_worker_error_and_clears_active_worker(self):
        from gui.core.workflow_controller import WorkflowController

        class _Settings:
            def value(self, key, default=""):
                values = {
                    "last_target": "D:/Library",
                    "last_scan_session_id": "session-1",
                }
                return values.get(key, default)

        class _Parent(QObject):
            def __init__(self):
                super().__init__()
                self.settings = _Settings()
                self._restore_session_worker = None
                self.set_search_status = mock.Mock()

        class _FakeWorker(QObject):
            finished = Signal(dict)
            error = Signal(str)

            def __init__(self, target, session_id):
                super().__init__()

            def start(self):
                self.error.emit("database unavailable")

            def deleteLater(self):
                return None

        worker_manager = mock.Mock()
        parent = _Parent()
        controller = WorkflowController(None, worker_manager, mock.Mock(), parent)

        with mock.patch("gui.core.workers.StartupRestoreWorker", _FakeWorker):
            controller.restore_session()

        parent.set_search_status.assert_called_once_with("Startup restore failed: database unavailable")
        worker_manager.error.emit.assert_called_once_with("Startup restore failed: database unavailable")
        self.assertIsNone(parent._restore_session_worker)

    def test_start_scan_surfaces_engine_initialization_error(self):
        from gui.core.workflow_controller import WorkflowController

        class _Parent(QObject):
            def __init__(self):
                super().__init__()
                self.set_search_status = mock.Mock()
                self.tagging_controller = None
                self.tree_organization_controller = None

        worker_manager = mock.Mock()
        parent = _Parent()
        controller = WorkflowController(None, worker_manager, mock.Mock(), parent)

        with mock.patch("gui.core.workflow_controller.create_workflow_bridge", side_effect=RuntimeError("bad target")):
            started = controller.start_scan(["D:/Missing"], require_clear_draft=False)

        self.assertFalse(started)
        parent.set_search_status.assert_called_once_with("Failed to initialize engine: bad target")
        worker_manager.error.emit.assert_called_once_with("Failed to initialize engine: bad target")

    def test_start_scan_clears_stale_coherence_review_state(self):
        from gui.core.workflow_controller import WorkflowController

        class _Parent(QObject):
            def __init__(self):
                super().__init__()
                self.settings = mock.Mock()
                self.tagging_controller = mock.Mock()
                self.coherence_controller = mock.Mock()
                self.tree_organization_controller = None
                self.undo_stack = mock.Mock()
                self.model = None

        worker_manager = mock.Mock()
        worker_manager.start_scan.return_value = True
        parent = _Parent()
        controller = WorkflowController(None, worker_manager, parent.undo_stack, parent)

        engine = mock.Mock()
        engine.db.get_committed_hashes.return_value = set()
        with mock.patch("gui.core.workflow_controller.create_workflow_bridge", return_value=engine):
            started = controller.start_scan(["D:/Samples"], require_clear_draft=False)

        self.assertTrue(started)
        parent.coherence_controller.clear_state.assert_called_once_with()

    def test_worker_manager_ignores_stale_finish_and_error_callbacks(self):
        from gui.core.worker_manager import WorkerManager

        manager = WorkerManager()
        current_worker = object()
        stale_worker = object()
        finished = []
        errors = []
        manager.worker = current_worker
        manager.finished.connect(lambda worker_type, result: finished.append((worker_type, result)))
        manager.error.connect(errors.append)

        manager._on_finished(stale_worker, "scan", (["old"], False))
        manager._on_error(stale_worker, "old error")

        self.assertEqual(finished, [])
        self.assertEqual(errors, [])
        self.assertIs(manager.worker, current_worker)

        manager._on_finished(current_worker, "scan", (["new"], False))

        self.assertEqual(finished, [("scan", (["new"], False))])
        self.assertIsNone(manager.worker)

    def test_worker_manager_clears_busy_state_when_worker_start_fails(self):
        from gui.core import worker_manager as worker_manager_module
        from gui.core.worker_manager import WorkerManager

        class _BrokenWorker:
            def __init__(self, *_args, **_kwargs):
                pass

            progress = mock.Mock()
            finished = mock.Mock()
            error = mock.Mock()

            def start(self, *_args, **_kwargs):
                raise RuntimeError("start failed")

            def deleteLater(self):
                return None

        manager = WorkerManager()
        manager.engine = mock.Mock()
        busy = []
        errors = []
        manager.busyStateChanged.connect(busy.append)
        manager.error.connect(errors.append)

        with mock.patch.object(worker_manager_module, "CommitWorker", _BrokenWorker):
            started = manager.start_commit([mock.Mock()], move=False, dry_run=False, flat=False, no_px=False)

        self.assertFalse(started)
        self.assertEqual(busy, [True, False])
        self.assertEqual(errors, ["start failed"])
        self.assertIsNone(manager.worker)

    def test_export_staging_session_uses_active_target_without_save_dialog(self):
        from gui.main.actions.library import export_session

        app = mock.Mock()
        app.engine = SimpleNamespace(target_dir=Path("D:/Library"))
        app.settings.value.return_value = ""
        app.data_manager.bridge.has_session.return_value = True

        export_session(app)

        app.data_manager.export_session_to_folder.assert_called_once_with(Path("D:/Library"), parent_widget=app)

    def test_staging_session_import_dialogs_point_to_sidecar_database(self):
        library_actions = Path("gui/main/actions/library.py").read_text(encoding="utf-8")
        startup_launcher = Path("gui/widgets/startup_launcher.py").read_text(encoding="utf-8")

        self.assertIn("Unshuffle Session Database (unshuffle.db *.db)", library_actions)
        self.assertIn("Unshuffle Session Database (unshuffle.db *.db)", startup_launcher)
        self.assertNotIn("Unshuffle Session Files (*.unshuffle)", library_actions)
        self.assertNotIn("Unshuffle Session Files (*.unshuffle)", startup_launcher)

    def test_export_staging_session_replaces_existing_sidecar_without_deleting_session_parent(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication, QMessageBox
        from gui.core.data_manager import DataManager
        from unshuffle.core.paths import get_local_system_dir
        from unshuffle.persistence import UnshuffleDB

        _app = QApplication.instance() or QApplication([])
        tmp = tempfile.TemporaryDirectory()
        tmp_path = Path(tmp.name)
        source = tmp_path / "source"
        target = tmp_path / "target"
        source.mkdir()
        target.mkdir()
        sample = source / "kick.wav"
        sample.write_bytes(b"sample")

        global_db = UnshuffleDB(tmp_path / "global.db")
        local_db = UnshuffleDB(get_local_system_dir(target) / "unshuffle.db")
        session_id = "session-export"

        try:
            global_db.register_session(session_id, source, target, "pending")
            global_db.set_session_sources(session_id, [source])
            global_db.add_staging_records_bulk(
                session_id,
                [
                    (
                        1,
                        str(sample),
                        sample.name,
                        "Pack",
                        "Kicks",
                        "",
                        "Oneshots",
                        "[]",
                        "1.0",
                        0.1,
                        None,
                        "[]",
                        None,
                        None,
                        0,
                    )
                ],
            )

            local_db.register_session(session_id, source, target, "old")
            local_db.set_session_sources(session_id, [source])
            local_db.add_records_bulk(
                session_id,
                [
                    {
                        "source_path": str(sample),
                        "target_path": str(target / sample.name),
                        "category": "Kicks",
                        "subcategory": "",
                        "pack": "Old",
                        "hash": "hash",
                        "confidence": 1.0,
                        "status": "copied",
                        "tags": "[]",
                    }
                ],
            )

            app = mock.Mock()
            app.footer = mock.Mock()
            app.settings_controller = mock.Mock()
            app.settings_controller.get_saved_filters.return_value = [
                {"name": "Kicks", "query": 'cat:"Kicks"'}
            ]
            manager = DataManager(engine=SimpleNamespace(db=global_db, session_id=session_id), app=app)

            with mock.patch("gui.core.data_manager.QMessageBox.question", return_value=QMessageBox.Yes), \
                 mock.patch.object(manager, "_show_session_export_success"):
                exported = manager.export_session_to_folder(target, parent_widget=app)

            self.assertTrue(exported)
            self.assertEqual(local_db.foreign_key_violations(), [])
            self.assertEqual(len(local_db.get_staging_records(session_id)), 1)
            self.assertEqual(local_db.get_session(session_id)["mode"], "pending")
            self.assertEqual(local_db.get_session_records(session_id), [])
            self.assertEqual(
                local_db.get_session_metadata(session_id, "saved_filters"),
                '[{"name": "Kicks", "query": "cat:\\"Kicks\\""}]',
            )
        finally:
            global_db.close()
            local_db.close()
            tmp.cleanup()

    def test_import_staging_session_prompts_when_sidecar_has_multiple_sessions(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        from gui.core.data_manager import DataManager
        from unshuffle.core.paths import get_local_system_dir
        from unshuffle.persistence import UnshuffleDB

        _app = QApplication.instance() or QApplication([])
        tmp = tempfile.TemporaryDirectory()
        tmp_path = Path(tmp.name)
        export_root = tmp_path / "exported"
        export_root.mkdir()
        old_source = tmp_path / "old.wav"
        new_source = tmp_path / "new.wav"
        old_source.write_bytes(b"old")
        new_source.write_bytes(b"new")

        local_db_path = get_local_system_dir(export_root) / "unshuffle.db"
        local_db = UnshuffleDB(local_db_path)
        global_db = UnshuffleDB(tmp_path / "global.db")

        def add_session(db, session_id, source):
            db.register_session(session_id, source.parent, export_root, "pending")
            db.set_session_sources(session_id, [source.parent])
            db.add_staging_records_bulk(
                session_id,
                [
                    (
                        1,
                        str(source),
                        source.name,
                        "Pack",
                        "Kicks",
                        "",
                        "Oneshots",
                        "[]",
                        "1.0",
                        0.1,
                        None,
                        "[]",
                        None,
                        None,
                        0,
                    )
                ],
            )

        try:
            add_session(local_db, "old-session", old_source)
            add_session(local_db, "new-session", new_source)
            local_db.set_session_metadata(
                "old-session",
                "saved_filters",
                '[{"name": "Old kicks", "query": "cat:\\"Kicks\\""}]',
            )

            engine = SimpleNamespace(db=global_db, session_id="", session_source_roots=[])
            app = mock.Mock()
            app.footer = mock.Mock()
            app.workflow_controller = mock.Mock()
            app.settings_controller = mock.Mock()
            app.library_tab = mock.Mock()
            app.filter_controller = mock.Mock()
            manager = DataManager(engine=engine, app=app)

            def choose_old(_parent, _title, _label, items, _current, _editable):
                return next(item for item in items if "old-session" in item), True

            with mock.patch("gui.core.data_manager.QInputDialog.getItem", side_effect=choose_old) as get_item, \
                 mock.patch("PySide6.QtWidgets.QApplication.setOverrideCursor"), \
                 mock.patch("PySide6.QtWidgets.QApplication.restoreOverrideCursor"):
                imported = manager.import_session_from_folder(export_root, parent_widget=app)

            self.assertTrue(imported)
            get_item.assert_called_once()
            self.assertEqual(engine.session_id, "old-session")
            args = app.workflow_controller.handle_scan_finished.call_args.args
            self.assertEqual([record.source_path for record in args[0]], [old_source])
            self.assertEqual(
                [Path(row["source_path"]) for row in global_db.get_staging_records("old-session")],
                [old_source],
            )
            app.settings_controller.save_saved_filters.assert_called_once_with(
                [{"name": "Old kicks", "query": 'cat:"Kicks"'}]
            )
            app.library_tab.set_saved_filters.assert_called_once_with(
                [{"name": "Old kicks", "query": 'cat:"Kicks"'}]
            )
            app.filter_controller.refresh_dock_filters.assert_called_once_with()
            self.assertFalse(global_db.get_staging_records("new-session"))
        finally:
            local_db.close()
            global_db.close()
            tmp.cleanup()

    def test_import_staging_session_bootstraps_database_when_fresh_install_has_no_active_session(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        from gui.core.data_manager import DataManager
        from unshuffle.bridge.workflow_bridge import WorkflowBridge
        from unshuffle.core.paths import get_local_system_dir
        from unshuffle.persistence import UnshuffleDB

        _app = QApplication.instance() or QApplication([])
        tmp = tempfile.TemporaryDirectory()
        tmp_path = Path(tmp.name)
        export_root = tmp_path / "exported"
        source_root = tmp_path / "source"
        export_root.mkdir()
        source_root.mkdir()
        source = source_root / "kick.wav"
        source.write_bytes(b"sample")
        old_target_root = tmp_path / "missing-old-target"
        session_id = "fresh-import-session"

        local_db_path = get_local_system_dir(export_root) / "unshuffle.db"
        local_db = UnshuffleDB(local_db_path)
        global_db = UnshuffleDB(tmp_path / "global.db")

        try:
            local_db.register_session(session_id, source_root, old_target_root, "pending")
            local_db.set_session_sources(session_id, [source_root])
            local_db.add_staging_records_bulk(
                session_id,
                [
                    (
                        1,
                        str(source),
                        source.name,
                        "Pack",
                        "Kicks",
                        "",
                        "Oneshots",
                        "[]",
                        "1.0",
                        0.1,
                        None,
                        "[]",
                        None,
                        None,
                        0,
                    )
                ],
            )

            app = mock.Mock()
            app.footer = mock.Mock()
            app.workflow_controller = mock.Mock()
            app.settings_controller = mock.Mock()
            app.library_tab = mock.Mock()
            app.filter_controller = mock.Mock()
            fake_runtime = SimpleNamespace(
                db=global_db,
                local_db=None,
                target_dir=export_root,
                session_id=session_id,
                session_source_root=None,
                session_source_roots=[],
                interrupted=False,
                progress_callback=None,
            )
            manager = DataManager(app=app)

            with mock.patch(
                "unshuffle.bridge.workflow_bridge.create_workflow_bridge",
                return_value=WorkflowBridge(fake_runtime),
            ) as create_bridge, \
                 mock.patch("PySide6.QtWidgets.QApplication.setOverrideCursor"), \
                 mock.patch("PySide6.QtWidgets.QApplication.restoreOverrideCursor"):
                imported = manager.import_session_from_folder(local_db_path, parent_widget=app)

            self.assertTrue(imported)
            create_bridge.assert_called_once_with(export_root, session_id=session_id)
            app.set_runtime_context.assert_called_once()
            self.assertEqual(
                os.path.normcase(global_db.get_session(session_id)["target_root"]),
                os.path.normcase(str(export_root)),
            )
            self.assertEqual([Path(row["source_path"]) for row in global_db.get_staging_records(session_id)], [source])
            app.workflow_controller.handle_scan_finished.assert_called_once()
        finally:
            local_db.close()
            global_db.close()
            tmp.cleanup()

    def test_load_staging_session_uses_background_loader_result(self):
        from gui.main.actions.session import load_staging_session

        class _Settings:
            def value(self, key, default=""):
                values = {"last_target": "D:/Library"}
                return values.get(key, default)

        class _FakeWorker(QObject):
            finished = Signal(dict)
            error = Signal(str)

            def __init__(self, target, session_id):
                super().__init__()
                self.target = target
                self.session_id = session_id

            def start(self):
                self.finished.emit(
                    {
                        "session_id": self.session_id,
                        "records": [{"source_path": "Source/kick.wav"}],
                        "sources": ["Source"],
                        "plan": [mock.Mock(spec=PlanRecord)],
                    }
                )

            def deleteLater(self):
                return None

        app = mock.Mock()
        app.settings = _Settings()
        app.footer = mock.Mock()
        app.undo_stack = mock.Mock()
        app.worker_manager = mock.Mock()
        app.search_controller = mock.Mock()
        app.search_controller.search_engine = mock.Mock()
        app.data_manager = mock.Mock()
        app.workflow_controller = mock.Mock()
        app.engine = None
        sess = {"session_id": "session-1", "source_path": "Source"}

        fake_engine = mock.Mock()
        fake_engine.session_source_roots = []

        with mock.patch("gui.main.actions.session.create_workflow_bridge", return_value=fake_engine), \
             mock.patch("gui.core.workers.SessionLoadWorker", _FakeWorker):
            load_staging_session(app, sess)

        app.workflow_controller.handle_scan_finished.assert_called_once()
        args = app.workflow_controller.handle_scan_finished.call_args.args
        self.assertEqual(len(args[0]), 1)

    def test_load_staging_session_keeps_current_engine_when_new_load_fails(self):
        from gui.main.actions.session import load_staging_session

        class _Settings:
            def value(self, key, default=""):
                return {"last_target": "D:/Library"}.get(key, default)

        old_engine = mock.Mock()
        app = mock.Mock()
        app.settings = _Settings()
        app.footer = mock.Mock()
        app.undo_stack = mock.Mock()
        app.worker_manager = mock.Mock()
        app.engine = old_engine
        app._session_load_worker = None
        sess = {"session_id": "session-1", "source_path": "Source"}

        with mock.patch("gui.main.actions.session.create_workflow_bridge", side_effect=RuntimeError("boom")):
            with mock.patch("gui.main.actions.session.QMessageBox.warning"):
                load_staging_session(app, sess)

        self.assertIs(app.engine, old_engine)
        old_engine.close.assert_not_called()
        app.undo_stack.clear.assert_not_called()

    def test_load_staging_session_rejects_missing_session_id(self):
        from gui.main.actions.session import load_staging_session

        app = mock.Mock()
        app.settings.value.return_value = "D:/Library"
        app.footer = mock.Mock()

        with mock.patch("gui.main.actions.session.create_workflow_bridge") as bridge, \
             mock.patch("gui.main.actions.session.QMessageBox.warning") as warning:
            load_staging_session(app, {"source_path": "Source"})

        bridge.assert_not_called()
        warning.assert_called_once()

    def test_confirm_history_undo_does_not_refresh_before_worker_finishes(self):
        from gui.main.window import ModernApp

        app = mock.Mock()
        session = {"session_id": "session-1", "target_root": "D:/Library"}

        with mock.patch("gui.main.actions.history.confirm_undo") as confirm:
            ModernApp._confirm_history_undo(app, session)  # type: ignore[arg-type]

        confirm.assert_called_once_with(app, session)
        app._refresh_history_page.assert_not_called()

    def test_refresh_history_page_uses_history_target_before_active_target(self):
        from gui.main import window_workspace

        class _Settings:
            def value(self, key, default=""):
                return {
                    "last_history_target": "D:/Build Target",
                    "last_target": "D:/Restored Source",
                }.get(key, default)

        window = mock.Mock()
        window.settings = _Settings()
        window.history_page = mock.Mock()

        window_workspace.refresh_history_page(window)

        window.history_page.refresh_from_target.assert_called_once_with("D:/Build Target")

    def test_build_persistence_sets_history_target_but_restored_source_does_not(self):
        from gui.core import workflow_session_persistence

        values = {}

        class _Settings:
            def setValue(self, key, value):
                values[key] = value

        engine = mock.Mock()
        engine.target_dir = Path("D:/Build Target")
        engine.session_source_roots = [Path("D:/Source")]

        workflow_session_persistence.persist_build_session(_Settings(), engine, "session-1")
        workflow_session_persistence.persist_restored_source(_Settings(), "D:/Restored Source", session_id="session-1")

        self.assertEqual(values["last_history_target"], str(Path("D:/Build Target")))
        self.assertEqual(values["last_target"], "D:/Restored Source")


class ViewControllerAndMainWindowStateTests(unittest.TestCase):
    def test_apply_current_sort_state_uses_source_model_order_and_refreshes_views(self):
        from gui.core.view_controller import ViewController

        class _Combo:
            def currentIndex(self):
                return 0

        class _LibraryTab:
            def __init__(self):
                self.combo_sort = _Combo()
                self.sort_columns = [3]
                self.tree_model = mock.Mock()
                self.view_table = mock.Mock()
                self.lib_stack = mock.Mock()
                self.lib_stack.currentIndex.return_value = 0

        class _Stack:
            def currentWidget(self):
                return object()

        class _Parent(QObject):
            def __init__(self):
                super().__init__()
                self.proxy_model = mock.Mock()
                self.model = mock.Mock()
                self.model.group_column = 1
                self.library_tab = _LibraryTab()
                self.stack = _Stack()
                self.dock_view = object()
                self.footer = mock.Mock()

        parent = _Parent()
        controller = ViewController(parent)

        with mock.patch.object(controller, "update_library_views") as refresh_mock:
            controller.apply_current_sort_state()

        parent.model.set_group_column.assert_called_once_with(3)
        parent.library_tab.tree_model.set_sort_column.assert_called_once_with(3)
        parent.proxy_model.sort.assert_called_once_with(-1)
        refresh_mock.assert_called_once_with(tree_delay_ms=0)

    def test_apply_current_sort_state_defers_reorder_during_active_draft(self):
        from gui.core.view_controller import ViewController

        class _Combo:
            def currentIndex(self):
                return 0

        class _LibraryTab:
            def __init__(self):
                self.combo_sort = _Combo()
                self.sort_columns = [3]
                self.tree_model = mock.Mock()
                self.view_table = mock.Mock()
                self.lib_stack = mock.Mock()
                self.lib_stack.currentIndex.return_value = 0

        class _Parent(QObject):
            def __init__(self):
                super().__init__()
                self.proxy_model = mock.Mock()
                self.model = mock.Mock()
                self.library_tab = _LibraryTab()
                self.stack = mock.Mock()
                self.dock_view = object()
                self.footer = mock.Mock()
                self.drafting_controller = mock.Mock()
                self.drafting_controller.has_changes.return_value = True

        parent = _Parent()
        controller = ViewController(parent)

        with mock.patch.object(controller, "update_library_views") as refresh_mock:
            controller.apply_current_sort_state()

        parent.model.set_group_column.assert_not_called()
        parent.proxy_model.sort.assert_not_called()
        refresh_mock.assert_called_once_with(tree_delay_ms=0)

    def test_apply_current_sort_state_can_force_reorder_during_active_draft(self):
        from gui.core.view_controller import ViewController

        class _Combo:
            def currentIndex(self):
                return 0

        class _LibraryTab:
            def __init__(self):
                self.combo_sort = _Combo()
                self.sort_columns = [3]
                self.tree_model = mock.Mock()
                self.view_table = mock.Mock()
                self.lib_stack = mock.Mock()
                self.lib_stack.currentIndex.return_value = 0

        class _Parent(QObject):
            def __init__(self):
                super().__init__()
                self.proxy_model = mock.Mock()
                self.model = mock.Mock()
                self.library_tab = _LibraryTab()
                self.stack = mock.Mock()
                self.dock_view = object()
                self.footer = mock.Mock()
                self.drafting_controller = mock.Mock()
                self.drafting_controller.has_changes.return_value = True

        parent = _Parent()
        controller = ViewController(parent)

        with mock.patch.object(controller, "update_library_views") as refresh_mock:
            controller.apply_current_sort_state(force=True)

        parent.model.set_group_column.assert_called_once_with(3)
        parent.proxy_model.sort.assert_called_once_with(-1)
        refresh_mock.assert_called_once_with(tree_delay_ms=0)

    def test_apply_current_sort_state_resets_tree_sort_to_filename_when_inactive(self):
        from gui.core.view_controller import ViewController
        from gui.utils.constants import StagingColumn

        class _Combo:
            def currentIndex(self):
                return -1

        class _LibraryTab:
            def __init__(self):
                self.combo_sort = _Combo()
                self.sort_columns = [3]
                self.tree_model = mock.Mock()
                self.view_table = mock.Mock()
                self.lib_stack = mock.Mock()
                self.lib_stack.currentIndex.return_value = 0

        class _Parent(QObject):
            def __init__(self):
                super().__init__()
                self.proxy_model = mock.Mock()
                self.model = mock.Mock()
                self.library_tab = _LibraryTab()
                self.stack = mock.Mock()
                self.dock_view = object()
                self.footer = mock.Mock()

        parent = _Parent()
        controller = ViewController(parent)

        with mock.patch.object(controller, "update_library_views") as refresh_mock:
            controller.apply_current_sort_state()

        parent.library_tab.tree_model.set_sort_column.assert_called_once_with(StagingColumn.FILENAME)
        parent.model.set_group_column.assert_not_called()
        parent.proxy_model.sort.assert_called_once_with(StagingColumn.FILENAME.value, Qt.AscendingOrder)
        refresh_mock.assert_called_once_with(tree_delay_ms=0)

    def test_set_view_mode_tree_rebuilds_even_without_pending_refresh(self):
        from gui.core.view_controller import ViewController

        class _LibraryTab:
            def __init__(self):
                self.current_mode = "table"

            def set_view_mode(self, mode):
                self.current_mode = mode

            def current_view_mode(self):
                return self.current_mode

            def is_view_available(self, mode):
                return mode in {"table", "tree", "map"}

        class _Parent(QObject):
            def __init__(self):
                super().__init__()
                self.model = mock.Mock()
                self.proxy_model = mock.Mock()
                self.library_tab = _LibraryTab()
                self.save_library_page_state = mock.Mock()

        parent = _Parent()
        controller = ViewController(parent)
        controller._tree_rebuild_pending = False

        with mock.patch.object(controller, "schedule_tree_rebuild") as rebuild_mock:
            controller.set_view_mode("tree")

        rebuild_mock.assert_called_once_with(delay_ms=0)
        parent.save_library_page_state.assert_called_once_with()

    def test_set_view_mode_table_refreshes_table_path(self):
        from gui.core.view_controller import ViewController

        class _LibraryTab:
            def set_view_mode(self, mode):
                self.current_mode = mode

            def current_view_mode(self):
                return getattr(self, "current_mode", "tree")

            def is_view_available(self, mode):
                return mode in {"table", "tree", "map"}

        class _Parent(QObject):
            def __init__(self):
                super().__init__()
                self.library_tab = _LibraryTab()
                self.save_library_page_state = mock.Mock()

        parent = _Parent()
        controller = ViewController(parent)

        with mock.patch.object(controller, "update_library_views") as refresh_mock:
            controller.set_view_mode("table")

        refresh_mock.assert_called_once_with(tree_delay_ms=0)
        parent.save_library_page_state.assert_called_once_with()

    def test_set_view_mode_map_refreshes_map_path(self):
        from gui.core.view_controller import ViewController
        from gui.core import view_controller as view_controller_module

        page = mock.Mock()

        class _LibraryTab:
            def set_view_mode(self, mode):
                self.current_mode = mode

            def current_view_mode(self):
                return getattr(self, "current_mode", "table")

            def is_view_available(self, mode):
                return mode in {"table", "tree", "map"}

        class _Parent(QObject):
            def __init__(self):
                super().__init__()
                self.library_tab = _LibraryTab()
                self._ensure_library_map = mock.Mock(return_value=page)
                self.save_library_page_state = mock.Mock()

        parent = _Parent()
        controller = ViewController(parent)

        with mock.patch.object(controller, "refresh_library_map") as refresh_mock, \
             mock.patch.object(
                 view_controller_module.QTimer,
                 "singleShot",
                 side_effect=lambda _delay, callback: callback(),
             ):
            controller.set_view_mode("map")

        page.set_loading.assert_called_once_with(True, "Preparing map...")
        refresh_mock.assert_called_once_with()
        parent.save_library_page_state.assert_called_once_with()

    def test_refresh_library_map_prewarms_type_specific_projections(self):
        from gui.core.view_controller import ViewController

        class _LibraryTab:
            def __init__(self):
                self.coherence_map = None

            def _current_audio_type_filter(self):
                return ""

            def _current_category_filter(self):
                return ""

            def _visible_record_ids_from_proxy(self):
                return None

            def is_view_available(self, mode):
                return mode == "map"

        page = mock.Mock()

        class _Parent(QObject):
            def __init__(self):
                super().__init__()
                self.model = mock.Mock()
                self.library_tab = _LibraryTab()
                self._ensure_library_map = mock.Mock(return_value=page)

        parent = _Parent()
        controller = ViewController(parent)

        controller.refresh_library_map()

        page.refresh_from_app.assert_called_once()
        page.prewarm_library_projections.assert_called_once_with()
        page.set_library_filters.assert_called_once_with("", "", None)

    def test_update_library_views_refreshes_docked_map_when_in_docked_map_mode(self):
        from gui.core.view_controller import ViewController

        class _LibraryTab:
            def __init__(self):
                self.view_table = mock.Mock()
                self.tree_model = mock.Mock()

            def current_view_mode(self):
                return "tree"

            def is_view_available(self, mode):
                return mode == "map"

        class _DockView:
            def __init__(self):
                self._view_mode = "map"
                self.view_tree = mock.Mock()

            def refresh_map_from_app(self, app, force=False):
                pass

        class _Parent(QObject):
            def __init__(self):
                super().__init__()
                self.model = mock.Mock()
                self.proxy_model = mock.Mock()
                self.library_tab = _LibraryTab()
                self.dock_view = _DockView()
                self.stack = mock.Mock()
                self.stack.currentWidget.return_value = self.dock_view
                self.footer = mock.Mock()

        parent = _Parent()
        controller = ViewController(parent)
        
        with mock.patch.object(controller, "refresh_docked_map") as refresh_docked_mock:
            controller.update_library_views()
            refresh_docked_mock.assert_called_once_with(force=False)

    def test_library_map_prewarms_category_projections(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        from gui.widgets.coherence_analyzer import CoherenceAnalyzerPage

        app = QApplication.instance() or QApplication([])
        page = CoherenceAnalyzerPage(show_header=False, show_filters=False)
        try:
            page._records = [  # type: ignore
                SimpleNamespace(audio_type="Loops", category="Melodics"),
                SimpleNamespace(audio_type="Loops", category="FX"),
                SimpleNamespace(audio_type="Oneshots", category="Kicks"),
                SimpleNamespace(audio_type="Oneshots", category="Kicks"),
            ]
            page.map = mock.Mock()

            page.prewarm_library_projections()

            page.map.prewarm_projection.assert_has_calls(
                [
                    mock.call("Loops", ""),
                    mock.call("Loops", "FX"),
                    mock.call("Loops", "Melodics"),
                    mock.call("Oneshots", ""),
                    mock.call("Oneshots", "Kicks"),
                ]
            )
        finally:
            _close_qt_window(page, app)

    def test_coherence_analyzer_refresh_failure_sets_safe_status(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        from gui.widgets import coherence_analyzer as analyzer_module
        from gui.widgets.coherence_analyzer import CoherenceAnalyzerPage

        app = QApplication.instance() or QApplication([])
        page = CoherenceAnalyzerPage(show_header=False, show_filters=False)
        try:
            with mock.patch.object(analyzer_module, "coherence_points_from_app", side_effect=RuntimeError("boom")):
                page.refresh_from_app(mock.Mock())

            self.assertEqual(page.status.text(), "Sound map could not be refreshed.")
            self.assertEqual(page._records, [])
        finally:
            _close_qt_window(page, app)

    def test_coherence_projection_falls_back_when_spatial_index_fails(self):
        from gui.widgets.coherence_view_model import AnalyzerPoint
        from gui.widgets import coherence_projection

        points = [
            AnalyzerPoint(
                str(idx),
                "Loops",
                "Kicks",
                "Sub",
                f"cluster-{idx % 4}",
                [float(idx % 7), float((idx + 1) % 11), float((idx + 2) % 13)],
            )
            for idx in range(221)
        ]

        with mock.patch("unshuffle.logic.coherence.spatial_index.SpatialIndex", side_effect=RuntimeError("boom")):
            projected = coherence_projection._continuous_acoustic_projection(
                points,
                lambda left, right: sum(abs(a - b) for a, b in zip(left.vector, right.vector)),
            )

        self.assertEqual(len(projected), len(points))

    def test_vibe_bar_close_clears_similarity_state(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        from gui.main.launcher import ModernApp

        app = QApplication.instance() or QApplication([])
        window = ModernApp()
        try:
            with mock.patch.object(window.proxy_model, "clear_similarity") as clear_mock, \
                 mock.patch.object(window.view_controller, "update_library_views") as refresh_mock:
                window.vibe_bar.closeRequested.emit()
                clear_mock.assert_called_once_with()
                refresh_mock.assert_called_once_with(tree_delay_ms=0)
        finally:
            if getattr(window, "engine", None):
                try:
                    window.engine.close()
                except Exception:
                    pass
            _close_qt_window(window, app)

    def test_confidence_slider_populates_and_clears_search_token(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        from gui.main.launcher import ModernApp

        app = QApplication.instance() or QApplication([])
        window = ModernApp()
        try:
            window._on_confidence_range_changed(0.25, 1.0)
            self.assertIn('confidence:"25-100"', window.library_tab.edit_search.text())

            window._on_confidence_range_changed(0.0, 1.0)
            self.assertNotIn('confidence:"', window.library_tab.edit_search.text())
        finally:
            if getattr(window, "engine", None):
                try:
                    window.engine.close()
                except Exception:
                    pass
            _close_qt_window(window, app)

    def test_sync_type_filter_state_normalizes_both_types_to_all(self):
        from gui.main.window import ModernApp

        app_for_method = SimpleNamespace()
        app_for_method.search_controller = SimpleNamespace(_audio_types={"Oneshots", "Loops"})
        app_for_method.library_tab = SimpleNamespace(set_type_state=mock.Mock())
        app_for_method.dock_view = SimpleNamespace(set_type_state=mock.Mock())

        ModernApp.sync_type_filter_state(app_for_method)  # type: ignore[arg-type]

        app_for_method.library_tab.set_type_state.assert_called_once_with(False, False, True)
        app_for_method.dock_view.set_type_state.assert_called_once_with(False, False, True)

    def test_vibe_anchor_bar_programmatic_reset_emits_bias_change(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        from gui.widgets.vibe_anchor_bar import VibeAnchorBar

        app = QApplication.instance() or QApplication([])
        bar = VibeAnchorBar()
        changes = []
        bar.biasChanged.connect(changes.append)
        try:
            bar.slider.setValue(40)
            changes.clear()
            bar.set_value(0)

            self.assertEqual(changes, [0])
        finally:
            _close_qt_window(bar, app)

    def test_sort_change_reorders_model_records(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        from gui.main.launcher import ModernApp
        from gui.models.staging_table import StagingTableModel
        from gui.models.proxy import MultiFilterProxyModel

        app = QApplication.instance() or QApplication([])
        window = ModernApp()
        try:
            first = mock.Mock(spec=PlanRecord)
            first.pack = "Zulu"
            first.category = "Kicks"
            first.subcategory = None
            first.tags = []
            first.audio_type = "Oneshots"
            first.source_path = Path("Source/z.wav")
            first.confidence = "0.90"
            first.evidence = {}
            first.is_manual = False
            first.is_preserved = False

            second = mock.Mock(spec=PlanRecord)
            second.pack = "Alpha"
            second.category = "Snares"
            second.subcategory = None
            second.tags = []
            second.audio_type = "Oneshots"
            second.source_path = Path("Source/a.wav")
            second.confidence = "0.90"
            second.evidence = {}
            second.is_manual = False
            second.is_preserved = False

            window.model = StagingTableModel([first, second], undo_stack=window.undo_stack, sync_callback=None)
            model = cast(QAbstractItemModel, window.model)
            window.proxy_model = MultiFilterProxyModel()
            window.proxy_model.setSourceModel(model)
            window.library_tab.set_proxy_model(window.proxy_model)

            window._on_sort_changed(0)

            self.assertEqual([rec.pack for rec in window.model.records], ["Alpha", "Zulu"])
        finally:
            if getattr(window, "engine", None):
                try:
                    window.engine.close()
                except Exception:
                    pass
            _close_qt_window(window, app)

    def test_scan_reset_helper_clears_search_sort_category_and_confidence(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        from gui.main.launcher import ModernApp

        app = QApplication.instance() or QApplication([])
        window = ModernApp()
        try:
            window.library_tab.edit_search.setText('cat:"Kicks", confidence:"25-100"')
            window.library_tab.category_carousel.set_active_values({"Kicks"})
            window.library_tab.sort_carousel.set_active_values({0})
            window.library_tab.sidebar.signal_floor_control.set_range(0.25, 1.0)

            main_window_scan_flow.reset_discovery_state(window)

            self.assertEqual(window.library_tab.edit_search.text(), "")
            self.assertFalse(window.library_tab.sort_carousel.is_active)
            self.assertFalse(window.library_tab.category_carousel.is_active)
            self.assertEqual(window.library_tab.sidebar.signal_floor_control.slider.min_val, 0)
            self.assertEqual(window.library_tab.sidebar.signal_floor_control.slider.max_val, 100)
        finally:
            if getattr(window, "engine", None):
                try:
                    window.engine.close()
                except Exception:
                    pass
            _close_qt_window(window, app)

    def test_search_sync_refreshes_save_button_and_sort_sidebar_state(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        from gui.main.launcher import ModernApp

        app = QApplication.instance() or QApplication([])
        window = ModernApp()
        try:
            window.sync_search_ui_state(
                query='category:"Kicks"',
                active_saved_filters=set(),
                active_source_filters=set(),
                active_categories={"Kicks"},
                confidence_range=(0.0, 1.0),
            )
            self.assertTrue(window.library_tab.btn_save_search.isEnabled())

            window.library_tab.set_sort_index(0)
            self.assertTrue(window.library_tab.sort_carousel.is_active)
            self.assertEqual(window.library_tab.sort_carousel.active_values, {0})
        finally:
            if getattr(window, "engine", None):
                try:
                    window.engine.close()
                except Exception:
                    pass
            _close_qt_window(window, app)

    def test_library_sidebar_places_options_after_lists(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        from gui.main.launcher import ModernApp

        app = QApplication.instance() or QApplication([])
        window = ModernApp()
        try:
            window.show()
            sidebar = window.library_tab.sidebar
            sidebar.set_sources([Path("D:/Samples/Pack")])
            app.processEvents()
            sidebar._fit_inner_lists()
            app.processEvents()
            layout = sidebar.content_layout
            widgets = [
                layout.itemAt(i).widget()
                for i in range(layout.count())
                if layout.itemAt(i).widget() is not None
            ]
            self.assertEqual(layout.indexOf(sidebar.header_container), -1)
            self.assertIsNot(sidebar.header_container.parentWidget(), sidebar.sidebar_content)
            self.assertEqual(sidebar.sidebar_scroll.verticalScrollBarPolicy(), Qt.ScrollBarAlwaysOff)
            self.assertIsNot(sidebar.sidebar_edge_scrollbar.parentWidget(), sidebar.sidebar_scroll)
            self.assertEqual(sidebar.sidebar_edge_scrollbar.y(), 0)
            self.assertFalse(sidebar.sidebar_edge_scrollbar.isVisible())
            self.assertGreater(widgets.index(sidebar.options_section), widgets.index(sidebar.directories_section))
            self.assertGreater(widgets.index(sidebar.options_section), widgets.index(sidebar.saved_filters_section))
        finally:
            if getattr(window, "engine", None):
                try:
                    window.engine.close()
                except Exception:
                    pass
            _close_qt_window(window, app)

    def test_library_docked_and_build_search_rows_use_uniform_control_heights(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication, QPushButton
        from gui.main.launcher import ModernApp
        from gui.widgets.build_page import BuildPage

        class FakeSettings:
            def value(self, key, default="", type=None):
                return default

            def setValue(self, key, value):
                pass

        app = QApplication.instance() or QApplication([])
        window = ModernApp()
        record = PlanRecord(Path("D:/Samples/Pack/kick.wav"), "Pack", "Kicks", "Oneshots", "0.9")
        build_page = BuildPage(FakeSettings(), [record], [])
        try:
            window.show()
            build_page.show()

            self.assertEqual(window.library_tab.edit_search.height(), window.library_tab.btn_save_search.height())
            self.assertEqual(window.dock_view.edit_search.height(), window.dock_view.btn_save_search.height())
            browse = next(button for button in build_page.findChildren(QPushButton) if button.objectName() == "CompareBrowseButton")
            self.assertEqual(build_page.edit_target.height(), browse.height())
            self.assertTrue(hasattr(window.dock_view, "scroll_area"))
            self.assertEqual(window.dock_view.scroll_area.horizontalScrollBarPolicy(), Qt.ScrollBarAlwaysOff)
            self.assertEqual(window.dock_view.scroll_area.verticalScrollBarPolicy(), Qt.ScrollBarAlwaysOff)
            self.assertEqual(window.dock_view.scroll_content.minimumHeight(), 0)
        finally:
            build_page.deleteLater()
            if getattr(window, "engine", None):
                try:
                    window.engine.close()
                except Exception:
                    pass
            _close_qt_window(window, app)

    def test_type_toggle_uses_uniform_button_sizing_and_label(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        from gui.main.launcher import ModernApp

        app = QApplication.instance() or QApplication([])
        window = ModernApp()
        try:
            window.show()
            app.processEvents()
            toggle = window.library_tab.type_picker
            self.assertEqual(toggle.btn_oneshots.text(), "I")
            self.assertEqual(toggle.btn_oneshots.size(), toggle.btn_loops.size())
            self.assertEqual(toggle.btn_loops.size(), toggle.btn_all.size())
        finally:
            if getattr(window, "engine", None):
                try:
                    window.engine.close()
                except Exception:
                    pass
            _close_qt_window(window, app)

    def test_docked_source_filter_sync_keeps_filter_carousel_active(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        from gui.main.launcher import ModernApp

        app = QApplication.instance() or QApplication([])
        window = ModernApp()
        try:
            query = 'source:"D:/Samples/Pack"'
            window.dock_view.set_filters([("Dir: Pack", query)])

            window.sync_search_ui_state(
                query=query,
                active_saved_filters=set(),
                active_source_filters={query},
                active_categories=set(),
                confidence_range=(0.0, 1.0),
            )

            self.assertTrue(window.dock_view.filter_carousel.is_active)
            self.assertEqual(window.dock_view.filter_carousel.active_values, {query})
        finally:
            if getattr(window, "engine", None):
                try:
                    window.engine.close()
                except Exception:
                    pass
            _close_qt_window(window, app)

    def test_oneshot_type_filter_hides_loop_exclusive_categories(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        from gui.main.launcher import ModernApp

        app = QApplication.instance() or QApplication([])
        window = ModernApp()
        try:
            carousel = window.library_tab.category_carousel
            self.assertIn("Full Drums", [value for _name, value in carousel.options])

            carousel.set_active_values({"Full Drums"})
            window.library_tab.type_picker.btn_oneshots.click()
            app.processEvents()

            self.assertNotIn("Full Drums", [value for _name, value in carousel.options])
            self.assertFalse(carousel.is_active)

            window.library_tab.type_picker.btn_loops.click()
            app.processEvents()

            self.assertIn("Full Drums", [value for _name, value in carousel.options])
        finally:
            if getattr(window, "engine", None):
                try:
                    window.engine.close()
                except Exception:
                    pass
            _close_qt_window(window, app)

    def test_library_view_menu_controls_toggle_and_prewarm(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        from PySide6.QtCore import QSettings
        from gui.main.launcher import ModernApp

        app = QApplication.instance() or QApplication([])
        settings = QSettings("UmU", "Unshuffle")
        settings.remove("library_view_modes_json")
        window = ModernApp()
        try:
            window.set_library_view_available("map", False)
            window.set_library_view_available("tree", False)

            self.assertFalse(window.library_tab.is_view_available("map"))
            self.assertFalse(window.library_tab.is_view_available("tree"))
            self.assertTrue(window.library_tab.is_view_available("table"))
            self.assertFalse(window.library_tab.btn_map_view.isVisible())
            self.assertFalse(window.library_tab.btn_tree_view.isVisible())
            self.assertIsNone(window.library_tab.coherence_map)

            with mock.patch.object(window.view_controller, "refresh_library_map") as refresh_map, \
                 mock.patch.object(window.view_controller, "_prewarm_library_tree_now") as prewarm_tree:
                window.view_controller.frontload_library_views()

            refresh_map.assert_not_called()
            prewarm_tree.assert_not_called()

            window.set_library_view_available("table", False)
            self.assertTrue(window.library_tab.is_view_available("table"))
            self.assertTrue(window.custom_menu_bar.library_view_actions["table"].isChecked())
        finally:
            settings.remove("library_view_modes_json")
            settings.sync()
            if getattr(window, "engine", None):
                try:
                    window.engine.close()
                except Exception:
                    pass
            _close_qt_window(window, app)

    def test_library_map_is_created_lazily_when_enabled_and_opened(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        from PySide6.QtCore import QSettings
        from gui.main.launcher import ModernApp

        app = QApplication.instance() or QApplication([])
        settings = QSettings("UmU", "Unshuffle")
        settings.remove("current_page")
        settings.remove("library_view_modes_json")
        window = ModernApp()
        try:
            self.assertIsNone(window.library_tab.coherence_map)
            window.view_controller.set_view_mode("map")
            self.assertIsNotNone(window.library_tab.coherence_map)
            self.assertEqual(window.library_tab.current_view_mode(), "map")
        finally:
            settings.remove("current_page")
            settings.remove("library_view_modes_json")
            settings.sync()
            if getattr(window, "engine", None):
                try:
                    window.engine.close()
                except Exception:
                    pass
            _close_qt_window(window, app)

    def test_library_table_column_visibility_persistence(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        from PySide6.QtCore import QSettings
        from gui.main.launcher import ModernApp
        from gui.utils.constants import StagingColumn

        app = QApplication.instance() or QApplication([])
        settings = QSettings("UmU", "Unshuffle")
        # Remove any existing settings to run clean
        for col in StagingColumn:
            settings.remove(f"table_column_visible_{col.name}")
            settings.remove(f"table_column_visible_user_set_{col.name}")
        settings.sync()

        window = ModernApp()
        from gui.models.staging_table import StagingTableModel
        source_model = StagingTableModel([], undo_stack=None, sync_callback=None)
        window.proxy_model.setSourceModel(source_model)
        window.library_tab.set_proxy_model(window.proxy_model)
        
        try:
            # Initially, StagingColumn.TYPE and StagingColumn.PATH are hidden
            self.assertTrue(window.library_tab.view_table.isColumnHidden(StagingColumn.TYPE))
            self.assertTrue(window.library_tab.view_table.isColumnHidden(StagingColumn.PATH))

            # Toggle TYPE to be visible
            window.library_tab.set_column_visible(StagingColumn.TYPE, True)
            self.assertFalse(window.library_tab.view_table.isColumnHidden(StagingColumn.TYPE))

            # Toggle it off again; explicit user choices should persist across model/view refreshes.
            window.library_tab.set_column_visible(StagingColumn.TYPE, False)
            self.assertTrue(window.library_tab.view_table.isColumnHidden(StagingColumn.TYPE))

            # Toggle CONFIDENCE to be hidden
            window.library_tab.set_column_visible(StagingColumn.CONFIDENCE, False)
            self.assertTrue(window.library_tab.view_table.isColumnHidden(StagingColumn.CONFIDENCE))

            # Re-set proxy model, verify settings are restored correctly
            window.library_tab.set_proxy_model(window.proxy_model)
            self.assertTrue(window.library_tab.view_table.isColumnHidden(StagingColumn.TYPE))
            self.assertTrue(window.library_tab.view_table.isColumnHidden(StagingColumn.PATH))
            self.assertTrue(window.library_tab.view_table.isColumnHidden(StagingColumn.CONFIDENCE))

            # Guard against late Qt/model lifecycle events making these columns visible again.
            window.library_tab.view_table.setColumnHidden(StagingColumn.TYPE, False)
            window.library_tab.view_table.setColumnHidden(StagingColumn.PATH, False)
            window.library_tab._apply_proportional_column_widths()
            self.assertTrue(window.library_tab.view_table.isColumnHidden(StagingColumn.TYPE))
            self.assertTrue(window.library_tab.view_table.isColumnHidden(StagingColumn.PATH))
        finally:
            for col in StagingColumn:
                settings.remove(f"table_column_visible_{col.name}")
                settings.remove(f"table_column_visible_user_set_{col.name}")
            settings.sync()
            if getattr(window, "engine", None):
                try:
                    window.engine.close()
                except Exception:
                    pass
            _close_qt_window(window, app)

    def test_default_hidden_columns_ignore_stale_visible_settings_without_user_marker(self):
        from gui.widgets.library_columns import load_column_visibility, save_column_visibility
        from gui.utils.constants import StagingColumn
        from PySide6.QtCore import QSettings

        settings = QSettings("UmU", "Unshuffle")
        try:
            for col in StagingColumn:
                settings.remove(f"table_column_visible_{col.name}")
                settings.remove(f"table_column_visible_user_set_{col.name}")
            settings.setValue("table_column_visible_TYPE", True)
            settings.setValue("table_column_visible_PATH", True)
            settings.sync()

            self.assertFalse(load_column_visibility(StagingColumn.TYPE))
            self.assertFalse(load_column_visibility(StagingColumn.PATH))

            save_column_visibility(StagingColumn.TYPE, True)
            self.assertTrue(load_column_visibility(StagingColumn.TYPE))
            self.assertFalse(load_column_visibility(StagingColumn.PATH))
        finally:
            for col in StagingColumn:
                settings.remove(f"table_column_visible_{col.name}")
                settings.remove(f"table_column_visible_user_set_{col.name}")
            settings.sync()

