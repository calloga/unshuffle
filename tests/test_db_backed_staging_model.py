from pathlib import Path
import json
import struct

from PySide6.QtCore import Qt

from gui.core import workflow_model_cleanup
from gui.core.staging_session_store import StagingSessionStore
from gui.core.acoustic_session_state import AcousticSessionState
from gui.models.db_staging_table import DbBackedStagingTableModel
from gui.models.library_tree import FIELDS_ROLE, LibraryTreeModel, RAW_NAME_ROLE
from gui.utils.constants import StagingColumn
from gui.widgets.coherence_view_model import coherence_points_from_app
from unshuffle.core.features import FEATURE_VECTOR_SIZE
from unshuffle.persistence import UnshuffleDB


def _row(index: int, *, category: str = "Kicks", audio_type: str = "Oneshots", vector: bytes | None = None):
    return (
        index,
        f"D:/Samples/Pack/sample_{index}.wav",
        f"sample_{index}.wav",
        "Pack",
        category,
        "",
        audio_type,
        "",
        "0.9",
        1.0,
        f"hash-{index}",
        f"fast-{index}",
        "[]",
        "{}",
        vector,
        None,
        None,
        None,
        None,
        None,
        0,
    )


def test_store_counts_and_orders_rows(tmp_path):
    db = UnshuffleDB(tmp_path / "test.db")
    try:
        db.register_session("session", Path("D:/Samples"), Path("D:/Target"), "pending")
        db.add_staging_records_bulk("session", [_row(2), _row(1), _row(3)])
        store = StagingSessionStore(db, "session")

        assert store.count() == 3
        assert store.row_ids() == [1, 2, 3]
        assert [record.source_path.name for batch in store.iter_records(batch_size=2) for record in batch] == [
            "sample_1.wav",
            "sample_2.wav",
            "sample_3.wav",
        ]
    finally:
        db.close()


def test_db_backed_model_sorts_by_selected_column(tmp_path):
    db = UnshuffleDB(tmp_path / "test.db")
    try:
        db.register_session("session", Path("D:/Samples"), Path("D:/Target"), "pending")
        first = list(_row(0))
        first[3] = "Zed Pack"
        first[4] = "Snares"
        first[8] = "0.1"
        second = list(_row(1))
        second[3] = "Alpha Pack"
        second[4] = "Kicks"
        second[8] = "0.9"
        db.add_staging_records_bulk("session", [tuple(first), tuple(second)])
        model = DbBackedStagingTableModel(StagingSessionStore(db, "session"))

        model.sort(StagingColumn.PACK, Qt.AscendingOrder)
        assert [model.data(model.index(row, 0), Qt.DisplayRole) for row in range(model.rowCount())] == [
            "Alpha Pack",
            "Zed Pack",
        ]

        model.sort(StagingColumn.CATEGORY, Qt.DescendingOrder)
        assert [model.data(model.index(row, 2), Qt.DisplayRole) for row in range(model.rowCount())] == [
            "Snares",
            "Kicks",
        ]
    finally:
        db.close()


def test_db_backed_tree_presents_non_audio_assets_as_utility(tmp_path):
    from PySide6.QtWidgets import QApplication

    _app = QApplication.instance() or QApplication([])
    db = UnshuffleDB(tmp_path / "test.db")
    try:
        db.register_session("session", Path("D:/Samples"), Path("D:/Target"), "pending")
        row = list(_row(0, audio_type="Non-Audio Assets", category="Non-Audio Assets"))
        row[1] = "D:/Samples/Pack/cover.jpg"
        row[2] = "cover.jpg"
        db.add_staging_records_bulk("session", [tuple(row)])
        store = StagingSessionStore(db, "session")
        tree = LibraryTreeModel()

        tree.rebuild_from_store(store)
        root = tree.invisibleRootItem()
        item = root.child(0, 0)

        assert item.data(RAW_NAME_ROLE) == "Utility"
        assert item.data(FIELDS_ROLE) == {"audio_type": "Utility"}

        tree.populate_index(tree.indexFromItem(item))
        child = item.child(0, 0)
        assert child.data(RAW_NAME_ROLE) == "Non-Audio Assets"
    finally:
        db.close()


