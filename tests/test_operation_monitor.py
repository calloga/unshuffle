from PySide6.QtCore import Qt


def test_operation_monitor_renders_determinate_progress(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from gui.widgets.operation_monitor import OperationMonitorDialog

    _app = QApplication.instance() or QApplication([])
    dialog = OperationMonitorDialog()
    dialog.start("Scanning Library", cancellable=True)

    dialog.update_progress(
        {
            "phase": "Hashing",
            "message": "Hashing samples...",
            "current": 25,
            "total": 100,
            "eta_seconds": 65,
        }
    )

    assert dialog.title_label.text() == "Scanning Library"
    assert dialog.phase_label.text() == "Hashing"
    assert dialog.detail_label.text() == "Hashing samples..."
    assert not dialog.detail_label.isHidden()
    assert dialog.progress.minimum() == 0
    assert dialog.progress.maximum() == 100
    assert dialog.progress.value() == 25
    assert dialog.progress.format() == "%p%"
    assert dialog.eta_label.text() == "Remaining Time: 1m 05s"
    dialog.finish()


def test_operation_monitor_hides_repeated_phase_and_missing_eta(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from gui.widgets.operation_monitor import OperationMonitorDialog

    _app = QApplication.instance() or QApplication([])
    dialog = OperationMonitorDialog()
    dialog.start("Scanning Library")

    dialog.update_progress(
        {
            "phase": "Classifying Samples",
            "message": "Classifying samples...",
            "percent": 88,
            "eta_seconds": None,
        }
    )

    assert dialog.detail_label.text() == ""
    assert dialog.detail_label.isHidden()
    assert dialog.eta_label.text() == ""
    assert dialog.eta_label.isHidden()
    assert dialog.progress.maximum() == 100
    assert dialog.progress.value() == 88
    dialog.finish()


def test_operation_monitor_completes_previous_phase_before_phase_change(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from gui.widgets.operation_monitor import OperationMonitorDialog

    _app = QApplication.instance() or QApplication([])
    dialog = OperationMonitorDialog()
    dialog.start("Scanning Library")
    dialog.update_progress({"phase": "Hashing", "current": 25, "total": 100})

    dialog.update_progress({"phase": "Creating Session", "current": 1, "total": 4})

    assert dialog.phase_label.text() == "Hashing"
    assert dialog._progress_animation.endValue() == 100
    dialog.finish()


def test_operation_monitor_cancel_and_close_guard(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QCloseEvent
    from PySide6.QtWidgets import QApplication
    from gui.widgets.operation_monitor import OperationMonitorDialog

    _app = QApplication.instance() or QApplication([])
    calls = []
    dialog = OperationMonitorDialog()
    dialog.start("Building Library", cancellable=True, on_cancel=lambda: calls.append("cancel"))

    dialog.btn_cancel.click()
    active_close = QCloseEvent()
    dialog.closeEvent(active_close)

    assert calls == ["cancel"]
    assert active_close.isAccepted() is False

    dialog.finish()
    inactive_close = QCloseEvent()
    dialog.closeEvent(inactive_close)
    assert inactive_close.isAccepted() is True


def test_operation_monitor_disables_cancellation_during_finalization(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from gui.widgets.operation_monitor import OperationMonitorDialog

    _app = QApplication.instance() or QApplication([])
    calls = []
    dialog = OperationMonitorDialog()
    dialog.start("Building Library", cancellable=True, on_cancel=lambda: calls.append("cancel"))

    dialog.update_progress(
        {
            "phase": "Finalizing Build",
            "message": "Cleaning up temporary scan data...",
            "cancellable": False,
        }
    )

    assert dialog.btn_cancel.isHidden()
    assert not dialog.btn_cancel.isEnabled()
    dialog.btn_cancel.click()
    assert calls == []
    dialog.finish()


def test_operation_monitor_manager_ignores_stale_progress(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QWidget
    from gui.core.operation_monitor import OperationMonitorManager

    app = QApplication.instance() or QApplication([])
    assert app is not None
    parent = QWidget()
    manager = OperationMonitorManager(parent)

    first = manager.start("Scanning Library")
    manager.finish(token=first)
    second = manager.start("Building Library")
    manager.update({"phase": "Hashing", "current": 50, "total": 100}, token=first)

    assert manager.dialog.title_label.text() == "Building Library"
    assert manager.dialog.phase_label.text() == ""

    manager.update({"phase": "Copying", "current": 50, "total": 100}, token=second)
    assert manager.dialog.phase_label.text() == "Copying"
    manager.finish(token=second)
    parent.close()
