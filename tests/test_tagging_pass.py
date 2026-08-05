import struct
from pathlib import Path
from unittest import mock

from PySide6.QtCore import QObject
from unshuffle.core import PlanRecord
from unshuffle.core.features import FEATURE_VECTOR_SIZE
from unshuffle.persistence import UnshuffleDB
from gui.core.staging_session_store import StagingSessionStore
from gui.core.tagging_controller import TaggingController
from gui.models.db_staging_table import DbBackedStagingTableModel
from unshuffle.logic.tagging import (
    DuplicateMatch,
    GENRE_TAG_PREFIX,
    POSSIBLE_DUPLICATE_TAG,
    compute_tagging_pass,
    compute_db_duplicate_tags,
    genre_from_tags,
    merge_generated_tags,
)
from unshuffle.logic.tagging import service as tagging_service


def _blob(values):
    return struct.pack("<" + "f" * len(values), *values)


def _record(name, *, pack="Pack", vector=None, duration=0.5):
    return PlanRecord(
        source_path=Path(f"D:/Samples/{pack}/{name}"),
        pack=pack,
        category="Bass",
        audio_type="Oneshots",
        confidence="0.9",
        duration=duration,
        acoustic_vector=vector,
    )


def _staging_row(index: int, name: str):
    return (
        index,
        f"D:/Samples/Pack/{name}",
        name,
        "Pack",
        "Bass",
        "",
        "Oneshots",
        "",
        "0.9",
        0.5,
        f"hash-{index}",
        f"fast-{index}",
        "[]",
        "{}",
        None,
        None,
        None,
        None,
        None,
        None,
        0,
    )


def test_tagging_pass_flags_near_identical_acoustic_vectors():
    vec = _v([1.0, 0.5, 0.4, 0.2, 0.2, *([0.1] * 12), 0.7])
    first = _record("Long Dist 808 (D#).wav", vector=_blob(vec), duration=0.7)
    second = _record("Long Dist 808 (D#)1.wav", vector=_blob(vec), duration=0.7)
    far = _record("Different.wav", vector=_blob(_v([2.0, 0.5, 0.4, 0.2, 0.2, *([0.1] * 12), 0.7])), duration=0.7)

    result = compute_tagging_pass([first, second, far], genre_metadata_path=Path("missing.json"))

    assert result.duplicate_file_count == 2
    assert result.tags_by_path[str(first.source_path).replace("\\", "/")] == [POSSIBLE_DUPLICATE_TAG]
    assert result.tags_by_path[str(second.source_path).replace("\\", "/")] == [POSSIBLE_DUPLICATE_TAG]


def test_db_tagging_matches_in_memory_duplicate_detection(tmp_path):
    vec = _blob(_v([1.0, 0.5, 0.4, 0.2, 0.2, *([0.1] * 12), 0.7]))
    far = _blob(_v([2.0, 0.5, 0.4, 0.2, 0.2, *([0.1] * 12), 0.7]))
    db = UnshuffleDB(tmp_path / "tagging.db")
    try:
        db.register_session("session", Path("D:/Samples"), tmp_path, "pending")
        rows = []
        for index, (name, blob) in enumerate((("a.wav", vec), ("b.wav", vec), ("far.wav", far))):
            row = list(_staging_row(index, name))
            row[14] = blob
            row[9] = 0.7
            rows.append(tuple(row))
        db.add_staging_records_bulk("session", rows)

        assert compute_db_duplicate_tags(db, "session") == 2
        tags = {
            int(row[0]): str(row[1] or "")
            for row in db.conn.execute(
                "SELECT row_id, tags FROM staging_records WHERE session_id = ? ORDER BY row_id",
                ("session",),
            )
        }
        assert tags == {0: POSSIBLE_DUPLICATE_TAG, 1: POSSIBLE_DUPLICATE_TAG, 2: ""}
    finally:
        db.close()