def test_db_backed_tree_does_not_create_other_for_empty_subcategories(tmp_path):
    from PySide6.QtWidgets import QApplication

    _app = QApplication.instance() or QApplication([])
    db = UnshuffleDB(tmp_path / "test.db")
    try:
        db.register_session("session", Path("D:/Samples"), Path("D:/Target"), "pending")
        db.add_staging_records_bulk(
            "session",
            [_row(0, category="Bass"), _row(1, category="Bass")],
        )
        tree = LibraryTreeModel()
        tree.rebuild_from_store(StagingSessionStore(db, "session"))
        root_item = tree.invisibleRootItem().child(0, 0)

        tree.populate_index(tree.indexFromItem(root_item))
        category_item = root_item.child(0, 0)
        tree.populate_index(tree.indexFromItem(category_item))

        assert category_item.rowCount() == 2
        assert all(category_item.child(row, 0).data(RAW_NAME_ROLE) != "Other" for row in range(category_item.rowCount()))
    finally:
        db.close()


def test_db_backed_model_hydrates_and_evictions_are_bounded(tmp_path):
    db = UnshuffleDB(tmp_path / "test.db")
    try:
        db.register_session("session", Path("D:/Samples"), Path("D:/Target"), "pending")
        db.add_staging_records_bulk("session", [_row(i) for i in range(20)])
        store = StagingSessionStore(db, "session")
        model = DbBackedStagingTableModel(store)
        model.CHUNK_SIZE = 3
        model._record_cache.max_size = 5

        assert model.rowCount() == 20
        assert model.data(model.index(0, 1), Qt.DisplayRole) == "sample_0.wav"
        assert model.record_id(19) == 9
        assert len(model._record_cache._cache) <= 5
    finally:
        db.close()


def test_db_backed_table_uses_visible_positions_and_lightweight_rows(tmp_path):
    vector = struct.pack("<" + ("f" * FEATURE_VECTOR_SIZE), *([0.1] * FEATURE_VECTOR_SIZE))
    db = UnshuffleDB(tmp_path / "test.db")
    try:
        db.register_session("session", Path("D:/Samples"), Path("D:/Target"), "pending")
        first = list(_row(10, category="Kicks", vector=vector))
        second = list(_row(3, category="Snares", vector=vector))
        db.add_staging_records_bulk("session", [tuple(first), tuple(second)])
        model = DbBackedStagingTableModel(StagingSessionStore(db, "session"))

        model.sort(StagingColumn.CATEGORY, Qt.DescendingOrder)
        assert model.headerData(0, Qt.Vertical, Qt.DisplayRole) == "1"
        record = model.record(0)
        assert record.feature_vector is None
        assert record.staging_row_id == 3
    finally:
        db.close()


def test_db_backed_model_updates_rows_without_rewrite(tmp_path):
    db = UnshuffleDB(tmp_path / "test.db")
    try:
        db.register_session("session", Path("D:/Samples"), Path("D:/Target"), "pending")
        db.add_staging_records_bulk("session", [_row(0)])
        store = StagingSessionStore(db, "session")
        model = DbBackedStagingTableModel(store)

        assert model.setData(model.index(0, 2), "Snares", Qt.EditRole)
        row = db.get_staging_records("session")[0]
        assert row["category"] == "Snares"
    finally:
        db.close()


