from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from gui.core.data_manager import DataManager
from gui.dialogs.session_import_dialog import (
    SessionImportDialog,
    format_session_timestamp,
    session_display_name,
)
from unshuffle.persistence import UnshuffleDB


def _application() -> QApplication:
    existing = QApplication.instance()
    if isinstance(existing, QApplication):
        return existing
    return QApplication([])


def _choice(
    *,
    session_id: str = "csv_secret-id",
    sources: list[str] | None = None,
    count: int = 11019,
) -> dict:
    return {
        "session": {
            "session_id": session_id,
            "timestamp": "2026-09-01 16:21:43",
            "source_path": (sources or [r"D:\SAMPLES"])[0],
        },
        "record_count": count,
        "sources": sources or [r"D:\SAMPLES"],
    }


def test_session_dialog_replaces_raw_id_with_friendly_summary_and_all_sources() -> None:
    _application()
    choice = _choice(sources=[r"D:\SAMPLES", r"E:\Drum Kits"])
    dialog = SessionImportDialog([choice, _choice(session_id="second-session")])

    item_text = dialog.session_list.item(0).text()
    assert "SAMPLES + 1 more" in item_text
    assert "CSV import" in item_text
    assert "11,019 files" in item_text
    assert "Sep 1, 2026 at 4:21 PM" in item_text
    assert "csv_secret-id" not in item_text
    assert dialog.sources_heading.text() == "Source folders (2)"
    assert dialog.sources_label.text() == "D:\\SAMPLES\nE:\\Drum Kits"
    assert dialog.import_button.text() == "Import"
    assert dialog.cancel_button.text() == "Cancel"


def test_session_dialog_collapses_and_expands_numerous_source_folders() -> None:
    _application()
    sources = [fr"D:\Library {index}" for index in range(7)]
    dialog = SessionImportDialog([_choice(sources=sources), _choice(session_id="second-session")])

    assert "and 3 more..." in dialog.sources_label.text()
    assert dialog.sources_toggle.isVisibleTo(dialog)
    dialog.sources_toggle.click()
    assert dialog.sources_label.text().splitlines() == sources
    assert dialog.sources_toggle.text() == "Show fewer"


def test_session_dialog_selection_returns_underlying_session_without_showing_its_id() -> None:
    _application()
    choices = [
        _choice(session_id="first-secret", sources=[r"D:\First"]),
        _choice(session_id="second-secret", sources=[r"E:\Second"]),
    ]
    dialog = SessionImportDialog(choices)

    dialog.session_list.setCurrentRow(1)

    assert dialog.selected_session() is choices[1]["session"]
    assert "second-secret" not in dialog.session_list.item(1).text()
    assert dialog.sources_label.text() == r"E:\Second"


def test_session_display_helpers_support_windows_roots_and_irregular_dates() -> None:
    assert session_display_name([r"D:\SAMPLES"]) == "SAMPLES"
    assert session_display_name(["D:\\"]) == "D:"
    assert format_session_timestamp("not-a-date") == "not-a-date"
    assert format_session_timestamp("") == "Date unavailable"


def test_session_import_choices_collect_counts_and_ordered_sources_without_id_batches(
    tmp_path: Path,
) -> None:
    database = UnshuffleDB(tmp_path / "sessions.db")
    try:
        for session_id, source in (
            ("first", Path(r"D:\First")),
            ("second", Path(r"E:\Second")),
        ):
            database.register_session(session_id, source, tmp_path, "pending")
        database.set_session_sources("first", [Path(r"D:\First"), Path(r"F:\Shared")])
        database.set_session_sources("second", [Path(r"E:\Second")])
        database.conn.executemany(
            "INSERT INTO staging_records (session_id, row_id, source_path, sample_name) "
            "VALUES (?, ?, ?, ?)",
            [
                ("first", 1, "one.wav", "one.wav"),
                ("first", 2, "two.wav", "two.wav"),
                ("second", 1, "three.wav", "three.wav"),
            ],
        )
        database.conn.commit()
        sessions = database.get_recent_sessions(10)

        choices = DataManager._session_import_choices(sessions, database)
        by_id = {choice["session"]["session_id"]: choice for choice in choices}

        assert by_id["first"]["record_count"] == 2
        assert by_id["first"]["sources"] == [r"D:\First", r"F:\Shared"]
        assert by_id["second"]["record_count"] == 1
        assert by_id["second"]["sources"] == [r"E:\Second"]
    finally:
        database.close()


def test_single_importable_session_skips_the_picker(tmp_path: Path) -> None:
    database = UnshuffleDB(tmp_path / "single.db")
    try:
        database.register_session("empty", Path(r"D:\Empty"), tmp_path, "pending")
        database.register_session("ready", Path(r"D:\Ready"), tmp_path, "pending")
        database.conn.execute(
            "INSERT INTO staging_records (session_id, row_id, source_path, sample_name) "
            "VALUES (?, ?, ?, ?)",
            ("ready", 1, "ready.wav", "ready.wav"),
        )
        database.conn.commit()
        sessions = database.get_recent_sessions(10)
        manager = DataManager(app=None)

        with mock.patch("gui.core.data_manager.SessionImportDialog") as picker:
            selected = manager._choose_import_session(sessions, database)

        assert selected is not None
        assert selected["session_id"] == "ready"
        picker.assert_not_called()
    finally:
        database.close()


