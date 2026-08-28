from pathlib import Path
from dataclasses import replace
import json
import struct

import pytest

from PySide6.QtCore import Qt
from PySide6.QtGui import QStandardItem

from gui.core import workflow_model_cleanup
from gui.core.staging_session_store import StagingQuery, StagingRowsUpdateCanceled, StagingSessionStore
from gui.core.tree_filter_options import EffectiveTaxonomyContext, custom_tree_filter_options
from gui.core.acoustic_session_state import AcousticSessionState
from gui.models.db_staging_table import DbBackedStagingTableModel
from gui.models.library_tree import (
    FIELDS_ROLE,
    LibraryTreeModel,
    NODE_TYPE_ROLE,
    RAW_NAME_ROLE,
    clear_tree_color_caches,
    tree_file_sequence_icon,
)
from gui.utils.constants import StagingColumn
from gui.widgets.coherence_view_model import coherence_points_from_app
from unshuffle.core.features import FEATURE_VECTOR_SIZE
from unshuffle.persistence import UnshuffleDB
from unshuffle.logic.tree_organization import TreeOrganizationNode, TreeOrganizationProfile


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


def test_db_model_bulk_edit_avoids_full_index_refresh_when_order_is_unchanged(tmp_path):
    db = UnshuffleDB(tmp_path / "test.db")
    try:
        db.register_session("session", Path("D:/Samples"), Path("D:/Target"), "pending")
        db.add_staging_records_bulk("session", [_row(1), _row(2), _row(3)])
        model = DbBackedStagingTableModel(StagingSessionStore(db, "session"))
        records = model.records_for_rows(range(3))
        refresh_calls = []
        model.refresh_index = lambda: refresh_calls.append(True)

        model._apply_bulk_values([
            (record, StagingColumn.CATEGORY, "Snares")
            for record in records
        ])

        assert refresh_calls == []
        categories = db.conn.execute(
            "SELECT DISTINCT category FROM staging_records WHERE session_id = ?",
            ("session",),
        ).fetchall()
        assert [row[0] for row in categories] == ["Snares"]
    finally:
        db.close()


def test_db_model_semantic_edit_mirrors_duplicate_shadow(tmp_path):
    db = UnshuffleDB(tmp_path / "test.db")
    try:
        db.register_session("session", Path("D:/Samples"), Path("D:/Target"), "pending")
        canonical = list(_row(1, category="Kicks"))
        shadow = list(_row(2, category="Kicks"))
        shadow[10] = canonical[10]
        shadow[11] = canonical[11]
        shadow[13] = json.dumps({
            "duplicate_shadow": {
                "is_shadow": True,
                "duplicate_of_hash": "hash-1",
                "duplicate_of_path": canonical[1],
            }
        })
        db.add_staging_records_bulk("session", [tuple(canonical), tuple(shadow)])
        model = DbBackedStagingTableModel(StagingSessionStore(db, "session"))

        model._apply_bulk_values([
            (model.record(0), StagingColumn.CATEGORY, "Snares"),
        ])

        rows = db.get_staging_records("session")
        assert [row["category"] for row in rows] == ["Snares", "Snares"]
        reloaded_shadow = model.store.record_by_row_id(2)
        assert reloaded_shadow is not None
        assert reloaded_shadow.is_duplicate_shadow is True
        assert isinstance(canonical[1], str)
        assert reloaded_shadow.duplicate_of_path == Path(canonical[1])
    finally:
        db.close()


def test_store_canceled_bulk_update_rolls_back_transaction(tmp_path):
    db = UnshuffleDB(tmp_path / "test.db")
    try:
        db.register_session("session", Path("D:/Samples"), Path("D:/Target"), "pending")
        db.add_staging_records_bulk("session", [_row(1), _row(2)])
        store = StagingSessionStore(db, "session")
        canceled = []

        with pytest.raises(StagingRowsUpdateCanceled):
            store.update_rows(
                [(1, {"category": "Snares"}), (2, {"category": "Snares"})],
                batch_size=1,
                progress_callback=lambda _current, _total: canceled.append(True),
                interrupted_check=lambda: bool(canceled),
            )

        assert [row["category"] for row in db.get_staging_records("session")] == ["Kicks", "Kicks"]
    finally:
        db.close()


def test_db_model_bulk_edit_refreshes_index_when_active_sort_changes(tmp_path):
    db = UnshuffleDB(tmp_path / "test.db")
    try:
        db.register_session("session", Path("D:/Samples"), Path("D:/Target"), "pending")
        db.add_staging_records_bulk("session", [_row(1), _row(2)])
        model = DbBackedStagingTableModel(StagingSessionStore(db, "session"))
        model.group_column = StagingColumn.CATEGORY
        record = model.record(0)
        refresh_calls = []
        model.refresh_index = lambda: refresh_calls.append(True)

        model._apply_bulk_values([(record, StagingColumn.CATEGORY, "Snares")])

        assert refresh_calls == [True]
    finally:
        db.close()


def test_store_returns_individual_tag_filter_values(tmp_path):
    db = UnshuffleDB(tmp_path / "test.db")
    try:
        db.register_session("session", Path("D:/Samples"), Path("D:/Target"), "pending")
        first = list(_row(1))
        first[7] = "warm, duplicate"
        second = list(_row(2))
        second[7] = "warm, possibleduplicate"
        db.add_staging_records_bulk("session", [tuple(first), tuple(second)])

        assert StagingSessionStore(db, "session").distinct_values(StagingColumn.TAGS) == [
            "duplicate",
            "possibleduplicate",
            "warm",
        ]
    finally:
        db.close()


