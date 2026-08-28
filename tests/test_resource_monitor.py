from __future__ import annotations

from unittest.mock import Mock, patch

from gui.core.workers import _report_scan_timing
from unshuffle.core.resource_monitor import ResourceMonitor


def test_resource_monitor_accumulates_distinct_phase_durations() -> None:
    thread = Mock()
    monitor = ResourceMonitor("scan-refresh")

    with (
        patch("unshuffle.core.resource_monitor.threading.Thread", return_value=thread),
        patch(
            "unshuffle.core.resource_monitor.time.monotonic",
            side_effect=[10.0, 12.0, 17.0, 20.0],
        ),
    ):
        monitor.start()
        monitor.set_phase("Discovering Samples")
        monitor.set_phase("Analyzing Audio Features")
        summary = monitor.stop()

    assert summary["operation"] == "scan-refresh"
    assert summary["elapsed_seconds"] == 10.0
    assert summary["phase_seconds"] == {
        "Analyzing Audio Features": 3.0,
        "Discovering Samples": 5.0,
        "starting": 2.0,
    }
    thread.start.assert_called_once_with()
    thread.join.assert_called_once()


def test_resource_monitor_repeated_phase_does_not_reset_timer() -> None:
    thread = Mock()
    monitor = ResourceMonitor("scan-fresh")

    with (
        patch("unshuffle.core.resource_monitor.threading.Thread", return_value=thread),
        patch(
            "unshuffle.core.resource_monitor.time.monotonic",
            side_effect=[5.0, 7.0, 11.0],
        ),
    ):
        monitor.start()
        monitor.set_phase("Discovering Samples")
        monitor.set_phase("Discovering Samples")
        summary = monitor.stop()

    assert summary["elapsed_seconds"] == 6.0
    assert summary["phase_seconds"] == {
        "Discovering Samples": 4.0,
        "starting": 2.0,
    }


def test_scan_timing_is_written_to_persistent_diagnostic_log() -> None:
    engine = Mock()
    summary = {
        "operation": "scan-refresh",
        "elapsed_seconds": 12.5,
        "phase_seconds": {"Discovering Samples": 2.0},
    }

    with patch("gui.core.workers.write_launcher_event_log") as write_event:
        _report_scan_timing(engine, summary)

    engine.log.assert_called_once_with(f"Scan performance timing: {summary}")
    write_event.assert_called_once_with("scan-performance-timing", **summary)