def test_db_backed_similarity_bias_matches_proxy_window_behavior(tmp_path):
    db = UnshuffleDB(tmp_path / "test.db")
    try:
        db.register_session("session", Path("D:/Samples"), Path("D:/Target"), "pending")
        db.add_staging_records_bulk("session", [_row(index) for index in range(5)])
        model = DbBackedStagingTableModel(StagingSessionStore(db, "session"))
        distances = {0: 0.0, 1: 0.1, 2: 0.2, 3: 0.3, 4: 0.4}

        model.set_similarity_data(distances, 0.2, anchor_row=0)
        assert model.rowCount() == 5

        model.set_similarity_bias(100)
        assert {model.record_id(row) for row in range(model.rowCount())} == {0, 1}

        model.set_similarity_bias(-100)
        assert {model.record_id(row) for row in range(model.rowCount())} == {0, 4}
    finally:
        db.close()


def test_db_backed_model_classification_tooltip_uses_staging_helpers(tmp_path):
    db = UnshuffleDB(tmp_path / "test.db")
    try:
        db.register_session("session", Path("D:/Samples"), Path("D:/Target"), "pending")
        row = list(_row(0))
        row[13] = json.dumps(
            {
                "trace": {
                    "components": {
                        "filename": {
                            "token_trace": [
                                {
                                    "token": "kick",
                                    "status": "matched",
                                    "matches": [{"category": "Kicks"}],
                                }
                            ]
                        }
                    },
                    "raw": {"Kicks": 1.2},
                }
            }
        )
        db.add_staging_records_bulk("session", [tuple(row)])
        model = DbBackedStagingTableModel(StagingSessionStore(db, "session"))

        tooltip = model.data(model.index(0, 2), Qt.ToolTipRole)

        assert "file name mentions" in tooltip
    finally:
        db.close()


def test_coherence_points_from_app_uses_db_cap_before_point_creation(tmp_path):
    vector = struct.pack("<" + ("f" * FEATURE_VECTOR_SIZE), *([0.1] * FEATURE_VECTOR_SIZE))
    db = UnshuffleDB(tmp_path / "test.db")
    try:
        db.register_session("session", Path("D:/Samples"), Path("D:/Target"), "pending")
        db.add_staging_records_bulk("session", [_row(i, vector=vector) for i in range(30)])
        store = StagingSessionStore(db, "session")

        app = type("App", (), {})()
        app.session_store = store
        app.model = None
        app.engine = type("Engine", (), {"db": db, "session_id": "session"})()

        points, _results = coherence_points_from_app(app, limit=7, audio_type="Oneshots")

        assert len(points) == 7
    finally:
        db.close()


def test_map_candidate_rows_balances_categories_under_the_cap(tmp_path):
    db = UnshuffleDB(tmp_path / "test.db")
    try:
        db.register_session("session", Path("D:/Samples"), Path("D:/Target"), "pending")
        vector = struct.pack("<" + ("f" * FEATURE_VECTOR_SIZE), *([0.1] * FEATURE_VECTOR_SIZE))
        rows = []
        for index, category in enumerate(("Kicks", "Snares", "Hats")):
            rows.extend(
                _row(index * 10 + offset, category=category, vector=vector)
                for offset in range(10)
            )
        db.add_staging_records_bulk("session", rows)

        candidates = StagingSessionStore(db, "session").map_candidate_rows(
            audio_type="Oneshots",
            limit=6,
        )

        assert len(candidates) == 6
        assert {row["category"] for row in candidates} == {"Kicks", "Snares", "Hats"}
    finally:
        db.close()


def test_coherence_points_from_app_caps_all_map_per_audio_type(tmp_path):
    vector = struct.pack("<" + ("f" * FEATURE_VECTOR_SIZE), *([0.1] * FEATURE_VECTOR_SIZE))
    db = UnshuffleDB(tmp_path / "test.db")
    try:
        db.register_session("session", Path("D:/Samples"), Path("D:/Target"), "pending")
        db.add_staging_records_bulk(
            "session",
            [
                *[_row(i, audio_type="Loops", vector=vector) for i in range(5)],
                *[_row(i + 10, audio_type="Oneshots", vector=vector) for i in range(5)],
            ],
        )
        store = StagingSessionStore(db, "session")

        app = type("App", (), {})()
        app.session_store = store
        app.model = None
        app.engine = type("Engine", (), {"db": db, "session_id": "session"})()

        points, _results = coherence_points_from_app(app, limit=3, audio_type="")

        assert sum(1 for point in points if point.audio_type == "Loops") == 3
        assert sum(1 for point in points if point.audio_type == "Oneshots") == 3
    finally:
        db.close()


