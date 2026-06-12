import json
import pytest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from unshuffle import cli


def test_cli_version_reports_v1_release(capsys):
    with mock.patch("sys.argv", ["unshuffle", "--version"]):
        with pytest.raises(SystemExit) as exc_info:
            cli.main()

    assert exc_info.value.code == 0
    assert "1.0.0" in capsys.readouterr().out


def _base_args(**overrides):
    data = {
        "source": ["SourceA"],
        "output": "LibraryOut",
        "pack_name": None,
        "move": False,
        "flat": False,
        "no_prefix": False,
        "dry_run": False,
        "rebuild_cache": False,
        "force_cache_reset": False,
        "yes": False,
        "undo": False,
        "session_id": None,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def test_cli_main_uses_workflow_bridge_for_plan_execution():
    workflow = mock.Mock()
    workflow.session_id = "session-1"
    workflow.prepare_plan.return_value = ["plan-record"]
    workflow.execute_plan.return_value = {
        "error": None,
        "interrupted": False,
        "total": 0,
        "copied": 0,
        "duplicates": 0,
        "dry_run": False,
        "move": False,
        "report_path": None,
    }

    with mock.patch("unshuffle.cli.argparse.ArgumentParser.parse_args", return_value=_base_args()):
        with mock.patch("unshuffle.cli.create_workflow_bridge", return_value=workflow) as create_bridge:
            with mock.patch("unshuffle.cli.setup_logging") as setup_logging:
                exit_code = cli.main()

    assert exit_code == 0
    create_bridge.assert_called_once()
    setup_logging.assert_called_once_with(Path("LibraryOut").resolve(), False, "session-1")
    workflow.prepare_plan.assert_called_once_with([Path("SourceA")], pack_name_override=None)
    workflow.execute_plan.assert_called_once_with(
        ["plan-record"],
        move=False,
        dry_run=False,
        flat=False,
        no_prefix=False,
    )
    workflow.close.assert_called_once()


def test_cli_main_uses_workflow_bridge_for_undo():
    workflow = mock.Mock()
    workflow.session_id = "session-1"
    workflow.db.get_recent_sessions.return_value = [{"session_id": "latest-session"}]
    workflow.undo_session.return_value = {"undone": 3}

    with mock.patch("unshuffle.cli.argparse.ArgumentParser.parse_args", return_value=_base_args(source=None, undo=True)):
        with mock.patch("unshuffle.cli.create_workflow_bridge", return_value=workflow) as create_bridge:
            with mock.patch("unshuffle.cli.setup_logging") as setup_logging:
                with mock.patch("builtins.print"):
                    exit_code = cli.main()

    assert exit_code == 0
    create_bridge.assert_called_once()
    setup_logging.assert_called_once_with(Path("LibraryOut").resolve(), False, "session-1")
    workflow.db.get_recent_sessions.assert_called_once_with(1, only_executed=True, target_root=Path("LibraryOut").resolve())
    workflow.undo_session.assert_called_once_with("latest-session")
    workflow.close.assert_called_once()


def test_cli_main_yes_flag_bypasses_interactive_cache_prompt():
    workflow = mock.Mock()
    workflow.session_id = "session-1"
    workflow.load_cache.side_effect = [json.JSONDecodeError("bad", "", 0), None]
    workflow.prepare_plan.return_value = []
    workflow.execute_plan.return_value = {
        "error": None,
        "interrupted": False,
        "total": 0,
        "copied": 0,
        "duplicates": 0,
        "dry_run": False,
        "move": False,
        "report_path": None,
    }

    with mock.patch("unshuffle.cli.argparse.ArgumentParser.parse_args", return_value=_base_args(rebuild_cache=True, yes=True)):
        with mock.patch("unshuffle.cli.create_workflow_bridge", return_value=workflow):
            with mock.patch("unshuffle.cli.setup_logging"):
                with mock.patch("builtins.input") as prompt:
                    exit_code = cli.main()

    assert exit_code == 0
    prompt.assert_not_called()
    assert workflow.load_cache.call_count == 2
