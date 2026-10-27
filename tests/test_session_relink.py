import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from unittest import mock

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication
import pytest

from gui.core.data_manager import DataManager
from gui.core.session_relink import prepare_saved_session_sources
from gui.core.workers import SessionLoadWorker, StartupRestoreWorker
from unshuffle.persistence import UnshuffleDB


@pytest.fixture
def qt_app(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


@pytest.fixture
def saved_session(tmp_path):
    old = tmp_path / "old-samples"
    new = tmp_path / "new-samples"
    new.mkdir()
    (new / "kick.wav").write_bytes(b"sample")
    db = UnshuffleDB(tmp_path / "session.db")
    for sid in ("selected", "other"):
        db.register_session(sid, old, old, "pending")
        db.set_session_sources(sid, [old])
        db.conn.execute(
            """INSERT INTO staging_records
            (row_id, session_id, source_path, sample_name, pack, category, audio_type,
             preserved_root, is_preserved, hash, feature_vector, evidence_json)
            VALUES (1, ?, ?, 'kick.wav', 'Pack', 'Kicks', 'Oneshots', ?, 1, 'hash', ?, ?)""",
            (sid, (old / "kick.wav").as_posix(), old.as_posix(), b"saved-analysis",
             json.dumps({"duplicate_shadow": {"duplicate_of_path": str(old / "original.wav")}, "keep": 42})),
        )
    db.conn.commit()
    db.upsert_coherence_review_decisions("selected", [{
        "source_path": str(old / "kick.wav"), "file_hash": "hash", "decision_type": "ignore",
    }])
    app = SimpleNamespace()
    app.data_manager = DataManager(app=app)
    payload = {
        "session_id": "selected", "target": str(old), "sources": [str(old)],
        "record_count": 1, "db_path": str(db.db_path), "db_scope": "local",
    }
    try:
        yield SimpleNamespace(old=old, new=new, db=db, app=app, payload=payload)
    finally:
        db.close()


def test_relink_preserves_analysis_rows_other_sessions_and_decisions(saved_session, qt_app):
    s = saved_session
    before = s.db.get_staging_records("selected")[0]
    with mock.patch.object(s.app.data_manager, "_prompt_source_remaps", return_value={str(s.old): s.new}) as prompt:
        result = prepare_saved_session_sources(s.app, s.payload)
    prompt.assert_called_once_with([str(s.old)], parent_widget=s.app)
    assert result is not None
    assert result["sources"] == [str(s.new)]
    assert Path(result["target"]) == s.new
    assert result["db_scope"] == "local"
    row = s.db.get_staging_records("selected")[0]
    assert Path(row["source_path"]) == s.new / "kick.wav"
    assert Path(row["preserved_root"]) == s.new
    evidence = json.loads(row["evidence_json"])
    assert Path(evidence["duplicate_shadow"]["duplicate_of_path"]) == s.new / "original.wav"
    assert evidence["keep"] == 42
    for key in ("id", "row_id", "hash", "feature_vector", "category", "is_preserved"):
        assert row[key] == before[key]
    assert Path(s.db.get_staging_records("other")[0]["source_path"]) == s.old / "kick.wav"
    assert [Path(p) for p in s.db.get_session_sources("selected")] == [s.new]
    assert [Path(p) for p in s.db.get_session_sources("other")] == [s.old]
    decisions = s.db.conn.execute("SELECT source_path FROM coherence_review_decisions").fetchall()
    assert {Path(r[0]) for r in decisions} == {s.old / "kick.wav", s.new / "kick.wav"}
    assert not s.old.exists()
    assert (s.new / "kick.wav").read_bytes() == b"sample"
    with mock.patch.object(s.app.data_manager, "_prompt_source_remaps") as prompt:
        assert prepare_saved_session_sources(s.app, result) is result
    prompt.assert_not_called()

    # Playback becomes available with the existing selection/preview code, without a scan.
    from gui.core.audio_controller import AudioController
    from gui.widgets.preview_control_bar import PreviewControlBar
    from unshuffle.core import parse_tags, plan_record_from_staging_row
    from tests.utils.qt_utils import close_qt_window
    bar = PreviewControlBar()
    controller = AudioController(bar)
    try:
        controller.handle_selection(plan_record_from_staging_row(before, parse_tags), None, None)
        assert not bar.btn_play_pause.isEnabled()
        controller.handle_selection(plan_record_from_staging_row(row, parse_tags), None, None)
        assert bar.btn_play_pause.isEnabled()
    finally:
        close_qt_window(bar, qt_app)


def test_existing_root_still_does_not_prompt_for_missing_files(saved_session):
    s = saved_session
    s.old.mkdir()
    with mock.patch.object(s.app.data_manager, "_prompt_source_remaps") as prompt:
        assert prepare_saved_session_sources(s.app, s.payload) is s.payload
    prompt.assert_not_called()
    assert not (s.old / "kick.wav").exists()


def test_cancel_second_root_does_not_change_any_saved_paths(saved_session):
    s = saved_session
    payload = dict(s.payload, sources=[str(s.old), str(s.old / "second")])
    before = s.db.get_staging_records("selected")
    with mock.patch("gui.core.data_manager.QMessageBox.information"), \
         mock.patch("gui.core.data_manager.QFileDialog.getExistingDirectory", side_effect=[str(s.new), ""]):
        assert prepare_saved_session_sources(s.app, payload) is None
    assert s.db.get_staging_records("selected") == before
    assert [Path(p) for p in s.db.get_session_sources("selected")] == [s.old]


def test_relink_rolls_back_all_changes_if_a_write_fails(saved_session):
    s = saved_session
    before = s.db.get_staging_records("selected")
    s.db.conn.execute("CREATE TRIGGER reject_relink BEFORE UPDATE ON sessions BEGIN SELECT RAISE(ABORT, 'failed'); END")
    s.db.conn.commit()
    with mock.patch.object(s.app.data_manager, "_prompt_source_remaps", return_value={str(s.old): s.new}), \
         pytest.raises(sqlite3.IntegrityError, match="failed"):
        prepare_saved_session_sources(s.app, s.payload)
    assert s.db.get_staging_records("selected") == before
    assert [Path(p) for p in s.db.get_session_sources("selected")] == [s.old]
    assert s.db.conn.execute("SELECT COUNT(*) FROM coherence_review_decisions").fetchone()[0] == 1


def test_relink_keeps_an_existing_decision_at_replacement_path(saved_session):
    s = saved_session
    s.db.upsert_coherence_review_decisions("other", [{
        "source_path": str(s.new / "kick.wav"), "file_hash": "hash", "decision_type": "keep-newer",
    }])
    with mock.patch.object(s.app.data_manager, "_prompt_source_remaps", return_value={str(s.old): s.new}):
        prepare_saved_session_sources(s.app, s.payload)
    row = s.db.conn.execute("SELECT decision_type FROM coherence_review_decisions WHERE source_path = ?", ((s.new / "kick.wav").as_posix(),)).fetchone()
    assert row[0] == "keep-newer"


def test_relink_handles_multiple_batches_without_dropping_missing_files(saved_session):
    s = saved_session
    s.db.conn.executemany(
        "INSERT INTO staging_records (row_id, session_id, source_path, sample_name) VALUES (?, 'selected', ?, ?)",
        [(i, (s.old / f"sample-{i}.wav").as_posix(), f"sample-{i}.wav") for i in range(2, 1004)],
    )
    s.db.conn.commit()
    with mock.patch.object(s.app.data_manager, "_prompt_source_remaps", return_value={str(s.old): s.new}):
        prepare_saved_session_sources(s.app, s.payload)
    records = s.db.get_staging_records("selected")
    assert len(records) == 1003
    assert {r["row_id"] for r in records} == set(range(1, 1004))
    assert all(Path(r["source_path"]).parent == s.new for r in records)


def test_relink_does_not_create_a_missing_database(saved_session, tmp_path):
    s = saved_session
    missing = tmp_path / "missing.db"
    with mock.patch.object(s.app.data_manager, "_prompt_source_remaps", return_value={str(s.old): s.new}), \
         pytest.raises(ValueError, match="database is unavailable"):
        prepare_saved_session_sources(s.app, dict(s.payload, db_path=str(missing)))
    assert not missing.exists()


@pytest.mark.parametrize("worker_class", [SessionLoadWorker, StartupRestoreWorker])
def test_loaders_report_the_database_actually_selected(saved_session, monkeypatch, worker_class):
    s = saved_session
    monkeypatch.setattr("gui.core.workers._open_restore_db", lambda *a, **kw: (s.db, "selected", "local", False))
    worker = worker_class(str(s.new), "selected")
    payloads, errors = [], []
    worker.finished.connect(payloads.append)
    worker.error.connect(errors.append)
    worker.run()
    assert not errors
    assert payloads[0]["db_path"] == str(s.db.db_path)
    assert payloads[0]["db_scope"] == "local"


@pytest.mark.parametrize("entry", ["restore", "recent"])
@pytest.mark.parametrize("cancel", [False, True])
def test_session_entry_points_relink_before_attaching_or_leave_current_session(saved_session, qt_app, monkeypatch, entry, cancel):
    s = saved_session
    from gui.core.workflow_controller import WorkflowController
    from gui.main.actions import session as session_actions
    old_engine = mock.Mock()

    class App(QObject):
        def __init__(self):
            super().__init__()
            self.data_manager = s.app.data_manager
            self.settings = SimpleNamespace(value=lambda key, default="": {
                "last_target": str(s.old), "last_scan_session_id": "selected",
            }.get(key, default))
            self.footer = mock.Mock()
            self.undo_stack = mock.Mock()
            self.worker_manager = mock.Mock()
            self.search_controller = mock.Mock()
            self.engine = old_engine

    app = App()
    engine = SimpleNamespace(db=s.db, local_db=s.db, session_source_roots=[], close=mock.Mock())

    class Loader(QObject):
        finished = Signal(dict)
        error = Signal(str)

        def __init__(self, *args, **kwargs):
            super().__init__()

        def start(self):
            self.finished.emit(dict(s.payload))

        def deleteLater(self):
            pass

    monkeypatch.setattr(s.app.data_manager, "_prompt_source_remaps", lambda *a, **kw: None if cancel else {str(s.old): s.new})
    if entry == "restore":
        monkeypatch.setattr("gui.core.workers.StartupRestoreWorker", Loader)
        factory = mock.Mock(return_value=engine)
        monkeypatch.setattr("gui.core.workflow_controller.create_workflow_bridge", factory)
        controller = WorkflowController(old_engine, app.worker_manager, app.undo_stack, app)
        controller.finalize_scan_data = mock.Mock()
        finished = []
        controller.restoreFinished.connect(finished.append)
        controller.restore_session()
        finalize = controller.finalize_scan_data
        if cancel:
            assert finished == [False]
    else:
        monkeypatch.setattr("gui.core.workers.SessionLoadWorker", Loader)
        factory = mock.Mock(return_value=engine)
        monkeypatch.setattr(session_actions, "create_workflow_bridge", factory)
        app.workflow_controller = mock.Mock()
        session_actions.load_staging_session(app, {"session_id": "selected"})
        finalize = app.workflow_controller.finalize_scan_data

    if cancel:
        finalize.assert_not_called()
        factory.assert_not_called()
        assert app.engine is old_engine
        old_engine.close.assert_not_called()
        assert Path(s.db.get_staging_records("selected")[0]["source_path"]) == s.old / "kick.wav"
    else:
        factory.assert_called_once_with(s.new, session_id="selected")
        finalize.assert_called_once()
        assert engine.session_source_roots == [s.new]
        assert Path(s.db.get_staging_records("selected")[0]["source_path"]).exists()