def test_all_sound_map_uses_mixed_projection_over_per_type_capped_points(monkeypatch):
    from PySide6.QtCore import QPointF
    from PySide6.QtWidgets import QApplication
    from gui.widgets import coherence_analyzer
    from gui.widgets.coherence_analyzer import CoherenceMapWidget
    from gui.widgets.coherence_view_model import AnalyzerPoint

    _app = QApplication.instance() or QApplication([])
    calls: list[tuple[str, ...]] = []

    def fake_projection(points, *_args, **_kwargs):
        calls.append(tuple(sorted({point.audio_type for point in points})))
        return [
            (point, QPointF(0.25 if point.audio_type == "Loops" else 0.75, 0.5))
            for point in points
        ]

    monkeypatch.setattr(coherence_analyzer.coherence_projection, "_continuous_acoustic_projection", fake_projection)
    widget = CoherenceMapWidget()
    points = [
        AnalyzerPoint("1", "Loops", "Bass", "", "Loops:Bass:", [0.1]),
        AnalyzerPoint("2", "Oneshots", "Kicks", "", "Oneshots:Kicks:", [0.2]),
    ]

    widget.set_points(points, version="test")
    calls.clear()
    widget.set_audio_type("")

    assert calls == [("Loops", "Oneshots")]
    assert [point.record_id for point, _pos in widget._projected] == ["1", "2"]


def test_embedded_map_all_filter_switches_underlying_map_audio_type():
    from PySide6.QtWidgets import QApplication
    from gui.widgets.coherence_analyzer import CoherenceAnalyzerPage
    from gui.widgets.coherence_view_model import AnalyzerPoint

    _app = QApplication.instance() or QApplication([])
    page = CoherenceAnalyzerPage(show_header=False, show_filters=False)
    points = [
        AnalyzerPoint("1", "Loops", "Bass", "", "Loops:Bass:", [0.1]),
        AnalyzerPoint("2", "Oneshots", "Kicks", "", "Oneshots:Kicks:", [0.2]),
    ]
    page.map.set_points(points, version="test")
    page._records = points
    page._results = []
    page._data_key = "test"
    page._selected_audio_type = "Loops"
    page.map.set_audio_type("Loops")

    page.set_library_filters("", "", None)

    assert page._selected_audio_type == ""
    assert page.map._audio_type == ""


def test_map_projection_prewarm_is_noop_for_unchanged_data(monkeypatch):
    from PySide6.QtWidgets import QApplication
    from gui.widgets.coherence_analyzer import CoherenceAnalyzerPage
    from gui.widgets.coherence_view_model import AnalyzerPoint

    _app = QApplication.instance() or QApplication([])
    page = CoherenceAnalyzerPage(show_header=False, show_filters=False)
    points = [
        AnalyzerPoint("1", "Loops", "Bass", "", "Loops:Bass:", [0.1]),
        AnalyzerPoint("2", "Oneshots", "Kicks", "", "Oneshots:Kicks:", [0.2]),
    ]
    page._records = points
    page._data_key = "test"
    calls = []
    monkeypatch.setattr(page.map, "prewarm_projection", lambda audio_type, category="": calls.append((audio_type, category)))

    page.prewarm_library_projections()
    page.prewarm_library_projections()

    assert calls
    assert calls.count(("", "")) == 1