def test_tree_branch_total_ignores_active_result_filter(tmp_path):
    db = UnshuffleDB(tmp_path / "test.db")
    try:
        db.register_session("session", Path("D:/Samples"), Path("D:/Target"), "pending")
        db.add_staging_records_bulk(
            "session",
            [_row(1, category="Kicks"), _row(2, category="Kicks"), _row(3, category="Snares")],
        )
        store = StagingSessionStore(db, "session")
        query = StagingQuery(matched_ids=frozenset({1}), show_non_audio_assets=True)
        model = LibraryTreeModel()
        model._session_store = store
        model._session_query = query
        item = QStandardItem("Oneshots (1)")
        item.setData("audio_type", NODE_TYPE_ROLE)
        item.setData({"audio_type": "Oneshots"}, FIELDS_ROLE)
        model.appendRow(item)

        oneshots = model.index(0, 0)
        assert model.has_active_session_filters()
        assert model.total_count_for_index(oneshots) == 3
        assert store.count_for_fields({"audio_type": "Oneshots", "category": "Kicks"}) == 2
    finally:
        db.close()


def test_store_computes_pack_common_tokens_from_buildable_audio_columns(tmp_path):
    db = UnshuffleDB(tmp_path / "test.db")
    try:
        db.register_session("session", Path("D:/Samples"), Path("D:/Target"), "pending")
        kick = list(_row(0))
        kick[1], kick[2], kick[3] = (
            "D:/Samples/Cymatics Cobra/Cymatics kick.wav",
            "Cymatics kick.wav",
            "Cymatics Cobra",
        )
        snare = list(_row(1))
        snare[1], snare[2], snare[3] = (
            "D:/Samples/Cymatics Cobra/Cymatics snare.wav",
            "Cymatics snare.wav",
            "Cymatics Cobra",
        )
        shadow = list(_row(2))
        shadow[1], shadow[2], shadow[3] = (
            "D:/Samples/Cymatics Cobra/plain.wav",
            "plain.wav",
            "Cymatics Cobra",
        )
        shadow[13] = json.dumps({"duplicate_shadow": {"is_shadow": True}})
        non_audio = list(_row(3, audio_type="Non-Audio Assets"))
        non_audio[1], non_audio[2], non_audio[3] = (
            "D:/Samples/Cymatics Cobra/cover.jpg",
            "cover.jpg",
            "Cymatics Cobra",
        )
        db.add_staging_records_bulk(
            "session",
            [tuple(kick), tuple(snare), tuple(shadow), tuple(non_audio)],
        )

        common = StagingSessionStore(db, "session").common_filename_tokens_by_pack()

        assert common == {"cymatics cobra": frozenset({"cymatics"})}
    finally:
        db.close()


def test_db_backed_model_can_hide_confirmed_duplicate_rows(tmp_path):
    db = UnshuffleDB(tmp_path / "test.db")
    try:
        db.register_session("session", Path("D:/Samples"), Path("D:/Target"), "pending")
        normal = list(_row(0))
        duplicate = list(_row(1))
        duplicate[7] = "duplicate"
        duplicate[13] = json.dumps({"duplicate_shadow": {"is_shadow": True}})
        db.add_staging_records_bulk("session", [tuple(normal), tuple(duplicate)])
        model = DbBackedStagingTableModel(StagingSessionStore(db, "session"))

        assert model.rowCount() == 2

        model.set_show_duplicates(False)

        assert model.rowCount() == 1
        assert model.record(0).source_path.name == "sample_0.wav"
    finally:
        db.close()


def test_db_backed_model_hides_non_audio_assets_by_default(tmp_path):
    db = UnshuffleDB(tmp_path / "test.db")
    try:
        db.register_session("session", Path("D:/Samples"), Path("D:/Target"), "pending")
        db.add_staging_records_bulk(
            "session",
            [_row(0), _row(1, audio_type="Non-Audio Assets")],
        )
        model = DbBackedStagingTableModel(StagingSessionStore(db, "session"))

        assert model.rowCount() == 1
        assert model.record(0).source_path.name == "sample_0.wav"

        model.set_show_non_audio_assets(True)

        assert model.rowCount() == 2
    finally:
        db.close()


def test_model_does_not_begin_reset_when_index_query_fails(tmp_path, monkeypatch):
    db = UnshuffleDB(tmp_path / "test.db")
    try:
        db.register_session("session", Path("D:/Samples"), Path("D:/Target"), "pending")
        db.add_staging_records_bulk("session", [_row(0)])
        store = StagingSessionStore(db, "session")
        model = DbBackedStagingTableModel(store)
        began = []
        ended = []
        monkeypatch.setattr(model, "beginResetModel", lambda: began.append(True))
        monkeypatch.setattr(model, "endResetModel", lambda: ended.append(True))
        monkeypatch.setattr(store, "row_ids", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("closed")))

        with pytest.raises(RuntimeError, match="closed"):
            model.refresh_index()

        assert began == []
        assert ended == []
    finally:
        db.close()


def test_store_builds_append_dedupe_index_without_full_record_hydration(tmp_path):
    db = UnshuffleDB(tmp_path / "test.db")
    try:
        db.register_session("session", Path("D:/Samples"), Path("D:/Target"), "pending")
        canonical = list(_row(0, category="Kicks"))
        canonical[10] = "full-hash"
        shadow = list(_row(1, category="Snares"))
        shadow[10] = "shadow-hash"
        shadow[13] = json.dumps({"duplicate_shadow": {"is_shadow": True}})
        db.add_staging_records_bulk("session", [tuple(canonical), tuple(shadow)])
        store = StagingSessionStore(db, "session")

        index = store.scan_dedupe_index()

        assert index["full_hashes"] == {"full-hash"}
        record = index["full_hash_records"]["full-hash"]
        assert record.category == "Kicks"
        assert record.source_path.name == "sample_0.wav"
        assert "shadow-hash" not in index["full_hashes"]
    finally:
        db.close()