def test_duplicate_detection_checks_later_pairs_in_same_bucket(monkeypatch):
    from unshuffle.logic.tagging import service as tagging_service

    a = _record("a.wav", vector=_blob(_v([0.0, 0.0])), duration=0.5)
    b = _record("b.wav", vector=_blob(_v([0.004, 0.0])), duration=0.5)
    c = _record("c.wav", vector=_blob(_v([0.004, 0.001])), duration=0.5)

    def fake_distance(left, right, **_kwargs):
        if left[0] == 0.0 or right[0] == 0.0:
            return 0.04
        return 0.001

    monkeypatch.setattr(tagging_service, "calculate_similarity_distance", fake_distance)

    assert tagging_service.find_possible_duplicates([a, b, c]) == [
        DuplicateMatch(
            str(b.source_path).replace("\\", "/"),
            str(c.source_path).replace("\\", "/"),
            0.001,
        )
    ]


def test_duplicate_detection_emits_determinate_progress():
    from unshuffle.logic.tagging import service as tagging_service

    records = [
        _record("a.wav", vector=_blob(_v([0.0, 0.0])), duration=0.5),
        _record("b.wav", vector=_blob(_v([0.0, 0.0])), duration=0.5),
    ]
    payloads = []

    tagging_service.find_possible_duplicates(records, progress_callback=payloads.append)

    assert payloads
    assert all(payload["phase"] == "Checking Possible Duplicates" for payload in payloads)
    assert any(payload.get("total") == len(records) for payload in payloads)
    assert payloads[-1]["current"] == payloads[-1]["total"]


def _v(values):
    return list(values) + [0.0] * (FEATURE_VECTOR_SIZE - len(values))


def test_tagging_pass_infers_genre_from_metadata_tokens(tmp_path):
    metadata = tmp_path / "genre_relationships.json"
    metadata.write_text('{"music": {"families": {"dance": {"house": ["deep house"]}}}}', encoding="utf-8")
    rec = _record("Deep House Loop.wav", pack="Sample House Pack", vector=None)

    result = compute_tagging_pass([rec], genre_metadata_path=metadata)

    path_key = str(rec.source_path).replace("\\", "/")
    assert result.genres_by_path[path_key] == "Deep House"
    assert result.tags_by_path[path_key] == [f"{GENRE_TAG_PREFIX}deep_house"]


def test_tagging_pass_can_run_without_genre_inference(tmp_path):
    metadata = tmp_path / "genre_relationships.json"
    metadata.write_text('{"music": {"families": {"dance": {"house": ["deep house"]}}}}', encoding="utf-8")
    rec = _record("Deep House Loop.wav", pack="Sample House Pack", vector=None)

    result = compute_tagging_pass([rec], genre_metadata_path=metadata, include_genres=False)

    assert result.genres_by_path == {}
    assert result.tags_by_path == {}


def test_generated_tags_replace_previous_generated_metadata_only():
    merged = merge_generated_tags(
        ["124bpm", POSSIBLE_DUPLICATE_TAG, "genre:house"],
        ["genre:deep_house"],
    )

    assert merged == ["124bpm", "genre:deep_house"]
    assert genre_from_tags(merged) == "Deep House"


def test_tagging_controller_clear_state_hides_footer_and_invalidates_results():
    class _App(QObject):
        def __init__(self):
            super().__init__()
            self.footer = mock.Mock()
            self.library_tab = mock.Mock()
            self.filter_controller = mock.Mock()

    app = _App()
    controller = TaggingController(app)
    controller._request_id = 4

    controller.clear_state()

    assert controller._request_id == 5
    app.footer.set_tagging_state.assert_called_once_with("", False)
    app.library_tab.set_possible_duplicate_filter_enabled.assert_called_once_with(False)
    app.filter_controller.refresh_dock_filters.assert_called_once_with()