def test_portable_import_uses_global_database_when_runtime_is_local(tmp_path: Path) -> None:
    local_db = SimpleNamespace(db_path=tmp_path / "local" / "unshuffle.db")
    global_db = SimpleNamespace(db_path=tmp_path / "global" / "unshuffle.db")

    with mock.patch("unshuffle.persistence.get_global_system_dir", return_value=tmp_path / "global"), \
         mock.patch("unshuffle.persistence.get_db", return_value=global_db) as get_db:
        selected, opened = DataManager._portable_import_destination_database(
            local_db,
            tmp_path / "library",
        )

    assert selected is global_db
    assert opened
    get_db.assert_called_once_with(tmp_path / "library")


def test_portable_import_reuses_existing_global_database(tmp_path: Path) -> None:
    global_db = SimpleNamespace(db_path=tmp_path / "global" / "unshuffle.db")

    with mock.patch("unshuffle.persistence.get_global_system_dir", return_value=tmp_path / "global"), \
         mock.patch("unshuffle.persistence.get_db") as get_db:
        selected, opened = DataManager._portable_import_destination_database(
            global_db,
            tmp_path / "library",
        )

    assert selected is global_db
    assert not opened
    get_db.assert_not_called()


def test_export_to_active_database_does_not_clear_its_source_rows(tmp_path: Path) -> None:
    from unshuffle.core.paths import get_local_system_dir

    export_root = tmp_path / "library"
    source_root = tmp_path / "source"
    export_root.mkdir()
    source_root.mkdir()
    sample = source_root / "kick.wav"
    sample.write_bytes(b"sample")
    database = UnshuffleDB(get_local_system_dir(export_root) / "unshuffle.db")
    session_id = "local-session"
    database.register_session(session_id, source_root, export_root, "pending")
    database.set_session_sources(session_id, [source_root])
    database.add_staging_records_bulk(
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
                "hash",
                "fast-hash",
                "[]",
                "{}",
                None,
                None,
                None,
                "ok",
                "[]",
                None,
                False,
            )
        ],
    )
    app = SimpleNamespace(
        drafting_controller=None,
        settings_controller=None,
        tree_organization_controller=None,
    )
    manager = DataManager(engine=SimpleNamespace(db=database, session_id=session_id), app=app)

    try:
        with mock.patch.object(manager, "_confirm_session_export", return_value=True), \
             mock.patch.object(manager, "_show_session_export_success"):
            exported = manager.export_session_to_folder(export_root)

        assert exported
        assert len(database.get_staging_records(session_id)) == 1
        database.conn.execute("SELECT 1").fetchone()
    finally:
        database.close()


def test_export_prefers_the_session_backing_the_visible_model(tmp_path: Path) -> None:
    visible_db = UnshuffleDB(tmp_path / "visible.db")
    stale_db = UnshuffleDB(tmp_path / "stale.db")
    source_root = tmp_path / "source"
    source_root.mkdir()
    sample = source_root / "kick.wav"
    sample.write_bytes(b"sample")
    visible_db.register_session("visible", source_root, tmp_path, "pending")
    visible_db.add_staging_records_bulk(
        "visible",
        [(1, str(sample), sample.name, "Pack", "Kicks", "", "Oneshots", "[]", "1.0", 0.1, None, "[]", None, None, 0)],
    )
    stale_db.register_session("stale", source_root, tmp_path, "pending")
    app = SimpleNamespace(
        session_store=SimpleNamespace(db=visible_db, session_id="visible"),
    )
    manager = DataManager(engine=SimpleNamespace(db=stale_db, session_id="stale"), app=app)

    try:
        database, session_id = manager._active_staging_export_source()

        assert database is visible_db
        assert session_id == "visible"
    finally:
        visible_db.close()
        stale_db.close()


def test_export_confirmation_states_exact_path_and_audio_dependency() -> None:
    expected_path = Path(r"D:\Library\DO_NOT_DELETE_unshuffle\unshuffle.db")
    export_button = object()
    cancel_button = object()
    message = mock.Mock()
    message.addButton.side_effect = [export_button, cancel_button]
    message.clickedButton.return_value = export_button
    manager = DataManager(app=SimpleNamespace())

    with mock.patch("gui.core.data_manager.QMessageBox", return_value=message) as message_class:
        message_class.Question = "question"
        message_class.AcceptRole = "accept"
        message_class.RejectRole = "reject"
        confirmed = manager._confirm_session_export(
            expected_path,
            [r"D:\SAMPLES", r"E:\Drum Kits"],
        )

    assert confirmed
    informative_text = message.setInformativeText.call_args.args[0]
    assert str(expected_path) in informative_text
    assert "Audio files are not copied" in informative_text
    assert "2 linked source folders" in informative_text
    assert "session_id" not in informative_text
    assert message.addButton.call_args_list[0].args == ("Export", "accept")
    assert message.addButton.call_args_list[1].args == ("Cancel", "reject")


def test_export_success_replaces_details_with_exact_path_and_open_folder_action() -> None:
    expected_path = Path(r"D:\Library\DO_NOT_DELETE_unshuffle\unshuffle.db")
    open_button = object()
    ok_button = object()
    message = mock.Mock()
    message.addButton.side_effect = [open_button, ok_button]
    message.clickedButton.return_value = ok_button
    manager = DataManager(app=SimpleNamespace())

    with mock.patch("gui.core.data_manager.QMessageBox", return_value=message) as message_class:
        message_class.Information = "information"
        message_class.ActionRole = "action"
        message_class.Ok = "ok"
        manager._show_session_export_success(expected_path, [r"D:\SAMPLES"])

    informative_text = message.setInformativeText.call_args.args[0]
    assert str(expected_path) in informative_text
    assert "audio files remain in their original folders" in informative_text
    message.setDetailedText.assert_not_called()
    assert message.addButton.call_args_list[0].args == ("Open Folder", "action")