def test_tree_file_icons_cache_by_palette_slot(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "gui.models.library_tree._get_tinted_icon_for_tree",
        lambda path, color: calls.append((path, color)) or object(),
    )
    clear_tree_color_caches()

    for index in range(600):
        tree_file_sequence_icon(index)

    assert len(calls) == len(set(calls))
    assert len(calls) <= 6
    clear_tree_color_caches()


def test_db_backed_private_bulk_apply_persists_in_one_transaction(tmp_path, monkeypatch):
    db = UnshuffleDB(tmp_path / "test.db")
    try:
        db.register_session("session", Path("D:/Samples"), Path("D:/Target"), "pending")
        db.add_staging_records_bulk("session", [_row(0), _row(1)])
        store = StagingSessionStore(db, "session")
        model = DbBackedStagingTableModel(store)
        records = [model.record(0), model.record(1)]
        bulk_calls = []
        original_update = store.update_rows

        def tracking_update(updates):
            updates = list(updates)
            bulk_calls.append(updates)
            assert all("feature_vector" not in fields for _row_id, fields in updates)
            return original_update(updates)

        monkeypatch.setattr(store, "update_rows", tracking_update)
        model._apply_bulk_values([
            (records[0], StagingColumn.CATEGORY, "Snares"),
            (records[1], StagingColumn.CATEGORY, "Claps"),
        ])

        rows = {row["row_id"]: row for row in db.get_staging_records("session")}
        assert rows[records[0].staging_row_id]["category"] == "Snares"
        assert rows[records[1].staging_row_id]["category"] == "Claps"
        assert len(bulk_calls) == 1
        assert len(bulk_calls[0]) == 2
    finally:
        db.close()