def test_tagging_controller_scan_clear_does_not_query_disposed_session_store():
    class _ClosedStore:
        def has_any_tags(self, _tags):
            raise RuntimeError("database handle is closed")

    class _App(QObject):
        def __init__(self):
            super().__init__()
            self.footer = mock.Mock()
            self.library_tab = mock.Mock()
            self.filter_controller = mock.Mock()
            self.session_store = _ClosedStore()

    app = _App()
    controller = TaggingController(app)

    controller.clear_state(refresh_filter_state=False)

    app.footer.set_tagging_state.assert_called_once_with("", False)
    app.library_tab.set_possible_duplicate_filter_enabled.assert_called_once_with(False)
    app.filter_controller.refresh_dock_filters.assert_called_once_with()


def test_tagging_controller_keeps_filter_for_confirmed_duplicate_shadows():
    class _Store:
        def has_any_tags(self, tags):
            assert tags == {"possibleduplicate", "duplicate"}
            return True

    class _App(QObject):
        def __init__(self):
            super().__init__()
            self.footer = mock.Mock()
            self.library_tab = mock.Mock()
            self.filter_controller = mock.Mock()
            self.session_store = _Store()

    app = _App()
    controller = TaggingController(app)

    controller.clear_state()

    app.library_tab.set_possible_duplicate_filter_enabled.assert_called_once_with(True)


def test_tagging_controller_quiet_progress_does_not_touch_footer():
    class _App(QObject):
        def __init__(self):
            super().__init__()
            self.footer = mock.Mock()

    app = _App()
    controller = TaggingController(app)
    controller._request_id = 7
    controller._quiet_requests.add(7)

    controller._handle_progress(
        {
            "request_id": 7,
            "phase": "Checking Possible Duplicates",
            "current": 10,
            "total": 20,
        }
    )

    app.footer.set_status.assert_not_called()
    app.footer.set_progress.assert_not_called()


def test_tagging_controller_quiet_result_updates_filter_without_footer_notice():
    from types import SimpleNamespace
    from gui.models.staging_table import StagingTableModel

    record = _record("first.wav")

    class _App(QObject):
        def __init__(self):
            super().__init__()
            self.model = StagingTableModel([record], undo_stack=None)
            self.view_controller = SimpleNamespace(update_library_views=mock.Mock())
            self.search_controller = SimpleNamespace(current_query="", execute_search=mock.Mock())
            self.library_tab = mock.Mock()
            self.filter_controller = mock.Mock()
            self.footer = mock.Mock()

    app = _App()
    controller = TaggingController(app)

    controller.apply_tagging_result(
        {
            "tags_by_path": {str(record.source_path).replace("\\", "/"): [POSSIBLE_DUPLICATE_TAG]},
            "duplicate_file_count": 1,
        },
        schedule_coherence=False,
        quiet=True,
    )

    app.library_tab.set_possible_duplicate_filter_enabled.assert_called_once_with(True)
    app.filter_controller.refresh_dock_filters.assert_called_once_with()
    app.footer.set_tagging_state.assert_called_once_with("", False, can_filter=False)


def test_tagging_controller_syncs_generated_tags_by_stable_staging_row_id():
    from types import SimpleNamespace
    from gui.models.staging_table import StagingTableModel

    first = _record("first.wav")
    first.staging_row_id = 109
    second = _record("second.wav")
    second.staging_row_id = 1561
    synced = []

    class _App(QObject):
        def __init__(self):
            super().__init__()
            self.model = StagingTableModel(
                [first, second],
                undo_stack=None,
                sync_callback=lambda row_id, rec: synced.append((row_id, rec.source_path.name)),
            )
            self.view_controller = SimpleNamespace(update_library_views=mock.Mock())
            self.search_controller = SimpleNamespace(current_query="", execute_search=mock.Mock())
            self.library_tab = mock.Mock()
            self.filter_controller = mock.Mock()
            self.footer = mock.Mock()

    app = _App()
    controller = TaggingController(app)

    controller.apply_tagging_result(
        {
            "tags_by_path": {
                str(first.source_path).replace("\\", "/"): [POSSIBLE_DUPLICATE_TAG],
                str(second.source_path).replace("\\", "/"): [POSSIBLE_DUPLICATE_TAG],
            },
            "duplicate_file_count": 2,
        },
        schedule_coherence=False,
    )

    assert synced == [(109, "first.wav"), (1561, "second.wav")]