def test_acoustic_session_state_uses_db_row_ids_without_hydrating_records(tmp_path):
    db = UnshuffleDB(tmp_path / "test.db")
    try:
        db.register_session("session", Path("D:/Samples"), Path("D:/Target"), "pending")
        db.add_staging_records_bulk("session", [_row(i) for i in range(5)])
        model = DbBackedStagingTableModel(StagingSessionStore(db, "session"))
        model.set_matched_ids({2})

        class ExplodingRecords:
            def __iter__(self):
                raise AssertionError("records should not be hydrated for staging_record_ids")

        model.records = ExplodingRecords()
        app = type("App", (), {"model": model})()

        assert model.rowCount() == 1
        assert AcousticSessionState(app).staging_record_ids() == {"0", "1", "2", "3", "4"}
    finally:
        db.close()


def test_db_backed_records_sequence_is_unfiltered_all_records(tmp_path):
    db = UnshuffleDB(tmp_path / "test.db")
    try:
        db.register_session("session", Path("D:/Samples"), Path("D:/Target"), "pending")
        db.add_staging_records_bulk("session", [_row(i) for i in range(5)])
        model = DbBackedStagingTableModel(StagingSessionStore(db, "session"))
        model.set_matched_ids({2})

        assert model.rowCount() == 1
        assert len(model.records) == 5
        assert [record.staging_row_id for record in model.records] == [0, 1, 2, 3, 4]
    finally:
        db.close()


def test_lightweight_rows_by_ids_excludes_feature_vector_blob(tmp_path):
    vector = struct.pack("<" + ("f" * FEATURE_VECTOR_SIZE), *([0.1] * FEATURE_VECTOR_SIZE))
    db = UnshuffleDB(tmp_path / "test.db")
    try:
        db.register_session("session", Path("D:/Samples"), Path("D:/Target"), "pending")
        db.add_staging_records_bulk("session", [_row(0, vector=vector)])
        store = StagingSessionStore(db, "session")

        row = store.lightweight_rows_by_ids([0])[0]

        assert row["source_path"] == "D:/Samples/Pack/sample_0.wav"
        assert "feature_vector" not in row
    finally:
        db.close()


def test_db_backed_cleanup_removes_prefix_without_hydrating_records(tmp_path):
    db = UnshuffleDB(tmp_path / "test.db")
    try:
        db.register_session("session", Path("D:/Samples"), Path("D:/Target"), "pending")
        db.add_staging_records_bulk(
            "session",
            [
                _row(0),
                _row(1),
                (
                    2,
                    "D:/Other/Pack/sample_2.wav",
                    "sample_2.wav",
                    "Pack",
                    "Kicks",
                    "",
                    "Oneshots",
                    "",
                    "0.9",
                    1.0,
                    "hash-2",
                    "fast-2",
                    "[]",
                    "{}",
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    0,
                ),
            ],
        )
        model = DbBackedStagingTableModel(StagingSessionStore(db, "session"))

        class ExplodingRecords:
            def __iter__(self):
                raise AssertionError("records should not be hydrated for DB cleanup")

        model.records = ExplodingRecords()

        removed = workflow_model_cleanup.remove_excluded_prefix(model, Path("D:/Samples"))

        assert removed == 2
        assert model.rowCount() == 1
        assert db.get_staging_records("session")[0]["source_path"] == "D:/Other/Pack/sample_2.wav"
    finally:
        db.close()


def test_db_backed_cleanup_removes_deleted_paths_without_hydrating_records(tmp_path):
    db = UnshuffleDB(tmp_path / "test.db")
    try:
        db.register_session("session", Path("D:/Samples"), Path("D:/Target"), "pending")
        db.add_staging_records_bulk("session", [_row(0), _row(1)])
        model = DbBackedStagingTableModel(StagingSessionStore(db, "session"))

        class ExplodingRecords:
            def __iter__(self):
                raise AssertionError("records should not be hydrated for DB cleanup")

        model.records = ExplodingRecords()

        removed = workflow_model_cleanup.remove_deleted_paths(model, [Path("D:/Samples/Pack/sample_1.wav")])

        assert removed == 1
        assert model.rowCount() == 1
        assert db.get_staging_records("session")[0]["source_path"] == "D:/Samples/Pack/sample_0.wav"
    finally:
        db.close()
