from pathlib import Path
from types import MethodType, SimpleNamespace
from typing import cast
from unittest import mock

from gui.core.data_manager import DataManager
from gui.core.workers import SessionLoadWorker
from gui.core.workflow_controller import WorkflowController
from gui.utils.history import (
    database_handles_for_target,
    invalidate_history_cache,
    load_executed_sessions,
)
from unshuffle.core.paths import DB_FILE_NAME, SYSTEM_FOLDER_NAME


class _LiveConnection:
    def execute(self, *_args, **_kwargs):
        return self

    def fetchone(self):
        return (1,)


class _BorrowedDatabase:
    def __init__(self, db_path: Path, sessions=None):
        self.db_path = db_path
        self.conn = _LiveConnection()
        self.sessions = list(sessions or [])
        self.closed = False

    def get_recent_sessions(self, limit=10, **_kwargs):
        return list(self.sessions[:limit])

    def get_staging_records(self, session_id):
        return [{"row_id": 1}] if session_id == "session-one" else []

    def get_session_sources(self, session_id):
        return ["D:/Source"] if session_id == "session-one" else []

    def close(self):
        self.closed = True


def test_data_manager_reuses_active_engine_databases_without_closing_them(tmp_path: Path):
    target = tmp_path / "library"
    target.mkdir()
    global_db = _BorrowedDatabase(tmp_path / "global.db")
    local_db = _BorrowedDatabase(target / SYSTEM_FOLDER_NAME / DB_FILE_NAME)
    runtime = SimpleNamespace(target_dir=target, db=global_db, local_db=local_db)
    manager = DataManager(runtime)

    with mock.patch("unshuffle.persistence.get_local_db") as open_local, mock.patch(
        "unshuffle.persistence.get_db"
    ) as open_global:
        assert manager.check_and_sync_local_db(target) is False

    open_local.assert_not_called()
    open_global.assert_not_called()
    assert local_db.closed is False
    assert global_db.closed is False


def test_session_load_worker_reuses_borrowed_database_without_closing_it(tmp_path: Path):
    target = tmp_path / "library"
    local_path = target / SYSTEM_FOLDER_NAME / DB_FILE_NAME
    local_path.parent.mkdir(parents=True)
    local_path.touch()
    local_db = _BorrowedDatabase(local_path)
    global_db = _BorrowedDatabase(tmp_path / "global.db")
    payloads = []
    worker = SessionLoadWorker(
        target,
        "session-one",
        local_db=local_db,
        global_db=global_db,
    )
    worker.finished.connect(payloads.append)

    with mock.patch("unshuffle.persistence.get_local_db") as open_local, mock.patch(
        "unshuffle.persistence.get_db"
    ) as open_global:
        worker.run()

    open_local.assert_not_called()
    open_global.assert_not_called()
    assert payloads[0]["db_scope"] == "local"
    assert payloads[0]["record_count"] == 1
    assert local_db.closed is False
    assert global_db.closed is False


def test_same_target_build_reuses_engine_database():
    database = _BorrowedDatabase(Path("active.db"))
    records = SimpleNamespace(store=SimpleNamespace(db=database, session_id="session-one"))
    controller = cast(
        WorkflowController,
        SimpleNamespace(
            _engine=SimpleNamespace(db=database, local_db=None),
            _detached_build_db=None,
        ),
    )
    controller._close_detached_build_db = MethodType(
        WorkflowController._close_detached_build_db,
        controller,
    )

    result = WorkflowController._build_record_source(
        controller,
        records,
        reuse_database=True,
    )

    assert result.store.db is database
    assert controller._detached_build_db is None
    assert database.closed is False


def test_changed_target_build_keeps_independent_database():
    active_database = _BorrowedDatabase(Path("active.db"))
    detached_database = _BorrowedDatabase(Path("detached.db"))
    records = SimpleNamespace(store=SimpleNamespace(db=active_database, session_id="session-one"))
    controller = cast(
        WorkflowController,
        SimpleNamespace(
            _engine=SimpleNamespace(db=active_database, local_db=None),
            _detached_build_db=None,
        ),
    )
    controller._close_detached_build_db = MethodType(
        WorkflowController._close_detached_build_db,
        controller,
    )

    with mock.patch("unshuffle.persistence.UnshuffleDB", return_value=detached_database) as open_database:
        result = WorkflowController._build_record_source(
            controller,
            records,
            reuse_database=False,
        )

    open_database.assert_called_once_with(active_database.db_path)
    assert result.store.db is detached_database
    assert controller._detached_build_db is detached_database
    assert active_database.closed is False


def test_history_queries_reuse_matching_engine_databases(tmp_path: Path):
    target = tmp_path / "library"
    local_path = target / SYSTEM_FOLDER_NAME / DB_FILE_NAME
    local_path.parent.mkdir(parents=True)
    local_path.touch()
    global_db = _BorrowedDatabase(
        tmp_path / "global.db",
        [{"session_id": "global-session", "timestamp": "2026-01-01"}],
    )
    local_db = _BorrowedDatabase(
        local_path,
        [{"session_id": "local-session", "timestamp": "2025-01-01"}],
    )
    engine = SimpleNamespace(target_dir=target, db=global_db, local_db=local_db)
    handles = database_handles_for_target(engine, target)
    invalidate_history_cache(str(target))

    with mock.patch("gui.utils.history.get_db") as open_global, mock.patch(
        "gui.utils.history.get_local_db"
    ) as open_local:
        sessions = load_executed_sessions(str(target), limit=10, **handles)

    open_global.assert_not_called()
    open_local.assert_not_called()
    assert [session["session_id"] for session in sessions] == ["global-session", "local-session"]
    assert global_db.closed is False
    assert local_db.closed is False


def test_local_primary_database_is_not_mistaken_for_global(tmp_path: Path):
    target = tmp_path / "library"
    local_path = target / SYSTEM_FOLDER_NAME / DB_FILE_NAME
    local_database = _BorrowedDatabase(local_path)
    engine = SimpleNamespace(target_dir=target, db=local_database, local_db=local_database)

    handles = database_handles_for_target(engine, target)

    assert handles == {"local_db": local_database}