def test_tagging_controller_persists_generated_tags_for_db_backed_model(tmp_path):
    from types import SimpleNamespace

    db = UnshuffleDB(tmp_path / "test.db")
    try:
        db.register_session("session", Path("D:/Samples"), Path("D:/Target"), "pending")
        db.add_staging_records_bulk("session", [_staging_row(10, "first.wav"), _staging_row(11, "second.wav")])
        model = DbBackedStagingTableModel(StagingSessionStore(db, "session"))

        class _App(QObject):
            def __init__(self):
                super().__init__()
                self.model = model
                self.view_controller = SimpleNamespace(update_library_views=mock.Mock())
                self.search_controller = SimpleNamespace(current_query='tag:"possibleduplicate"', execute_search=mock.Mock())
                self.library_tab = mock.Mock()
                self.filter_controller = mock.Mock()
                self.footer = mock.Mock()

        app = _App()
        controller = TaggingController(app)

        controller.apply_tagging_result(
            {
                "tags_by_path": {"D:/Samples/Pack/first.wav": [POSSIBLE_DUPLICATE_TAG]},
                "duplicate_file_count": 1,
            },
            schedule_coherence=False,
        )

        row = db.conn.execute("SELECT tags FROM staging_records WHERE session_id = ? AND row_id = ?", ("session", 10)).fetchone()
        assert "possibleduplicate" in (row[0] or "")
        fts_count = db.conn.execute(
            "SELECT COUNT(*) FROM staging_fts WHERE staging_fts MATCH ?",
            ('tags : "possibleduplicate"*',),
        ).fetchone()[0]
        assert fts_count == 1
        app.search_controller.execute_search.assert_called_once_with()
    finally:
        db.close()


def test_tagging_controller_starts_db_worker_without_materializing_records(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from gui.core import workers

    db = UnshuffleDB(tmp_path / "test.db")
    try:
        db.register_session("session", Path("D:/Samples"), Path("D:/Target"), "pending")
        db.add_staging_records_bulk("session", [_staging_row(10, "first.wav")])
        model = DbBackedStagingTableModel(StagingSessionStore(db, "session"))

        class ExplodingRecords:
            def __iter__(self):
                raise AssertionError("DB tagging must not hydrate model.records")

        model.records = ExplodingRecords()
        started = []
        monkeypatch.setattr(workers.TaggingWorker, "start", lambda self: started.append(self))

        class _App(QObject):
            def __init__(self):
                super().__init__()
                self.model = model
                self.engine = SimpleNamespace(db=db, session_id="session")
                self.acoustic_session_state = None
                self.footer = mock.Mock()

        controller = TaggingController(_App())
        controller.start_tagging_pass(schedule_coherence=False)

        assert len(started) == 1
        assert started[0].records == []
        assert started[0].db is db
        assert started[0].session_id == "session"
    finally:
        db.close()


def test_large_duplicate_bucket_uses_ann_and_marks_every_identical_row(monkeypatch):
    import sqlite3

    conn = sqlite3.connect(":memory:")
    try:
        conn.execute(
            "CREATE TEMP TABLE tagging_candidates ("
            "bucket_duration INTEGER, bucket_signature TEXT, row_id INTEGER PRIMARY KEY, "
            "source_path TEXT, duration REAL, feature_vector BLOB)"
        )
        conn.execute("CREATE TEMP TABLE tagging_matches (row_id INTEGER PRIMARY KEY)")
        vector = _blob([0.2] * FEATURE_VECTOR_SIZE)
        conn.executemany(
            "INSERT INTO tagging_candidates VALUES (1, 'same', ?, ?, 0.5, ?)",
            [(index, f"D:/Samples/{index}.wav", vector) for index in range(2001)],
        )
        monkeypatch.setattr(
            tagging_service,
            "_mark_large_bucket_exact",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("quadratic fallback used")),
        )

        tagging_service._mark_large_bucket_ann(conn, 1, "same", 2001, 0.025, 0.05)

        assert conn.execute("SELECT COUNT(*) FROM tagging_matches").fetchone()[0] == 2001
    finally:
        conn.close()