def test_db_backed_draft_discard_restores_record_and_staging_row(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QObject
    from PySide6.QtWidgets import QApplication
    from gui.core.drafting_controller import DraftingController

    _app = QApplication.instance() or QApplication([])
    db = UnshuffleDB(tmp_path / "test.db")
    try:
        db.register_session("session", Path("D:/Samples"), Path("D:/Target"), "pending")
        db.add_staging_records_bulk("session", [_row(0, category="Kicks")])
        model = DbBackedStagingTableModel(StagingSessionStore(db, "session"))
        record = model.record(0)

        class FakeFooter:
            def set_reorg_draft_state(self, *_args, **_kwargs):
                pass

            def log(self, _message):
                pass

            def toggle_footer(self, _visible):
                pass

        class FakeViewController:
            def update_library_views(self, tree_delay_ms=0):
                pass

        class FakeApp(QObject):
            def __init__(self):
                super().__init__()
                self.model = model
                self.footer = FakeFooter()
                self.view_controller = FakeViewController()

        controller = DraftingController(FakeApp())
        controller.stage_updates([(record, StagingColumn.CATEGORY, "Snares")])
        model._apply_bulk_values([(record, StagingColumn.CATEGORY, "Snares")])

        controller.discard_reorg_draft(confirm=False)

        assert record.category == "Kicks"
        assert db.get_staging_records("session")[0]["category"] == "Kicks"
    finally:
        db.close()


def test_layout_bulk_update_monitor_keeps_qt_event_loop_responsive(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QObject, QTimer
    from PySide6.QtWidgets import QApplication
    from gui.core.drafting_controller import DraftingController

    _app = QApplication.instance() or QApplication([])
    db = UnshuffleDB(tmp_path / "test.db")
    try:
        db.register_session("session", Path("D:/Samples"), Path("D:/Target"), "pending")
        db.add_staging_records_bulk("session", [_row(0, category="Kicks")])
        model = DbBackedStagingTableModel(StagingSessionStore(db, "session"))
        record = model.record(0)
        monitor_calls = []

        class FakeMonitor:
            def start(self, title, **_kwargs):
                monitor_calls.append(("start", title))
                return 9

            def update(self, payload, **_kwargs):
                monitor_calls.append(("update", payload.get("current")))

            def finish(self, text=None, **_kwargs):
                monitor_calls.append(("finish", text))

            def fail(self, text, **_kwargs):
                monitor_calls.append(("fail", text))

        class FakeApp(QObject):
            def __init__(self):
                super().__init__()
                self.model = model
                self.operation_monitor = FakeMonitor()

        controller = DraftingController(FakeApp())
        event_loop_ticks = []
        QTimer.singleShot(0, lambda: event_loop_ticks.append(True))

        controller._apply_model_bulk_values(
            [(record, StagingColumn.CATEGORY, "Snares")],
            operation_title="Updating Library Layout",
            completion_text="Library layout updated.",
        )

        assert event_loop_ticks == [True]
        assert db.get_staging_records("session")[0]["category"] == "Snares"
        assert monitor_calls[0] == ("start", "Updating Library Layout")
        assert monitor_calls[-1] == ("finish", "Library layout updated.")
    finally:
        db.close()


def test_layout_bulk_update_cancel_rolls_back_model_and_database(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QObject
    from PySide6.QtWidgets import QApplication
    from gui.core.drafting_controller import DraftingController

    _app = QApplication.instance() or QApplication([])
    db = UnshuffleDB(tmp_path / "test.db")
    try:
        db.register_session("session", Path("D:/Samples"), Path("D:/Target"), "pending")
        db.add_staging_records_bulk("session", [_row(0, category="Kicks")])
        model = DbBackedStagingTableModel(StagingSessionStore(db, "session"))
        record = model.record(0)
        finishes = []

        class FakeMonitor:
            def start(self, _title, **kwargs):
                assert kwargs["cancellable"] is True
                kwargs["on_cancel"]()
                return 4

            def update(self, _payload, **_kwargs):
                pass

            def finish(self, text=None, **_kwargs):
                finishes.append(text)

            def fail(self, text, **_kwargs):
                pytest.fail(text)

        class FakeApp(QObject):
            def __init__(self):
                super().__init__()
                self.model = model
                self.operation_monitor = FakeMonitor()

        controller = DraftingController(FakeApp())
        applied = controller._apply_model_bulk_values(
            [(record, StagingColumn.CATEGORY, "Snares")],
            operation_title="Updating Library Layout",
        )

        assert applied is False
        assert record.category == "Kicks"
        assert db.get_staging_records("session")[0]["category"] == "Kicks"
        assert finishes == ["Operation canceled."]
    finally:
        db.close()


def test_store_iterates_requested_rows_in_bounded_ordered_batches(tmp_path):
    db = UnshuffleDB(tmp_path / "test.db")
    try:
        db.register_session("session", Path("D:/Samples"), Path("D:/Target"), "pending")
        db.add_staging_records_bulk("session", [_row(index) for index in range(7)])
        store = StagingSessionStore(db, "session")

        batches = list(store.iter_rows_by_ids([6, 1, 4, 2], "row_id, sample_name", batch_size=2))

        assert [len(batch) for batch in batches] == [2, 2]
        assert [row["row_id"] for batch in batches for row in batch] == [6, 1, 4, 2]
    finally:
        db.close()


def test_custom_tree_projection_repairs_only_the_updated_staging_row(tmp_path, monkeypatch):
    db = UnshuffleDB(tmp_path / "test.db")
    try:
        db.register_session("session", Path("D:/Samples"), Path("D:/Target"), "pending")
        row = list(_row(0, category="Kicks"))
        row[7] = "favorite"
        other_row = list(_row(1, category="Snares"))
        other_row[7] = "favorite"
        db.add_staging_records_bulk("session", [tuple(row), tuple(other_row)])
        profile = TreeOrganizationProfile(
            "profile",
            "Custom",
            "root",
            [
                TreeOrganizationNode("root", None, "Root", None, "system", 0, True),
                TreeOrganizationNode("favorite", "root", "Favorite", 'tag:"favorite"', "custom", 1, True),
            ],
            "now",
            "now",
        )
        store = StagingSessionStore(db, "session")
        signature = store.ensure_custom_tree_projection(
            profile,
            [("audio_type", "type"), ("category", "category")],
        )
        assert store.custom_tree_child_counts(profile.id, signature, "", None)

        store.update_row(0, {"tags": ""})

        projected_rows = db.conn.execute(
            "SELECT DISTINCT row_id FROM custom_tree_memberships WHERE session_id = ? ORDER BY row_id",
            ("session",),
        ).fetchall()
        assert [int(projected[0]) for projected in projected_rows] == [1]

        routed_row_ids = []
        original_iter = store.iter_tree_records_by_ids

        def track_rows(row_ids, *, batch_size=1000):
            routed_row_ids.extend(row_ids)
            yield from original_iter(row_ids, batch_size=batch_size)

        monkeypatch.setattr(store, "iter_tree_records_by_ids", track_rows)
        store.ensure_custom_tree_projection(
            profile,
            [("audio_type", "type"), ("category", "category")],
        )

        assert routed_row_ids == [0]
        projected_rows = db.conn.execute(
            "SELECT DISTINCT row_id FROM custom_tree_memberships WHERE session_id = ? ORDER BY row_id",
            ("session",),
        ).fetchall()
        assert [int(projected[0]) for projected in projected_rows] == [0, 1]
    finally:
        db.close()


def test_projection_cleanup_retains_active_root_cache_and_other_profiles(tmp_path):
    db = UnshuffleDB(tmp_path / "test.db")
    try:
        db.register_session("session", Path("D:/Samples"), Path("D:/Target"), "pending")
        store = StagingSessionStore(db, "session")
        active_key = ("profile-a", "active", "", None)
        stale_key = ("profile-a", "stale", "", None)
        other_key = ("profile-b", "active", "", None)
        store._custom_child_count_cache[active_key] = ({"label": "Active"},)
        store._custom_child_count_cache[stale_key] = ({"label": "Stale"},)
        store._custom_child_count_cache[other_key] = ({"label": "Other"},)

        store.clear_custom_tree_projections("profile-a", keep_signature="active")

        assert active_key in store._custom_child_count_cache
        assert stale_key not in store._custom_child_count_cache
        assert other_key in store._custom_child_count_cache
    finally:
        db.close()


def test_opening_existing_database_replaces_session_wide_projection_trigger(tmp_path):
    db_path = tmp_path / "test.db"
    db = UnshuffleDB(db_path)
    try:
        db.conn.executescript(
            """
            DROP TRIGGER custom_tree_memberships_staging_au;
            CREATE TRIGGER custom_tree_memberships_staging_au
            AFTER UPDATE ON staging_records BEGIN
                DELETE FROM custom_tree_memberships WHERE session_id = new.session_id;
            END;
            """
        )
    finally:
        db.close()

    reopened = UnshuffleDB(db_path)
    try:
        trigger_sql = reopened.conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'trigger' "
            "AND name = 'custom_tree_memberships_staging_au'"
        ).fetchone()[0]
        assert "row_id = old.row_id" in trigger_sql
        assert "row_id = new.row_id" in trigger_sql
    finally:
        reopened.close()


def test_custom_tree_projection_worker_builds_projection_off_controller_path(tmp_path):
    from gui.core.workers import CustomTreeProjectionWorker

    db = UnshuffleDB(tmp_path / "test.db")
    try:
        db.register_session("session", Path("D:/Samples"), Path("D:/Target"), "pending")
        db.add_staging_records_bulk("session", [_row(0), _row(1, category="Snares")])
        profile = TreeOrganizationProfile(
            "profile",
            "Custom",
            "root",
            [
                TreeOrganizationNode("root", None, "Root", None, "system", 0, True),
                TreeOrganizationNode("kicks", "root", "Kicks", 'cat:"Kicks"', "custom", 1, True),
            ],
            "now",
            "now",
        )
        payloads = []
        worker = CustomTreeProjectionWorker(
            7,
            db,
            "session",
            profile,
            [("audio_type", "type"), ("category", "category")],
            0.0,
            True,
        )
        worker.finished.connect(payloads.append)

        worker.run()

        assert payloads[0]["request_id"] == 7
        assert payloads[0]["profile_id"] == profile.id
        assert StagingSessionStore(db, "session").has_custom_tree_projection(
            profile.id,
            payloads[0]["signature"],
        )
    finally:
        db.close()


def test_partial_custom_tree_projection_is_rebuilt_before_reuse(tmp_path):
    db = UnshuffleDB(tmp_path / "test.db")
    try:
        db.register_session("session", Path("D:/Samples"), Path("D:/Target"), "pending")
        db.add_staging_records_bulk("session", [_row(0), _row(1, category="Snares")])
        profile = TreeOrganizationProfile(
            "profile",
            "Custom",
            "root",
            [
                TreeOrganizationNode("root", None, "Root", None, "system", 0, True),
                TreeOrganizationNode("kicks", "root", "Kicks", 'cat:"Kicks"', "custom", 1, True),
            ],
            "now",
            "now",
        )
        store = StagingSessionStore(db, "session")
        levels = [("audio_type", "type"), ("category", "category")]
        signature = store.ensure_custom_tree_projection(profile, levels)
        db.conn.execute(
            "DELETE FROM custom_tree_memberships WHERE session_id = ? AND row_id = ?",
            ("session", 0),
        )
        db.conn.commit()

        assert not store.has_custom_tree_projection(profile.id, signature)
        store.ensure_custom_tree_projection(profile, levels)

        projected = db.conn.execute(
            "SELECT COUNT(DISTINCT row_id) FROM custom_tree_memberships "
            "WHERE session_id = ? AND profile_id = ? AND projection_signature = ?",
            ("session", profile.id, signature),
        ).fetchone()[0]
        assert projected == 2
    finally:
        db.close()


def test_custom_tree_projection_signature_ignores_profile_metadata():
    profile = TreeOrganizationProfile(
        "profile",
        "First Name",
        "root",
        [
            TreeOrganizationNode("root", None, "Root", None, "system", 0, True),
            TreeOrganizationNode("kicks", "root", "Kicks", 'cat:"Kicks"', "custom", 1, True),
        ],
        "created-a",
        "updated-a",
    )
    renamed = replace(profile, name="Second Name", updated_at="updated-b")
    rerouted = replace(
        profile,
        nodes=[profile.nodes[0], replace(profile.nodes[1], filter_query='cat:"Snares"')],
        updated_at="updated-c",
    )
    levels = [("audio_type", "type"), ("category", "category")]

    original_signature = StagingSessionStore.custom_tree_projection_signature(
        profile,
        levels,
        confidence_floor=0.0,
        confidence_filter_enabled=True,
    )

    assert StagingSessionStore.custom_tree_projection_signature(
        renamed,
        levels,
        confidence_floor=0.0,
        confidence_filter_enabled=True,
    ) == original_signature
    assert StagingSessionStore.custom_tree_projection_signature(
        rerouted,
        levels,
        confidence_floor=0.0,
        confidence_filter_enabled=True,
    ) != original_signature


def test_custom_tree_preview_counts_do_not_write_projection_rows(tmp_path):
    db = UnshuffleDB(tmp_path / "test.db")
    try:
        db.register_session("session", Path("D:/Samples"), Path("D:/Target"), "pending")
        favorite = list(_row(0, category="Kicks"))
        favorite[7] = "favorite"
        db.add_staging_records_bulk("session", [tuple(favorite), _row(1, category="Snares")])
        store = StagingSessionStore(db, "session")
        profile = TreeOrganizationProfile(
            "profile",
            "Custom",
            "root",
            [
                TreeOrganizationNode("root", None, "Root", None, "system", 0, True),
                TreeOrganizationNode("favorites", "root", "Favorites", 'tag:"favorite"', "custom", 1, True),
                TreeOrganizationNode("other", "root", "Other", None, "fallback", 2, True),
            ],
            "now",
            "now",
        )

        counts = store.preview_custom_tree_node_counts(
            profile,
            [("audio_type", "type"), ("category", "category")],
        )

        assert counts["root"] == 2
        assert counts["favorites"] == 1
        assert counts["other"] == 1
        assert db.conn.execute("SELECT COUNT(*) FROM custom_tree_memberships").fetchone()[0] == 0
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


def test_db_backed_initial_all_query_includes_utility(tmp_path):
    from PySide6.QtWidgets import QApplication

    _app = QApplication.instance() or QApplication([])
    db = UnshuffleDB(tmp_path / "test.db")
    try:
        db.register_session("session", Path("D:/Samples"), Path("D:/Target"), "pending")
        row = list(_row(0, audio_type="Non-Audio Assets", category="Non-Audio Assets"))
        row[1] = "D:/Samples/Pack/cover.jpg"
        row[2] = "cover.jpg"
        db.add_staging_records_bulk("session", [tuple(row)])
        model = DbBackedStagingTableModel(StagingSessionStore(db, "session"))
        model.set_show_non_audio_assets(True)
        tree = LibraryTreeModel()

        tree.rebuild_from_store(model.store, model.query)

        assert tree.invisibleRootItem().child(0, 0).data(RAW_NAME_ROLE) == "Utility"
    finally:
        db.close()


def test_custom_utility_leaf_collapses_other_and_fetches_in_chunks(tmp_path):
    from PySide6.QtWidgets import QApplication

    _app = QApplication.instance() or QApplication([])
    db = UnshuffleDB(tmp_path / "test.db")
    try:
        db.register_session("session", Path("D:/Samples"), Path("D:/Target"), "pending")
        rows = []
        for index in range(600):
            row = list(_row(index, audio_type="Non-Audio Assets", category="Non-Audio Assets"))
            row[1] = f"D:/Samples/Docs/asset_{index}.pdf"
            row[2] = f"asset_{index}.pdf"
            rows.append(tuple(row))
        db.add_staging_records_bulk("session", rows)
        profile = TreeOrganizationProfile(
            "profile",
            "Custom",
            "root",
            [
                TreeOrganizationNode("root", None, "Root", None, "system", 0, True),
                TreeOrganizationNode("utility", "root", "Utility", 'type:"Non-Audio Assets"', "system", 1, True),
                TreeOrganizationNode("assets", "utility", "Non-Audio Assets", 'cat:"Non-Audio Assets"', "system", 1, True),
                TreeOrganizationNode("other", "assets", "Other", None, "fallback", 1, True),
            ],
            "now",
            "now",
        )
        tree = LibraryTreeModel()
        tree.set_custom_tree_profile(profile)
        store = StagingSessionStore(db, "session")
        tree.rebuild_from_store(store)
        utility = tree.invisibleRootItem().child(0, 0)
        tree.populate_index(tree.indexFromItem(utility))
        assets = utility.child(0, 0)

        tree.populate_index(tree.indexFromItem(assets))
        pack = assets.child(0, 0)
        assert pack.data(RAW_NAME_ROLE) == "Pack"
        assert all(assets.child(row, 0).data(RAW_NAME_ROLE) != "Other" for row in range(assets.rowCount()))
        tree.populate_index(tree.indexFromItem(pack))

        assert pack.rowCount() == tree.LEAF_BATCH_SIZE
        pack_index = tree.indexFromItem(pack)
        assert len(tree.records_for_index(pack_index)) == 600
        assert tree.canFetchMore(pack_index)
        tree.fetchMore(pack_index)
        assert pack.rowCount() == tree.LEAF_BATCH_SIZE * 2
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


def test_db_backed_tree_applies_nested_custom_profile(tmp_path):
    from PySide6.QtWidgets import QApplication

    _app = QApplication.instance() or QApplication([])
    db = UnshuffleDB(tmp_path / "test.db")
    try:
        db.register_session("session", Path("D:/Samples"), Path("D:/Target"), "pending")
        favorite = list(_row(0, category="Kicks"))
        favorite[7] = "favorite"
        ordinary = list(_row(1, category="Snares"))
        db.add_staging_records_bulk("session", [tuple(favorite), tuple(ordinary)])
        profile = TreeOrganizationProfile(
            "profile",
            "Custom",
            "root",
            [
                TreeOrganizationNode("root", None, "Root", None, "system", 0, True),
                TreeOrganizationNode("oneshots", "root", "Oneshots", 'type:"Oneshots"', "system", 1, True),
                TreeOrganizationNode("favorites", "oneshots", "Favorites", 'tag:"favorite"', "custom", 1, True),
            ],
            "now",
            "now",
        )
        tree = LibraryTreeModel()
        tree.set_custom_tree_profile(profile)
        tree.rebuild_from_store(StagingSessionStore(db, "session"))

        root = tree.invisibleRootItem()
        assert root.child(0, 0).data(RAW_NAME_ROLE) == "Oneshots"
        oneshots = root.child(0, 0)
        tree.populate_index(tree.indexFromItem(oneshots))
        labels = {
            oneshots.child(row, 0).data(RAW_NAME_ROLE)
            for row in range(oneshots.rowCount())
        }
        assert {"Favorites", "Snares"} <= labels
    finally:
        db.close()


def test_db_backed_tree_prefers_nested_custom_bucket_over_broad_root_bucket(tmp_path):
    from PySide6.QtWidgets import QApplication

    _app = QApplication.instance() or QApplication([])
    db = UnshuffleDB(tmp_path / "test.db")
    try:
        db.register_session("session", Path("D:/Samples"), Path("D:/Target"), "pending")
        possible_duplicate = list(_row(0, category="Bass", audio_type="Loops"))
        possible_duplicate[7] = "possibleduplicate"
        db.add_staging_records_bulk("session", [tuple(possible_duplicate)])
        profile = TreeOrganizationProfile(
            "profile",
            "Custom",
            "root",
            [
                TreeOrganizationNode("root", None, "Root", None, "system", 0, True),
                TreeOrganizationNode("loops", "root", "Loops", 'type:"Loops"', "system", 1, True),
                TreeOrganizationNode(
                    "loop_dupes", "loops", "Loop Dupes", 'tag:"possibleduplicate"', "custom", 1, True
                ),
                TreeOrganizationNode(
                    "all_dupes", "root", "All Dupes", 'tag:"possibleduplicate"', "custom", 2, True
                ),
            ],
            "now",
            "now",
        )
        tree = LibraryTreeModel()
        tree.set_custom_tree_profile(profile)
        tree.rebuild_from_store(StagingSessionStore(db, "session"))

        root = tree.invisibleRootItem()
        assert root.child(0, 0).data(RAW_NAME_ROLE) == "Loops"
        loops = root.child(0, 0)
        tree.populate_index(tree.indexFromItem(loops))
        assert loops.child(0, 0).data(RAW_NAME_ROLE) == "Loop Dupes"
    finally:
        db.close()


def test_custom_tree_projection_table_is_available_without_rescan(tmp_path):
    db = UnshuffleDB(tmp_path / "test.db")
    try:
        columns = {
            str(row[1])
            for row in db.conn.execute("PRAGMA table_info(custom_tree_memberships)")
        }
        assert {"session_id", "profile_id", "projection_signature", "route_key", "row_id"} <= columns
    finally:
        db.close()


def test_effective_custom_category_is_exclusive_in_table_options_and_search(tmp_path):
    db = UnshuffleDB(tmp_path / "test.db")
    try:
        db.register_session("session", Path("D:/Samples"), Path("D:/Target"), "pending")
        duplicate = list(_row(0, category="Bass", audio_type="Loops"))
        duplicate[7] = "possibleduplicate"
        ordinary = list(_row(1, category="Bass", audio_type="Loops"))
        ordinary[1] = "D:/Samples/Pack/ordinary.wav"
        ordinary[2] = "ordinary.wav"
        db.add_staging_records_bulk("session", [tuple(duplicate), tuple(ordinary)])
        profile = TreeOrganizationProfile(
            "profile",
            "Custom",
            "root",
            [
                TreeOrganizationNode("root", None, "Root", None, "system", 0, True),
                TreeOrganizationNode("loops", "root", "Loops", 'type:"Loops"', "system", 1, True),
                TreeOrganizationNode(
                    "dupes", "loops", "Dupes", 'tag:"possibleduplicate"', "custom", 1, True
                ),
            ],
            "now",
            "now",
        )
        store = StagingSessionStore(db, "session")
        levels = [("audio_type", "type"), ("category", "category"), ("subcategory", "subcategory")]
        signature = store.ensure_custom_tree_projection(profile, levels)
        options = custom_tree_filter_options(profile, store.custom_tree_node_counts(profile.id, signature))
        context = EffectiveTaxonomyContext(profile.id, signature, tuple(options))
        model = DbBackedStagingTableModel(store)
        model.set_effective_taxonomy_context(context)
        duplicate_row = model._row_positions[0]
        ordinary_row = model._row_positions[1]

        assert model.data(model.index(duplicate_row, StagingColumn.CATEGORY), Qt.DisplayRole) == "Dupes - Bass"
        assert model.data(model.index(duplicate_row, StagingColumn.CATEGORY), Qt.EditRole) == "Bass"
        assert model.data(model.index(ordinary_row, StagingColumn.CATEGORY), Qt.DisplayRole) == "Bass"

        category_options = model.taxonomy_options_for_index(
            model.index(duplicate_row, StagingColumn.CATEGORY),
            StagingColumn.CATEGORY,
        )
        assert ("Dupes - Bass", "Bass") in category_options
        assert ("Bass", "Bass") not in category_options

        assert store.effective_taxonomy_match_ids("category", "Dupes-Bass", context) == {0}
        assert store.effective_taxonomy_match_ids("category", "Dupes", context) == set()
        assert store.effective_taxonomy_match_ids("category", "Bass", context) == {1}

        counts = store.effective_taxonomy_group_counts(context)
        by_category = {row["category"]: row["count"] for row in counts}
        assert by_category == {"Dupes - Bass": 1, "Bass": 1}

        model.sort(StagingColumn.CATEGORY, Qt.AscendingOrder)
        assert [
            model.data(model.index(row, StagingColumn.CATEGORY), Qt.DisplayRole)
            for row in range(model.rowCount())
        ] == ["Bass", "Dupes - Bass"]

        model.set_column_filters(StagingColumn.CATEGORY, {"Dupes - Bass"})
        assert model.rowCount() == 1
        assert model.data(model.index(0, StagingColumn.CATEGORY), Qt.DisplayRole) == "Dupes - Bass"
        model.set_column_filters(StagingColumn.CATEGORY, {"Bass"})
        assert model.rowCount() == 1
        assert model.data(model.index(0, StagingColumn.CATEGORY), Qt.DisplayRole) == "Bass"
    finally:
        db.close()


def test_effective_custom_subcategory_reconciles_other_exclusively(tmp_path):
    db = UnshuffleDB(tmp_path / "test.db")
    try:
        db.register_session("session", Path("D:/Samples"), Path("D:/Target"), "pending")
        picked = list(_row(0, category="Bass", audio_type="Oneshots"))
        picked[7] = "picked"
        db.add_staging_records_bulk("session", [tuple(picked)])
        profile = TreeOrganizationProfile(
            "profile",
            "Custom",
            "root",
            [
                TreeOrganizationNode("root", None, "Root", None, "system", 0, True),
                TreeOrganizationNode("oneshots", "root", "Oneshots", 'type:"Oneshots"', "system", 1, True),
                TreeOrganizationNode("bass", "oneshots", "Bass", 'category:"Bass"', "system", 1, True),
                TreeOrganizationNode("picked", "bass", "Picked", 'tag:"picked"', "custom", 1, True),
            ],
            "now",
            "now",
        )
        store = StagingSessionStore(db, "session")
        levels = [("audio_type", "type"), ("category", "category"), ("subcategory", "subcategory")]
        signature = store.ensure_custom_tree_projection(profile, levels)
        context = EffectiveTaxonomyContext(
            profile.id,
            signature,
            tuple(custom_tree_filter_options(profile, store.custom_tree_node_counts(profile.id, signature))),
        )
        model = DbBackedStagingTableModel(store)
        model.set_effective_taxonomy_context(context)
        row = model._row_positions[0]

        assert model.data(model.index(row, StagingColumn.SUBCATEGORY), Qt.DisplayRole) == "Picked - Other"
        assert model.data(model.index(row, StagingColumn.SUBCATEGORY), Qt.EditRole) in {None, ""}
        options = model.taxonomy_options_for_index(
            model.index(row, StagingColumn.SUBCATEGORY),
            StagingColumn.SUBCATEGORY,
        )
        assert ("Picked - Other", "") in options
        assert ("Other", "") not in options
        assert store.effective_taxonomy_match_ids("subcategory", "Picked-Other", context) == {0}
        assert store.effective_taxonomy_match_ids("subcategory", "Other", context) == set()
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


def test_db_backed_model_uses_small_ui_hydration_windows(tmp_path):
    db = UnshuffleDB(tmp_path / "test.db")
    try:
        db.register_session("session", Path("D:/Samples"), Path("D:/Target"), "pending")
        db.add_staging_records_bulk("session", [_row(i) for i in range(600)])
        store = StagingSessionStore(db, "session")
        model = DbBackedStagingTableModel(store)

        model.record(0)

        assert model.CHUNK_SIZE == 256
        assert len(model._record_cache._cache) == 256
    finally:
        db.close()


def test_db_backed_flags_do_not_hydrate_records(tmp_path, monkeypatch):
    db = UnshuffleDB(tmp_path / "test.db")
    try:
        db.register_session("session", Path("D:/Samples"), Path("D:/Target"), "pending")
        normal = list(_row(1))
        shadow = list(_row(2))
        shadow[13] = json.dumps({"duplicate_shadow": {"is_shadow": True}})
        db.add_staging_records_bulk("session", [tuple(normal), tuple(shadow)])
        model = DbBackedStagingTableModel(StagingSessionStore(db, "session"))
        monkeypatch.setattr(model, "record", lambda _row: (_ for _ in ()).throw(AssertionError("flags hydrated a record")))

        normal_flags = model.flags(model.index(0, StagingColumn.PACK))
        shadow_flags = model.flags(model.index(1, StagingColumn.PACK))

        assert normal_flags & Qt.ItemFlag.ItemIsEditable
        assert not shadow_flags & Qt.ItemFlag.ItemIsEditable
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


def test_large_query_id_sets_use_single_json_bind_parameter(tmp_path):
    db = UnshuffleDB(tmp_path / "large-query.db")
    try:
        store = StagingSessionStore(db, "session")
        query = StagingQuery(
            matched_ids=frozenset(range(1, 12001)),
            similarity_rows=frozenset(range(6000, 18001)),
        )

        where, params = store._where(query)

        assert where.count("json_each(?)") == 2
        assert len(params) == 3
        assert len(json.loads(params[1])) == 12000
        assert len(json.loads(params[2])) == 12001
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


def test_acoustic_session_state_streams_digest_without_hydrating_rows(tmp_path, monkeypatch):
    db = UnshuffleDB(tmp_path / "test.db")
    try:
        db.register_session("session", Path("D:/Samples"), Path("D:/Target"), "pending")
        db.add_staging_records_bulk("session", [_row(i) for i in range(5)])
        store = StagingSessionStore(db, "session")
        model = DbBackedStagingTableModel(store)
        monkeypatch.setattr(
            store,
            "acoustic_state_rows",
            lambda: (_ for _ in ()).throw(AssertionError("rows must not be materialized")),
        )
        app = type(
            "App",
            (),
            {"model": model, "engine": type("Engine", (), {"session_id": "session"})()},
        )()
        state = AcousticSessionState(app)

        first = state.current_key()
        second = state.current_key()
        store.update_row(2, {"category": "Changed"})
        changed = state.current_key()

        assert first
        assert second == first
        assert changed != first
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


def test_db_backed_buildable_records_stream_and_exclude_duplicate_shadows(tmp_path):
    from gui.core.workflow_records import buildable_records

    db = UnshuffleDB(tmp_path / "test.db")
    try:
        db.register_session("session", Path("D:/Samples"), Path("D:/Target"), "pending")
        db.add_staging_records_bulk("session", [_row(i) for i in range(5)])
        db.conn.execute(
            "UPDATE staging_records SET evidence_json = ? WHERE session_id = ? AND row_id = ?",
            ('{"duplicate_shadow":{"is_shadow":true}}', "session", 2),
        )
        db.conn.commit()
        model = DbBackedStagingTableModel(StagingSessionStore(db, "session"))

        records = buildable_records(model.records)

        assert hasattr(records, "store")
        assert len(records) == 4
        assert [record.staging_row_id for record in records] == [0, 1, 3, 4]
    finally:
        db.close()


def test_db_backed_build_stream_can_include_duplicate_shadows(tmp_path):
    from gui.core.staging_session_store import BuildableDbRecordSequence

    db = UnshuffleDB(tmp_path / "test.db")
    try:
        db.register_session("session", Path("D:/Samples"), Path("D:/Target"), "pending")
        db.add_staging_records_bulk("session", [_row(i) for i in range(3)])
        db.conn.execute(
            "UPDATE staging_records SET evidence_json = ? WHERE session_id = ? AND row_id = ?",
            ('{"duplicate_shadow":{"is_shadow":true}}', "session", 1),
        )
        db.conn.commit()
        records = BuildableDbRecordSequence(
            StagingSessionStore(db, "session"),
            include_duplicate_shadows=True,
        )

        assert len(records) == 3
        assert [record.staging_row_id for record in records] == [0, 1, 2]
        assert list(records)[1].is_duplicate_shadow is True
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


def test_db_backed_cleanup_chunks_large_deleted_path_sets(tmp_path):
    import sqlite3

    db = UnshuffleDB(tmp_path / "test.db")
    try:
        db.register_session("session", Path("D:/Samples"), Path("D:/Target"), "pending")
        rows = [_row(index) for index in range(1200)]
        db.add_staging_records_bulk("session", rows)
        model = DbBackedStagingTableModel(StagingSessionStore(db, "session"))
        db.conn.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 999)

        removed = workflow_model_cleanup.remove_deleted_paths(
            model,
            [Path(row[1]) for row in rows],
        )

        assert removed == 1200
        assert model.rowCount() == 0
    finally:
        db.close()
