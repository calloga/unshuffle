from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from unshuffle.bridge.search_bridge import SearchBridge


def test_search_bridge_diagnose_file_uses_workflow_db_by_default():
    workflow = SimpleNamespace(db=mock.sentinel.db, session_source_root=None)
    bridge = SearchBridge(workflow)

    with mock.patch("unshuffle.bridge.search_bridge.diagnose_path", return_value=mock.sentinel.diagnosis) as diagnose_mock:
        result = bridge.diagnose_file("Library/Kicks/kick.wav", scan_root="Library")

    assert result is mock.sentinel.diagnosis
    diagnose_mock.assert_called_once_with(
        Path("Library/Kicks/kick.wav"),
        scan_root=Path("Library"),
        db=mock.sentinel.db,
    )


def test_search_bridge_diagnose_file_defaults_scan_root_from_workflow():
    workflow = SimpleNamespace(db=mock.sentinel.db, session_source_root=Path("Library"))
    bridge = SearchBridge(workflow)

    with mock.patch("unshuffle.bridge.search_bridge.diagnose_path", return_value=mock.sentinel.diagnosis) as diagnose_mock:
        result = bridge.diagnose_file("Library/Kicks/kick.wav")

    assert result is mock.sentinel.diagnosis
    diagnose_mock.assert_called_once_with(
        Path("Library/Kicks/kick.wav"),
        scan_root=Path("Library"),
        db=mock.sentinel.db,
    )


def test_search_bridge_format_diagnosis_proxies_formatter():
    with mock.patch("unshuffle.bridge.search_bridge.format_file_diagnosis", return_value="report") as format_mock:
        result = SearchBridge.format_diagnosis(mock.sentinel.diagnosis)

    assert result == "report"
    format_mock.assert_called_once_with(mock.sentinel.diagnosis)