def test_sidebar_hides_possible_duplicate_filter_until_enabled():
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from gui.widgets.sidebar import LibrarySidebar, POSSIBLE_DUPLICATE_FILTER_QUERY, SavedFilterItem

    _app = QApplication.instance() or QApplication([])
    sidebar = LibrarySidebar()
    sidebar.set_saved_filters([])

    assert sidebar.saved_filters_layout.count() == 0

    sidebar.set_possible_duplicate_filter_enabled(True)
    item = sidebar.saved_filters_layout.itemAt(0).widget()

    assert isinstance(item, SavedFilterItem)
    assert item.query == POSSIBLE_DUPLICATE_FILTER_QUERY
    assert item.filter_enabled

    sidebar.set_possible_duplicate_filter_enabled(False)
    assert sidebar.saved_filters_layout.count() == 0


def test_dock_filters_include_builtin_possible_duplicates_when_enabled():
    from types import SimpleNamespace
    from gui.core.filter_controller import FilterController
    from gui.widgets.sidebar import POSSIBLE_DUPLICATE_FILTER_QUERY

    app = SimpleNamespace(
        engine=None,
        settings_controller=SimpleNamespace(get_saved_filters=lambda: []),
        library_tab=SimpleNamespace(sidebar=SimpleNamespace(possible_duplicate_filter_enabled=True)),
        dock_view=mock.Mock(),
    )
    controller = FilterController(app.settings_controller, app)

    controller.refresh_dock_filters()

    options = app.dock_view.set_filters.call_args.args[0]
    assert ("Filter: Possible/Confirmed Duplicates", POSSIBLE_DUPLICATE_FILTER_QUERY) in options


def test_header_filter_routes_through_search_query_and_clear_restores_it():
    from types import SimpleNamespace
    from gui.core.filter_controller import FilterController
    from gui.utils.constants import StagingColumn

    search_controller = SimpleNamespace(
        current_query='source:"D:/Samples"',
        set_query=mock.Mock(),
    )
    app = SimpleNamespace(
        search_controller=search_controller,
        library_tab=SimpleNamespace(update_header_labels=mock.Mock()),
    )
    controller = FilterController(SimpleNamespace(), app)

    controller._set_header_filter_query(StagingColumn.PACK, "Pack A")

    search_controller.set_query.assert_called_once_with(
        'source:"D:/Samples" AND pack:"Pack A"',
        immediate=True,
    )

    search_controller.current_query = 'source:"D:/Samples" AND pack:"Pack A"'
    search_controller.set_query.reset_mock()
    controller.clear_header_filter(StagingColumn.PACK)

    search_controller.set_query.assert_called_once_with('source:"D:/Samples"', immediate=True)


def test_confidence_header_filter_uses_an_exact_range_query():
    from types import SimpleNamespace
    from gui.core.filter_controller import FilterController
    from gui.utils.constants import StagingColumn

    search_controller = SimpleNamespace(current_query="", set_query=mock.Mock())
    app = SimpleNamespace(
        search_controller=search_controller,
        library_tab=SimpleNamespace(update_header_labels=mock.Mock()),
    )

    FilterController(SimpleNamespace(), app)._set_header_filter_query(StagingColumn.CONFIDENCE, "90%")

    search_controller.set_query.assert_called_once_with('conf:"90-90"', immediate=True)
