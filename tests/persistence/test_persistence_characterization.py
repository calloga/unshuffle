import json
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from unittest import mock

from unshuffle.logic.planning.service import _extractor_worker_count, run_plan
from unshuffle.audio.acoustic import FeaturePayload, SimilarityEngine
from unshuffle.core.features import CURRENT_FEATURE_SCHEMA
from unshuffle.bridge.persistence_bridge import PersistenceBridge
from unshuffle.bridge.search_bridge import SearchBridge
from unshuffle.bridge.workflow_bridge import WorkflowBridge, create_workflow_bridge
from gui.core.data_manager import DataManager
from gui.utils import history as history_queries
from unshuffle.core import load_config
from unshuffle.core.constants import ALIAS_TABLE, get_runtime_config_snapshot
from unshuffle.persistence import UnshuffleDB
from unshuffle.persistence import load_json_meta, save_json_meta, sync_full_config
from unshuffle.persistence.schema.schema import migrations_up


class PersistenceTests(unittest.TestCase):
    def test_session_metadata_round_trips_json_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = UnshuffleDB(Path(tmp) / "session_metadata.db")
            try:
                db.register_session("session-a", Path("Source"), Path("Target"), "pending")
                db.set_session_metadata("session-a", "saved_filters", '[{"name": "Kicks", "query": "cat:\\"Kicks\\""}]')

                self.assertEqual(
                    db.get_session_metadata("session-a", "saved_filters"),
                    '[{"name": "Kicks", "query": "cat:\\"Kicks\\""}]',
                )
            finally:
                db.close()

    def test_history_queries_cache_and_invalidate_session_lists(self):
        entered = []

        class _FakeDB:
            def __enter__(self):
                entered.append(True)
                return self

            def __exit__(self, exc_type, exc, tb):
                return None

            def get_recent_sessions(self, limit=10, only_executed=False):
                return [{"session_id": "s1", "file_count": 1 if only_executed else 0}]

        history_queries.invalidate_history_cache()
        with mock.patch("gui.utils.history.get_db", return_value=_FakeDB()):
            first = history_queries.load_executed_sessions("C:/Library", limit=1)
            second = history_queries.load_executed_sessions("C:/Library", limit=1)

        self.assertEqual(first, second)
        self.assertEqual(len(entered), 1)

        history_queries.invalidate_history_cache("C:/Library")
        with mock.patch("gui.utils.history.get_db", return_value=_FakeDB()):
            history_queries.load_executed_sessions("C:/Library", limit=1)
        self.assertEqual(len(entered), 2)

    def test_history_session_invalidation_clears_executed_session_list_cache(self):
        calls = []

        class _FakeDB:
            def __init__(self, rows):
                self.rows = rows

            def __enter__(self):
                calls.append(tuple(row["history_state"] for row in self.rows))
                return self

            def __exit__(self, exc_type, exc, tb):
                return None

            def get_recent_sessions(self, limit=10, only_executed=False, target_root=None):
                return list(self.rows)

        history_queries.invalidate_history_cache()
        first_db = _FakeDB([{"session_id": "s1", "file_count": 1, "history_state": "undoable"}])
        second_db = _FakeDB([{"session_id": "s1", "file_count": 1, "history_state": "undone"}])

        with mock.patch("gui.utils.history.get_db", return_value=first_db):
            first = history_queries.load_executed_sessions("C:/Library", limit=1)
        history_queries.invalidate_history_cache("C:/Library", "s1")
        with mock.patch("gui.utils.history.get_db", return_value=second_db):
            second = history_queries.load_executed_sessions("C:/Library", limit=1)

        self.assertEqual(first[0]["history_state"], "undoable")
        self.assertEqual(second[0]["history_state"], "undone")
        self.assertEqual(calls, [("undoable",), ("undone",)])

    def test_resolve_history_target_backfills_latest_global_target_when_active_target_has_no_rows(self):
        class _Settings:
            def __init__(self):
                self.values = {"last_history_target": "", "last_target": "D:/Source"}
                self.set_calls = []

            def value(self, key, default=""):
                return self.values.get(key, default)

            def setValue(self, key, value):
                self.values[key] = value
                self.set_calls.append((key, value))

        settings = _Settings()

        with mock.patch("gui.utils.history.load_executed_sessions", return_value=[]), \
             mock.patch("gui.utils.history.load_latest_history_target", return_value="D:/Target"):
            target = history_queries.resolve_history_target(settings)

        self.assertEqual(target, "D:/Target")
        self.assertEqual(settings.set_calls, [("last_history_target", "D:/Target")])

    def test_clear_migration_history_clears_global_and_local_sidecar_scopes(self):
        cleared = []

        class _FakeDB:
            def __init__(self, name):
                self.name = name

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return None

            def clear_history_for_target(self, target_root):
                cleared.append((self.name, str(target_root)))

        history_queries.invalidate_history_cache()
        with mock.patch("gui.utils.history.get_db", return_value=_FakeDB("global")), \
             mock.patch("gui.utils.history._local_db_exists", return_value=True), \
             mock.patch("gui.utils.history.get_local_db", return_value=_FakeDB("local")):
            history_queries.clear_migration_history("D:/Target")

        self.assertEqual([name for name, _target in cleared], ["global", "local"])
        self.assertTrue(all(Path(target) == Path("D:/Target") for _name, target in cleared))

    def test_history_query_passes_selected_target_filter(self):
        seen = {}

        class _FakeDB:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return None

            def get_recent_sessions(self, limit=10, only_executed=False, target_root=None):
                seen["limit"] = limit
                seen["only_executed"] = only_executed
                seen["target_root"] = target_root
                return [{"session_id": "s1", "file_count": 1}]

        history_queries.invalidate_history_cache()
        with mock.patch("gui.utils.history.get_db", return_value=_FakeDB()):
            history_queries.load_executed_sessions("C:/Library", limit=3)

        self.assertEqual(seen["limit"], 3)
        self.assertTrue(seen["only_executed"])
        self.assertEqual(seen["target_root"], Path("C:/Library"))

    def test_history_queries_merge_local_and_global_executed_sessions(self):
        calls = []

        class _FakeDB:
            def __init__(self, rows):
                self.rows = rows
                self.closed = False

            def get_recent_sessions(self, limit=10, only_executed=False, target_root=None):
                calls.append((tuple(row["session_id"] for row in self.rows), limit, only_executed, target_root))
                return list(self.rows)

            def close(self):
                self.closed = True

        local = _FakeDB([
            {"session_id": "local-session", "timestamp": "2026-06-02T00:00:00", "file_count": 1},
        ])
        global_db = _FakeDB([
            {"session_id": "global-session", "timestamp": "2026-06-01T00:00:00", "file_count": 1},
        ])

        history_queries.invalidate_history_cache()
        with mock.patch("gui.utils.history._local_db_exists", return_value=True), \
             mock.patch("gui.utils.history.get_local_db", return_value=local), \
             mock.patch("gui.utils.history.get_db", return_value=global_db):
            sessions = history_queries.load_executed_sessions("C:/Library", limit=10)

        self.assertEqual([session["session_id"] for session in sessions], ["local-session", "global-session"])
        self.assertEqual(len(calls), 2)
        self.assertTrue(local.closed)
        self.assertTrue(global_db.closed)

    def test_history_queries_dedupe_local_and_global_sessions_by_newest_timestamp(self):
        class _FakeDB:
            def __init__(self, rows):
                self.rows = rows

            def get_recent_sessions(self, limit=10, only_executed=False, target_root=None):
                return list(self.rows)

            def close(self):
                pass

        local = _FakeDB([
            {"session_id": "same-session", "timestamp": "2026-06-01T00:00:00", "file_count": 1, "source_path": "old"},
        ])
        global_db = _FakeDB([
            {"session_id": "same-session", "timestamp": "2026-06-03T00:00:00", "file_count": 1, "source_path": "new"},
        ])

        history_queries.invalidate_history_cache()
        with mock.patch("gui.utils.history._local_db_exists", return_value=True), \
             mock.patch("gui.utils.history.get_local_db", return_value=local), \
             mock.patch("gui.utils.history.get_db", return_value=global_db):
            sessions = history_queries.load_executed_sessions("C:/Library", limit=10)

        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["source_path"], "new")

    def test_history_queries_prefer_global_state_over_local_duplicate(self):
        class _FakeDB:
            def __init__(self, rows):
                self.rows = rows

            def get_recent_sessions(self, limit=10, only_executed=False, target_root=None):
                return list(self.rows)

            def close(self):
                pass

        local = _FakeDB([
            {
                "session_id": "same-session",
                "timestamp": "2026-06-03T00:00:00",
                "file_count": 1,
                "history_state": "undoable",
            },
        ])
        global_db = _FakeDB([
            {
                "session_id": "same-session",
                "timestamp": "2026-06-01T00:00:00",
                "file_count": 1,
                "history_state": "undone",
            },
        ])

        history_queries.invalidate_history_cache()
        with mock.patch("gui.utils.history._local_db_exists", return_value=True), \
             mock.patch("gui.utils.history.get_local_db", return_value=local), \
             mock.patch("gui.utils.history.get_db", return_value=global_db):
            sessions = history_queries.load_executed_sessions("C:/Library", limit=10)

        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["history_state"], "undone")

    def test_recent_sessions_include_undone_migrations_with_derived_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = UnshuffleDB(root / "unshuffle.db")
            try:
                source = root / "source.wav"
                target = root / "target"
                target.mkdir()
                db.register_session("done", source, target, "copy")
                db.add_records_bulk(
                    "done",
                    [{
                        "source_path": str(source),
                        "target_path": str(target / "built.wav"),
                        "category": "Kicks",
                        "subcategory": "",
                        "pack": "Pack",
                        "hash": "hash",
                        "confidence": 1.0,
                        "status": "copied",
                        "tags": "",
                        "step_status": "COMMITTED",
                    }],
                )

                before = db.get_recent_sessions(limit=10, only_executed=True, target_root=target)
                db.mark_session_undone("done")
                after = db.get_recent_sessions(limit=10, only_executed=True, target_root=target)

                self.assertEqual(before[0]["history_state"], "undoable")
                self.assertEqual(after[0]["history_state"], "undone")
                self.assertEqual(after[0]["file_count"], 1)
                self.assertEqual(after[0]["undoable_count"], 0)
                self.assertEqual(after[0]["undone_count"], 1)
            finally:
                db.close()

    def test_confirm_undo_blocks_target_mismatch(self):
        from gui.main.actions import history as history_actions

        app = mock.Mock()
        app.settings.value.return_value = "C:/Selected"
        sess = {
            "session_id": "s1",
            "mode": "copy",
            "source_path": "C:/Source",
            "target_root": "D:/Other",
            "file_count": 1,
        }

        with mock.patch.object(history_actions.QMessageBox, "warning") as warning:
            history_actions.confirm_undo(app, sess)

        warning.assert_called_once()
        app.worker_manager.start_undo.assert_not_called()

    def test_confirm_undo_blocks_missing_session_id(self):
        from gui.main.actions import history as history_actions

        app = mock.Mock()
        app.settings.value.return_value = "C:/Selected"
        sess = {
            "mode": "copy",
            "source_path": "C:/Source",
            "target_root": "C:/Selected",
            "file_count": 1,
        }

        with mock.patch.object(history_actions.QMessageBox, "warning") as warning:
            history_actions.confirm_undo(app, sess)

        warning.assert_called_once()
        app.worker_manager.start_undo.assert_not_called()

    def test_confirm_undo_uses_readable_html_paragraphs(self):
        from gui.main.actions import history as history_actions

        app = mock.Mock()
        app.settings.value.return_value = "C:/Library"
        sess = {
            "session_id": "s1",
            "mode": "move",
            "source_path": "C:/Source & Originals",
            "target_root": "C:/Library",
            "file_count": 12,
            "timestamp": "2026-06-04 23:10:02",
        }

        with (
            mock.patch.object(history_actions.QMessageBox, "warning", return_value=history_actions.QMessageBox.No) as warning,
            mock.patch("gui.main.actions.history.invalidate_history_cache") as invalidate_cache,
        ):
            history_actions.confirm_undo(app, sess)

        message = warning.call_args.args[2]
        self.assertIn("<p>Are you sure you want to REVERT this migration?</p>", message)
        self.assertIn("<p><b>Session:</b> s1<br>", message)
        self.assertIn("C:/Source &amp; Originals", message)
        self.assertIn("<p>All moved/copied files will be returned to their original locations.</p>", message)
        invalidate_cache.assert_not_called()
        app.worker_manager.start_undo.assert_not_called()

    def test_save_json_meta_round_trips(self):
        root = Path(__file__).resolve().parent.parent
        filename = f"test_meta_{uuid.uuid4().hex}.json"
        try:
            payload = {"session": "abc123", "count": 2}
            with mock.patch("unshuffle.persistence.get_system_dir", return_value=root):
                save_json_meta(root, filename, payload, is_dry_run=True)
                self.assertEqual(load_json_meta(root, filename, is_dry_run=True), payload)
        finally:
            (root / filename).unlink(missing_ok=True)
            (root / f"{filename}.tmp").unlink(missing_ok=True)

    def test_session_sources_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = UnshuffleDB(Path(tmp) / "session_sources.db")
            try:
                db.register_session("s1", Path("VendorA"), Path("Library"), "copy")
                db.set_session_sources("s1", [Path("VendorA"), Path("VendorB")])

                self.assertEqual(db.get_session_sources("s1"), ["VendorA", "VendorB"])
                recent = db.get_recent_sessions(1)[0]
                self.assertEqual(recent["source_count"], 2)
            finally:
                db.close()

    def test_sub_taxonomy_sync_round_trips_nested_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = UnshuffleDB(Path(tmp) / "taxonomy_sync.db")
            try:
                config = {
                    "NOISE_WORDS": [],
                    "LOOP_INDICATORS": [],
                    "ONESHOT_INDICATORS": [],
                    "ONESHOT_HINT_TOKENS": ["kick", "snare"],
                    "PERCUSSIVE_CATEGORIES": ["Kicks", "Snares"],
                    "WEAK_LOOP_INDICATORS": [],
                    "CATEGORY_SUPPRESSION_RULES": {},
                    "SUB_TAXONOMY_MAP": {
                        "Kicks": {"kick": "no-sub"},
                        "Percussion": {"conga": "Percussive(Membranophones)"},
                    },
                }

                sync_full_config(db, config)
                self.assertEqual(db.get_config_list("oneshot_hint_token"), ["kick", "snare"])
                self.assertEqual(db.get_config_list("percussive_category"), ["Kicks", "Snares"])
                self.assertEqual(
                    db.get_sub_taxonomy(),
                    {
                        "Kicks": {"kick": "no-sub"},
                        "Percussion": {"conga": "Percussive(Membranophones)"},
                    },
                )

                sync_full_config(
                    db,
                    {
                        **config,
                        "SUB_TAXONOMY_MAP": {"Bass": {"808": "no-sub"}},
                    },
                )
                self.assertEqual(db.get_sub_taxonomy(), {"Bass": {"808": "no-sub"}})
            finally:
                db.close()

    def test_v1_sub_taxonomy_schema_is_category_aware(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = UnshuffleDB(Path(tmp) / "taxonomy_schema.db")
            try:
                cols = [row["name"] for row in db.conn.execute("PRAGMA table_info(sub_taxonomy)").fetchall()]
                self.assertEqual(cols, ["category", "token", "sub_category"])
            finally:
                db.close()

    def test_db_uses_thread_local_connections(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = UnshuffleDB(Path(tmp) / "thread_local.db")
            try:
                main_conn = db.conn
                other = []

                def worker():
                    other.append(db.conn)

                thread = threading.Thread(target=worker)
                thread.start()
                thread.join()

                self.assertEqual(len(other), 1)
                self.assertIsNot(main_conn, other[0])
            finally:
                db.close()

    def test_v1_schema_contains_current_cache_and_safety_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = UnshuffleDB(Path(tmp) / "schema_columns.db")
            try:
                file_cache_cols = {
                    row["name"] for row in db.conn.execute("PRAGMA table_info(file_cache)").fetchall()
                }
                records_cols = {
                    row["name"] for row in db.conn.execute("PRAGMA table_info(records)").fetchall()
                }
                staging_cols = {
                    row["name"] for row in db.conn.execute("PRAGMA table_info(staging_records)").fetchall()
                }

                self.assertIn("feature_space_version", file_cache_cols)
                self.assertIn("extractor_version", file_cache_cols)
                self.assertIn("feature_schema_json", file_cache_cols)
                self.assertIn("analysis_status", file_cache_cols)
                self.assertIn("analysis_tags_json", file_cache_cols)
                self.assertIn("updated_at", file_cache_cols)
                self.assertIn("trash_path", records_cols)
                self.assertIn("preserved_root", records_cols)
                self.assertIn("preserved_root", staging_cols)
                self.assertIn("pack_candidates", staging_cols)
                self.assertIn("evidence_json", staging_cols)
            finally:
                db.close()

    def test_legacy_file_cache_schema_gets_current_metadata_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "legacy_cache.db"
            import sqlite3

            conn = sqlite3.connect(db_path)
            try:
                migrations_up(conn)
                file_cache_cols = {
                    row[1] for row in conn.execute("PRAGMA table_info(file_cache)").fetchall()
                }

                self.assertIn("feature_vector", file_cache_cols)
                self.assertIn("feature_space_version", file_cache_cols)
                self.assertIn("extractor_version", file_cache_cols)
                self.assertIn("feature_schema_json", file_cache_cols)
                self.assertIn("analysis_status", file_cache_cols)
                self.assertIn("analysis_tags_json", file_cache_cols)
                self.assertIn("updated_at", file_cache_cols)
            finally:
                conn.close()

    def test_alias_provenance_round_trips_for_taxonomy_views(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = UnshuffleDB(Path(tmp) / "alias_provenance.db")
            try:
                db.seed_aliases_bulk(
                    [
                        ("kick", "Kicks", 1.0, "system"),
                        ("thump", "Kicks", 0.8, "user"),
                    ]
                )
                self.assertEqual(
                    db.get_aliases_with_source(),
                    {
                        "kick": ("Kicks", 1.0, "system"),
                        "thump": ("Kicks", 0.8, "user"),
                    },
                )
                self.assertEqual(db.get_aliases_by_source("user"), {"thump": ("Kicks", 0.8)})
            finally:
                db.close()

    def test_concurrent_cache_updates_share_db_safely(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = UnshuffleDB(Path(tmp) / "concurrent_cache.db")
            errors = []
            start = threading.Barrier(5)

            def worker(idx: int):
                try:
                    start.wait()
                    db.update_cache(f"hash-{idx}", Path(f"file_{idx}.wav"), idx, float(idx))
                except Exception as exc:
                    errors.append(exc)

            threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
            try:
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join()
                self.assertFalse(errors, f"unexpected concurrent DB errors: {errors}")
                self.assertEqual(len(db.get_all_hashes()), 5)
            finally:
                db.close()

    def test_foreign_keys_are_enabled_and_integrity_check_detects_orphans(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = UnshuffleDB(Path(tmp) / "fk_check.db")
            try:
                self.assertEqual(int(db.conn.execute("PRAGMA foreign_keys").fetchone()[0]), 1)

                db.conn.execute("PRAGMA foreign_keys = OFF")
                db.conn.execute(
                    "INSERT INTO records (session_id, source_path, target_path, category, subcategory, pack, file_hash, confidence, status, tags) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    ("missing-session", "a.wav", "b.wav", "Kicks", None, "Pack", "h", 1.0, "copied", "[]"),
                )
                db.conn.commit()
                db.conn.execute("PRAGMA foreign_keys = ON")

                violations = db.foreign_key_violations()
                self.assertTrue(any(v[0] == "records" for v in violations))
            finally:
                db.close()

    def test_update_cache_bulk_accepts_current_v1_cache_rows(self):
        from unshuffle.core.features import CURRENT_EXTRACTOR_VERSION, CURRENT_FEATURE_SPACE_VERSION, CURRENT_VECTOR_SCHEMA

        with tempfile.TemporaryDirectory() as tmp:
            db = UnshuffleDB(Path(tmp) / "bulk_vectors.db")
            try:
                vector = b"\x00" * (SimilarityEngine.FEATURE_VECTOR_SIZE * 4)
                db.update_cache_bulk(
                    [
                        (
                            "hash-a",
                            "a.wav",
                            10,
                            1.0,
                            vector,
                            CURRENT_FEATURE_SPACE_VERSION,
                            CURRENT_EXTRACTOR_VERSION,
                            json.dumps(list(CURRENT_VECTOR_SCHEMA)),
                            "ok",
                        ),
                        ("hash-b", "b.wav", 11, 2.0),
                    ]
                )

                self.assertIsNotNone(db.get_acoustic_vector("hash-a"))
                self.assertIsNone(db.get_acoustic_vector("hash-b"))
            finally:
                db.close()

    def test_feature_vector_lookup_requires_current_metadata(self):
        from unshuffle.core.features import CURRENT_EXTRACTOR_VERSION, CURRENT_FEATURE_SPACE_VERSION, CURRENT_VECTOR_SCHEMA

        with tempfile.TemporaryDirectory() as tmp:
            db = UnshuffleDB(Path(tmp) / "feature_vector_metadata.db")
            try:
                vector = b"\x00" * (SimilarityEngine.FEATURE_VECTOR_SIZE * 4)
                db.update_cache_bulk(
                    [
                        (
                            "hash-current",
                            "current.wav",
                            10,
                            1.0,
                            vector,
                            CURRENT_FEATURE_SPACE_VERSION,
                            CURRENT_EXTRACTOR_VERSION,
                            json.dumps(list(CURRENT_VECTOR_SCHEMA)),
                            "ok",
                        ),
                        (
                            "hash-old-space",
                            "old-space.wav",
                            10,
                            1.0,
                            vector,
                            "old-space",
                            CURRENT_EXTRACTOR_VERSION,
                            json.dumps(list(CURRENT_VECTOR_SCHEMA)),
                            "ok",
                        ),
                        (
                            "hash-old-schema",
                            "old-schema.wav",
                            10,
                            1.0,
                            vector,
                            CURRENT_FEATURE_SPACE_VERSION,
                            CURRENT_EXTRACTOR_VERSION,
                            json.dumps(["old"]),
                            "ok",
                        ),
                    ]
                )

                self.assertIsNotNone(db.get_feature_vector("hash-current"))
                self.assertEqual(db.get_feature_vectors_bulk(["hash-current", "hash-old-space", "hash-old-schema"]), {"hash-current": vector})
                self.assertIsNone(db.get_feature_vector("hash-old-space"))
                self.assertIsNone(db.get_feature_vector("hash-old-schema"))
            finally:
                db.close()

    def test_update_cache_bulk_rejects_pre_release_vector_row_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = UnshuffleDB(Path(tmp) / "bulk_old_vector_shape.db")
            try:
                vector = b"\x00" * (SimilarityEngine.FEATURE_VECTOR_SIZE * 4)
                with self.assertRaises(ValueError):
                    db.update_cache_bulk([("hash-a", "a.wav", 10, 1.0, vector)])
            finally:
                db.close()

    def test_get_cached_hashes_batches_and_verifies_file_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = UnshuffleDB(Path(tmp) / "bulk_hash_lookup.db")
            try:
                matched = Path(tmp) / "matched.wav"
                stale = Path(tmp) / "stale.wav"
                missing = Path(tmp) / "missing.wav"
                db.update_cache_bulk(
                    [
                        ("hash-matched", matched, 10, 1.0),
                        ("hash-stale", stale, 20, 2.0),
                    ]
                )

                cached = db.get_cached_hashes(
                    [
                        (matched, 10, 1.0),
                        (stale, 21, 2.0),
                        (missing, 1, 1.0),
                    ]
                )

                self.assertEqual(cached, {matched.as_posix(): "hash-matched"})
            finally:
                db.close()

    def test_db_query_plans_use_v1_composite_indexes_for_hot_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = UnshuffleDB(Path(tmp) / "query_plans.db")
            try:
                plans = {
                    "cached_hash": db.conn.execute(
                        """
                        EXPLAIN QUERY PLAN
                        SELECT hash FROM file_cache
                        WHERE last_path = ? AND size = ? AND mtime = ?
                        """,
                        ("a.wav", 1, 1.0),
                    ).fetchall(),
                    "library_hash": db.conn.execute(
                        """
                        EXPLAIN QUERY PLAN
                        SELECT 1 FROM records
                        WHERE file_hash = ? AND status IN ('moved', 'copied')
                        LIMIT 1
                        """,
                        ("hash-a",),
                    ).fetchall(),
                    "committed_hashes": db.conn.execute(
                        """
                        EXPLAIN QUERY PLAN
                        SELECT DISTINCT file_hash FROM records
                        WHERE status IN ('moved', 'copied') AND file_hash IS NOT NULL
                        """
                    ).fetchall(),
                    "staging_order": db.conn.execute(
                        """
                        EXPLAIN QUERY PLAN
                        SELECT * FROM staging_records
                        WHERE session_id = ?
                        ORDER BY row_id ASC, id ASC
                        """,
                        ("session-1",),
                    ).fetchall(),
                    "staging_row": db.conn.execute(
                        """
                        EXPLAIN QUERY PLAN
                        SELECT feature_vector, duration FROM staging_records
                        WHERE session_id = ? AND row_id = ?
                        """,
                        ("session-1", 1),
                    ).fetchall(),
                }
                plan_text = {
                    name: " | ".join(str(row["detail"]) for row in rows)
                    for name, rows in plans.items()
                }

                self.assertIn("idx_cache_path_size_mtime", plan_text["cached_hash"])
                self.assertIn("idx_records_status_file_hash", plan_text["library_hash"])
                self.assertIn("idx_records_status_file_hash", plan_text["committed_hashes"])
                self.assertIn("idx_staging_records_session_row", plan_text["staging_order"])
                self.assertIn("idx_staging_records_session_row", plan_text["staging_row"])
                self.assertNotIn("USE TEMP B-TREE FOR ORDER BY", plan_text["staging_order"])
            finally:
                db.close()

    def test_run_plan_batches_acoustic_cache_updates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "Source"
            target = root / "Target"
            source.mkdir()
            target.mkdir()
            (source / "kick.wav").write_bytes(b"audio-a")
            (source / "snare.wav").write_bytes(b"audio-b")

            db = UnshuffleDB(root / "plan.db")
            update_cache = mock.Mock(wraps=db.update_cache)
            update_cache_bulk = mock.Mock(wraps=db.update_cache_bulk)
            db.update_cache = update_cache
            db.update_cache_bulk = update_cache_bulk

            vector = [0.0] * SimilarityEngine.FEATURE_VECTOR_SIZE

            try:
                with mock.patch("unshuffle.logic.planning.service.get_audio_duration", return_value=0.5), \
                     mock.patch("unshuffle.logic.planning.service.SimilarityEngine.extract_feature_payload", return_value=FeaturePayload(vector)):
                    run_plan(source, target, db=db, acoustic_index=True)

                self.assertEqual(update_cache.call_count, 0)
                self.assertEqual(update_cache_bulk.call_count, 1)
                self.assertEqual(len(update_cache_bulk.call_args.args[0]), 2)
            finally:
                db.close()

    def test_run_plan_reuses_acoustic_cache_with_bulk_lookup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "Source"
            target = root / "Target"
            source.mkdir()
            target.mkdir()
            (source / "kick.wav").write_bytes(b"audio-a")
            (source / "snare.wav").write_bytes(b"audio-b")

            db = UnshuffleDB(root / "plan.db")
            vector = [0.0] * SimilarityEngine.FEATURE_VECTOR_SIZE
            vector[SimilarityEngine.IDX_ACTIVE_DURATION] = 0.5

            try:
                with mock.patch("unshuffle.logic.planning.service.SimilarityEngine.extract_feature_payload", return_value=FeaturePayload(vector)):
                    run_plan(source, target, db=db, acoustic_index=True)

                bulk_lookup = mock.Mock(wraps=db.get_feature_vectors_bulk)
                single_lookup = mock.Mock(wraps=db.get_feature_vector)
                db.get_feature_vectors_bulk = bulk_lookup
                db.get_feature_vector = single_lookup

                with mock.patch("unshuffle.logic.planning.service.get_audio_duration") as get_duration, \
                     mock.patch("unshuffle.logic.planning.service.SimilarityEngine.extract_feature_payload") as extract:
                    records = run_plan(source, target, db=db, acoustic_index=True)

                self.assertEqual(bulk_lookup.call_count, 1)
                single_lookup.assert_not_called()
                extract.assert_not_called()
                get_duration.assert_not_called()
                self.assertEqual(len(records), 2)
                self.assertTrue(all(record.feature_vector for record in records))
                self.assertEqual({record.duration for record in records}, {0.5})
            finally:
                db.close()

    def test_extractor_worker_count_caps_default_and_honors_override(self):
        with mock.patch("os.cpu_count", return_value=64), \
             mock.patch("unshuffle.core.concurrency.sys.platform", "linux"), \
             mock.patch.dict("os.environ", {}, clear=True):
            self.assertEqual(_extractor_worker_count(20), 8)
            self.assertEqual(_extractor_worker_count(3), 3)

        with mock.patch.dict("os.environ", {"UNSHUFFLE_EXTRACTOR_WORKERS": "2"}, clear=True):
            self.assertEqual(_extractor_worker_count(20), 2)

        with mock.patch.dict("os.environ", {"UNSHUFFLE_MAX_SCAN_WORKERS": "3"}, clear=True), \
             mock.patch("unshuffle.core.concurrency.sys.platform", "linux"), \
             mock.patch("os.cpu_count", return_value=64):
            self.assertEqual(_extractor_worker_count(20), 3)

        with mock.patch.dict("os.environ", {
            "UNSHUFFLE_EXTRACTOR_WORKERS": "2",
            "UNSHUFFLE_MAX_SCAN_WORKERS": "3",
        }, clear=True):
            self.assertEqual(_extractor_worker_count(20), 2)

        with mock.patch("unshuffle.core.concurrency.sys.platform", "darwin"), \
             mock.patch.dict("os.environ", {}, clear=True):
            self.assertEqual(_extractor_worker_count(20), 4)

        with mock.patch.dict("os.environ", {"UNSHUFFLE_EXTRACTOR_WORKERS": "invalid"}, clear=True), \
             mock.patch("unshuffle.core.concurrency.sys.platform", "linux"), \
             mock.patch("os.cpu_count", return_value=64):
            self.assertEqual(_extractor_worker_count(20), 8)

    def test_run_plan_extracts_duplicate_hash_once_per_scan(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "Source"
            target = root / "Target"
            source.mkdir()
            target.mkdir()
            payload = b"same-audio"
            (source / "kick_a.wav").write_bytes(payload)
            (source / "kick_b.wav").write_bytes(payload)

            vector = [0.0] * SimilarityEngine.FEATURE_VECTOR_SIZE
            vector[SimilarityEngine.IDX_ACTIVE_DURATION] = 0.5

            with mock.patch("unshuffle.logic.planning.service.SimilarityEngine.extract_feature_payload", return_value=FeaturePayload(vector)) as extract:
                records = run_plan(source, target, acoustic_index=True)

            self.assertEqual(extract.call_count, 1)
            self.assertEqual(len(records), 2)
            self.assertTrue(all(record.feature_vector for record in records))
            self.assertEqual({record.duration for record in records}, {0.5})

    def test_run_plan_reuses_acoustic_vector_duration_without_metadata_duration_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "Source"
            target = root / "Target"
            source.mkdir()
            target.mkdir()
            (source / "loop_120bpm.wav").write_bytes(b"audio-a")

            vector = [0.0] * SimilarityEngine.FEATURE_VECTOR_SIZE
            vector[SimilarityEngine.IDX_ACTIVE_DURATION] = 3.25

            with mock.patch("unshuffle.logic.planning.service.get_audio_duration") as get_duration, \
                 mock.patch("unshuffle.logic.planning.service.SimilarityEngine.extract_feature_payload", return_value=FeaturePayload(vector)):
                records = run_plan(source, target, acoustic_index=True)

            get_duration.assert_not_called()
            self.assertEqual(len(records), 1)
            self.assertAlmostEqual(records[0].duration, 3.25)
            self.assertIsNotNone(records[0].acoustic_vector)

    def test_run_plan_tags_failed_acoustic_extractions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "Source"
            target = root / "Target"
            source.mkdir()
            target.mkdir()
            (source / "silent_kick.wav").write_bytes(b"audio-a")

            with mock.patch("unshuffle.logic.planning.service.get_audio_duration", return_value=0.0), \
                 mock.patch("unshuffle.logic.planning.service.SimilarityEngine.extract_feature_payload", return_value=None), \
                 mock.patch("unshuffle.logic.planning.service.SimilarityEngine.extraction_failure_tag", return_value="Silent"):
                records = run_plan(source, target, acoustic_index=True)

            self.assertEqual(len(records), 1)
            self.assertIn("Silent", records[0].tags)

    def test_run_plan_skips_classifier_and_acoustic_work_for_non_audio_assets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "Source"
            target = root / "Target"
            source.mkdir()
            target.mkdir()
            (source / "notes.txt").write_text("not audio", encoding="utf-8")

            with mock.patch("unshuffle.logic.planning.service.classify_node") as classify, \
                 mock.patch("unshuffle.logic.planning.service.get_audio_duration") as get_duration, \
                 mock.patch("unshuffle.logic.planning.service.SimilarityEngine.extract_feature_payload") as extract_features:
                records = run_plan(source, target, acoustic_index=True)

            classify.assert_not_called()
            get_duration.assert_not_called()
            extract_features.assert_not_called()
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].category, "Non-Audio Assets")
            self.assertEqual(records[0].audio_type, "Non-Audio Assets")
            self.assertIsNone(records[0].acoustic_vector)
            self.assertEqual(records[0].duration, 0.0)

    def test_run_plan_rescans_built_non_audio_assets_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "BuiltLibrary"
            target = root / "Target"
            asset = source / "Non-Audio Assets" / "Pack A" / "LICENSE.pdf"
            asset.parent.mkdir(parents=True)
            target.mkdir()
            asset.write_text("license", encoding="utf-8")

            records = run_plan(source, target, acoustic_index=True)

            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].source_path, asset)
            self.assertEqual(records[0].audio_type, "Non-Audio Assets")
            self.assertEqual(records[0].category, "Non-Audio Assets")
            self.assertEqual(records[0].pack, "Pack A")

    def test_run_plan_rescans_non_audio_assets_when_source_is_target_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "BuiltLibrary"
            asset = source / "Non-Audio Assets" / "Pack A" / "LICENSE.pdf"
            asset.parent.mkdir(parents=True)
            asset.write_text("license", encoding="utf-8")

            records = run_plan(source, source, acoustic_index=True)

            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].source_path, asset)
            self.assertEqual(records[0].audio_type, "Non-Audio Assets")
            self.assertEqual(records[0].category, "Non-Audio Assets")
            self.assertEqual(records[0].pack, "Pack A")

    def test_invalid_taxonomy_file_emits_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            taxonomy_dir = root / "data" / "taxonomy"
            taxonomy_dir.mkdir(parents=True)
            (root / "config.json").write_text("{}", encoding="utf-8")
            (taxonomy_dir / "broken.json").write_text(
                '{"category":"Broken","taxonomy":{"bad_bucket": 123}}',
                encoding="utf-8",
            )

            with mock.patch("unshuffle.core.config.ROOT_DIR", root), \
                 mock.patch("unshuffle.core.config.logger.warning") as warning_mock:
                cfg = load_config()

            self.assertIn("Broken", cfg["SUB_TAXONOMY_MAP"])
            warning_texts = [" ".join(str(arg) for arg in call.args) for call in warning_mock.call_args_list]
            self.assertTrue(any("broken.json" in text for text in warning_texts))

    def test_staging_records_round_trip_pack_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = UnshuffleDB(Path(tmp) / "staging_pack_candidates.db")
            session_id = "pack-candidates"
            try:
                db.register_session(session_id, Path("Source"), Path("Target"), "copy")
                db.add_staging_records_bulk(
                    session_id,
                    [
                        (
                            1,
                            "Source/a.wav",
                            "a.wav",
                            "Pack A",
                            "Kicks",
                            "",
                            "Oneshots",
                            "",
                            "0.95",
                            0.5,
                            "hash-a",
                            '[["Pack A", 1.0], ["Source", 0.7]]',
                            None,
                            None,
                            0,
                        )
                    ],
                )

                rows = db.get_staging_records(session_id)
                self.assertEqual(rows[0]["pack_candidates"], '[["Pack A", 1.0], ["Source", 0.7]]')
            finally:
                db.close()

    def test_staging_records_round_trip_evidence_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = UnshuffleDB(Path(tmp) / "staging_evidence.db")
            session_id = "evidence"
            try:
                db.register_session(session_id, Path("Source"), Path("Target"), "copy")
                evidence = {"stage": "final", "raw": {"Kicks": 1.2}}
                db.add_staging_records_bulk(
                    session_id,
                    [
                        (
                            1,
                            "Source/a.wav",
                            "a.wav",
                            "Pack A",
                            "Kicks",
                            "",
                            "Oneshots",
                            "",
                            "0.95",
                            0.5,
                            "hash-a",
                            "[]",
                            json.dumps(evidence),
                            None,
                            None,
                            None,
                            None,
                            None,
                            None,
                            0,
                        )
                    ],
                )

                rows = db.get_staging_records(session_id)
                self.assertEqual(json.loads(rows[0]["evidence_json"]), evidence)
            finally:
                db.close()

    def test_staging_records_reject_pre_v1_tuple_shapes(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = UnshuffleDB(Path(tmp) / "staging_old_shape.db")
            session_id = "old-shape"
            try:
                db.register_session(session_id, Path("Source"), Path("Target"), "copy")
                with self.assertRaises(ValueError):
                    db.add_staging_records_bulk(
                        session_id,
                        [(1, "Source/a.wav", "a.wav", "Pack", "Kicks", "", "Oneshots", "", "0.9", 0.1, None)],
                    )
            finally:
                db.close()

    def test_history_queries_close_ephemeral_db_handles(self):
        entered = []
        exited = []

        class _FakeDB:
            def __enter__(self):
                entered.append(True)
                return self

            def __exit__(self, exc_type, exc, tb):
                exited.append(True)

            def get_recent_sessions(self, limit=10, only_executed=False):
                return [{"session_id": "s1", "file_count": 1}]

        history_queries.invalidate_history_cache()
        with mock.patch("gui.utils.history.get_db", return_value=_FakeDB()):
            sessions = history_queries.load_executed_sessions("C:/Library", limit=1)

        self.assertEqual(len(sessions), 1)
        self.assertEqual(len(entered), 1)
        self.assertEqual(len(exited), 1)

    def test_data_manager_closes_local_and_global_db_handles(self):
        class _FakeDB:
            def __init__(self, sessions):
                self._sessions = sessions
                self.closed = False

            def get_recent_sessions(self, limit=10):
                return list(self._sessions)

            def get_session_sources(self, sid):
                return []

            def get_session_records(self, sid):
                return []

            def register_session(self, *args, **kwargs):
                return None

            def set_session_sources(self, *args, **kwargs):
                return None

            def add_records_bulk(self, *args, **kwargs):
                return None

            def close(self):
                self.closed = True

        local_db = _FakeDB([])
        global_db = _FakeDB([])
        manager = DataManager()

        with mock.patch("unshuffle.persistence.get_local_db", return_value=local_db), \
             mock.patch("unshuffle.persistence.get_db", return_value=global_db):
            self.assertFalse(manager.check_and_sync_local_db("C:/Library"))

        self.assertTrue(local_db.closed)
        self.assertTrue(global_db.closed)

    def test_runtime_config_snapshot_is_isolated_copy(self):
        snapshot = get_runtime_config_snapshot()
        existing_alias = next(iter(ALIAS_TABLE.items()))
        snapshot["alias_table"][existing_alias[0]] = ("Changed", 1.0)

        self.assertEqual(ALIAS_TABLE[existing_alias[0]], existing_alias[1])

    def test_persistence_bridge_exposes_taxonomy_state_helpers(self):
        class _Workflow:
            def __init__(self, db):
                self.db = db
                self.session_id = "session-1"

        with tempfile.TemporaryDirectory() as tmp:
            db = UnshuffleDB(Path(tmp) / "bridge_taxonomy.db")
            try:
                db.seed_aliases_bulk([("thump", "Kicks", 0.8, "user")])
                db.seed_config_list("loop_indicator", ["loop"], clear=True)
                db.seed_suppression_rules({"Claps": ["Snare Rolls"]})
                db.seed_sub_taxonomy({"Kicks": {"thump": "Punchy"}})
                db.update_token_adjustments_bulk([("kick", "Kicks", 0.2)])

                bridge = PersistenceBridge(_Workflow(db))
                self.assertEqual(bridge.get_aliases_by_source("user"), {"thump": ("Kicks", 0.8)})
                self.assertEqual(bridge.get_config_list("loop_indicator"), ["loop"])
                self.assertEqual(bridge.get_suppression_rules(), {"Claps": ["Snare Rolls"]})
                self.assertEqual(bridge.get_sub_taxonomy(), {"Kicks": {"thump": "Punchy"}})
                self.assertEqual(bridge.get_token_adjustments(), {"kick": {"Kicks": 0.1}})
            finally:
                db.close()

    def test_persistence_bridge_bulk_user_aliases_and_correction_removal(self):
        class _Workflow:
            def __init__(self, db):
                self.db = db
                self.session_id = "session-1"

        with tempfile.TemporaryDirectory() as tmp:
            db = UnshuffleDB(Path(tmp) / "bridge_taxonomy_mutations.db")
            try:
                bridge = PersistenceBridge(_Workflow(db))
                with mock.patch("unshuffle.bridge.persistence_bridge.refresh_alias_structures"), \
                     mock.patch("unshuffle.bridge.persistence_bridge.reset_scoring_engine"):
                    added = bridge.add_aliases_bulk(["Thump", "thump", "Snap"], "Kicks", source="user")

                self.assertEqual(added, 2)
                self.assertEqual(
                    bridge.get_user_additions(),
                    [("snap", "Kicks", "user"), ("thump", "Kicks", "user")],
                )

                db.update_token_adjustments_bulk([("kick", "Kicks", 0.2), ("kick", "Snares", -0.1)])
                self.assertEqual(
                    bridge.list_token_adjustments(),
                    [("kick", "Kicks", 0.1), ("kick", "Snares", -0.1)],
                )
                removed = bridge.remove_token_adjustments([("kick", "Snares")])
                self.assertEqual(removed, 1)
                self.assertEqual(bridge.list_token_adjustments(), [("kick", "Kicks", 0.1)])
            finally:
                db.close()

    def test_token_adjustments_prune_unweighted_tokens(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = UnshuffleDB(Path(tmp) / "learned_cleanup.db")
            try:
                with db.write_transaction():
                    db.conn.execute(
                        "INSERT INTO token_adjustments (token, category, weight_offset) VALUES (?, ?, ?)",
                        ("wav", "Percussion", 0.1),
                    )
                    db.conn.execute(
                        "INSERT INTO token_adjustments (token, category, weight_offset) VALUES (?, ?, ?)",
                        ("kick", "Kicks", 0.1),
                    )

                self.assertEqual(db.get_token_adjustments(), {"kick": {"Kicks": 0.1}})
                stale = db.conn.execute("SELECT COUNT(*) FROM token_adjustments WHERE token = 'wav'").fetchone()[0]
                self.assertEqual(stale, 0)
            finally:
                db.close()

    def test_token_adjustments_reject_unweighted_tokens_on_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = UnshuffleDB(Path(tmp) / "learned_reject_unweighted.db")
            try:
                db.update_token_adjustments_bulk([("wav", "Percussion", 0.1)])

                self.assertEqual(db.get_token_adjustments(), {})
            finally:
                db.close()

    def test_learned_correction_events_prevent_duplicate_sample_learning(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = UnshuffleDB(Path(tmp) / "learned_events.db")
            try:
                event = ("path:d:/samples/pack/kick.wav", "kick", "Kicks", "Snares", -0.01, 0.01)

                first = db.update_token_adjustments_from_events([event])
                second = db.update_token_adjustments_from_events([event])

                self.assertEqual(first, 2)
                self.assertEqual(second, 0)
                self.assertEqual(db.get_token_adjustments(), {"kick": {"Kicks": -0.01, "Snares": 0.01}})
                count = db.conn.execute("SELECT COUNT(*) FROM learned_correction_events").fetchone()[0]
                self.assertEqual(count, 1)
            finally:
                db.close()

    def test_learned_correction_events_schema_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = UnshuffleDB(Path(tmp) / "learned_events_schema.db")
            try:
                tables = {
                    row["name"]
                    for row in db.conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                }
                self.assertIn("learned_correction_events", tables)
            finally:
                db.close()

    def test_persistence_bridge_warns_on_missing_session_for_writes(self):
        bridge = PersistenceBridge()

        with mock.patch("unshuffle.bridge.persistence_bridge.logger.warning") as warning_mock:
            bridge.add_exclusion("C:/Library/Ignore")
            bridge.reset_adjustments()

        self.assertEqual(warning_mock.call_count, 2)

    def test_search_bridge_uses_standardized_workflow_db_access(self):
        workflow = mock.Mock()
        workflow.db = mock.Mock()
        workflow.session_id = "session-1"
        workflow.db.search_staging.return_value = {1, 2}

        bridge = SearchBridge(workflow)
        result = bridge.search_staging('category:"Kicks"')

        self.assertEqual(result, {1, 2})
        workflow.db.search_staging.assert_called_once_with("session-1", 'category:"Kicks"')

    def test_create_workflow_bridge_accepts_explicit_engine_factory(self):
        created = {}

        class _Engine:
            def __init__(self, target_dir, **kwargs):
                created["target_dir"] = target_dir
                created["kwargs"] = kwargs
                self.db = mock.Mock()
                self.local_db = mock.Mock()
                self.target_dir = target_dir
                self.session_id = kwargs.get("session_id")
                self.session_source_root = None
                self.session_source_roots = []
                self.interrupted = False
                self.progress_callback = kwargs.get("progress_callback")

        bridge = create_workflow_bridge(Path("Library"), session_id="s1", engine_factory=_Engine)

        self.assertIsInstance(bridge, WorkflowBridge)
        self.assertEqual(created["kwargs"]["session_id"], "s1")

    def test_workflow_bridge_update_state_applies_multiple_engine_fields(self):
        engine = mock.Mock()
        bridge = WorkflowBridge(engine)

        bridge.update_state(target_dir=Path("Library"), interrupted=True)

        self.assertEqual(engine.target_dir, Path("Library"))
        self.assertTrue(engine.interrupted)

    def test_workflow_bridge_does_not_proxy_unknown_engine_attributes(self):
        engine = mock.Mock()
        bridge = WorkflowBridge(engine)

        with self.assertRaises(AttributeError):
            getattr(bridge, "unknown_engine_only_method")

    def test_workflow_bridge_exposes_explicit_cache_operation(self):
        engine = mock.Mock()
        bridge = WorkflowBridge(engine)

        bridge.load_cache(rebuild=True)

        engine.load_cache.assert_called_once_with(rebuild=True)

    def test_prune_ephemeral_state_keeps_current_and_preserves_durable_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "target"
            other_target = Path(tmp) / "other"
            db = UnshuffleDB(Path(tmp) / "maintenance.db")
            try:
                for session_id in ("keep", "stale", "executed", "other"):
                    db.register_session(
                        session_id,
                        Path(tmp) / f"source-{session_id}",
                        other_target if session_id == "other" else target,
                        "pending",
                    )
                db.add_records_bulk(
                    "executed",
                    [{
                        "source_path": str(Path(tmp) / "source-executed" / "kick.wav"),
                        "target_path": str(target / "Kicks" / "kick.wav"),
                        "category": "Kicks",
                        "subcategory": "",
                        "pack": "Pack",
                        "hash": "hash-executed",
                        "status": "copied",
                        "step_status": "COMMITTED",
                    }],
                )
                db.update_token_adjustments_bulk([("kick", "Kicks", 0.2)])
                db.upsert_coherence_review_decisions(
                    "stale",
                    [{
                        "source_path": str(Path(tmp) / "source-stale" / "snare.wav"),
                        "file_hash": "hash-stale",
                        "decision_type": "accepted_current",
                    }],
                )

                def _insert_ephemeral_rows(session_id: str) -> None:
                    db.conn.execute(
                        """
                        INSERT INTO staging_records (
                            row_id, session_id, source_path, sample_name, pack, category,
                            subcategory, audio_type, tags, confidence, duration, hash, pack_candidates
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            1,
                            session_id,
                            f"{tmp}/{session_id}.wav",
                            f"{session_id}.wav",
                            "Pack",
                            "Kicks",
                            "",
                            "Oneshots",
                            "",
                            "100",
                            1.0,
                            f"hash-{session_id}",
                            "[]",
                        ),
                    )
                    db.conn.execute(
                        """
                        INSERT INTO coherence_results (session_id, record_id, coherence_status, is_outlier)
                        VALUES (?, ?, ?, ?)
                        """,
                        (session_id, "1", "outlier", 1),
                    )
                    db.conn.execute(
                        """
                        INSERT INTO refinement_candidates (session_id, candidate_id, record_id, state)
                        VALUES (?, ?, ?, ?)
                        """,
                        (session_id, f"candidate-{session_id}", "1", "pending"),
                    )
                    db.conn.execute(
                        """
                        INSERT INTO anchor_profiles (session_id, anchor_id, state)
                        VALUES (?, ?, ?)
                        """,
                        (session_id, f"candidate-anchor-{session_id}", "candidate"),
                    )
                for session_id in ("keep", "stale", "executed", "other"):
                    _insert_ephemeral_rows(session_id)
                db.conn.commit()
                db.conn.execute("PRAGMA foreign_keys = OFF")
                _insert_ephemeral_rows("orphan")
                db.conn.commit()
                db.conn.execute("PRAGMA foreign_keys = ON")
                db.conn.execute(
                    "INSERT INTO anchor_profiles (session_id, anchor_id, state) VALUES (?, ?, ?)",
                    ("stale", "verified-anchor", "verified"),
                )
                db.conn.commit()

                stats = db.prune_ephemeral_state({"keep"}, target_root=target)

                self.assertIn("stale", stats["pruned_sessions"])
                self.assertIn("executed", stats["pruned_sessions"])
                self.assertIn("orphan", stats["pruned_sessions"])
                self.assertEqual(len(db.get_staging_records("keep")), 1)
                self.assertEqual(len(db.get_staging_records("stale")), 0)
                self.assertEqual(len(db.get_staging_records("executed")), 0)
                self.assertEqual(len(db.get_staging_records("other")), 1)
                self.assertIsNone(db.get_session("stale"))
                self.assertIsNotNone(db.get_session("executed"))
                self.assertEqual(len(db.get_session_records("executed")), 1)
                self.assertEqual(db.get_token_adjustments(), {"kick": {"Kicks": 0.1}})
                self.assertEqual(len(db.list_coherence_review_decisions(file_hashes=["hash-stale"])), 1)
                verified = [
                    row for row in db.list_anchor_candidates("stale", state="verified")
                    if row["anchor_id"] == "verified-anchor"
                ]
                self.assertEqual(len(verified), 1)
                self.assertEqual(db.count_refinement_candidates("stale"), 0)
            finally:
                db.close()

    def test_prune_ephemeral_state_falls_back_to_newest_restorable_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "target"
            db = UnshuffleDB(Path(tmp) / "maintenance_fallback.db")
            try:
                db.register_session("old", Path(tmp) / "old-source", target, "pending")
                db.register_session("new", Path(tmp) / "new-source", target, "pending")
                db.conn.execute("UPDATE sessions SET timestamp = '2024-01-01 00:00:00' WHERE session_id = 'old'")
                db.conn.execute("UPDATE sessions SET timestamp = '2024-02-01 00:00:00' WHERE session_id = 'new'")
                for session_id in ("old", "new"):
                    db.conn.execute(
                        """
                        INSERT INTO staging_records (
                            row_id, session_id, source_path, sample_name, pack, category,
                            subcategory, audio_type, tags, confidence, duration, hash, pack_candidates
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (1, session_id, f"{tmp}/{session_id}.wav", f"{session_id}.wav", "Pack", "Kicks", "", "Oneshots", "", "100", 1.0, session_id, "[]"),
                    )
                db.conn.commit()

                stats = db.prune_ephemeral_state(set(), target_root=target)

                self.assertEqual(stats["kept_sessions"], ["new"])
                self.assertEqual(len(db.get_staging_records("old")), 0)
                self.assertEqual(len(db.get_staging_records("new")), 1)
            finally:
                db.close()

    def test_compact_if_worthwhile_skips_small_reclaimable_space(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = UnshuffleDB(Path(tmp) / "maintenance_compact.db")
            try:
                result = db.compact_if_worthwhile(min_reclaim_mb=512, min_reclaim_ratio=0.25)

                self.assertTrue(result["skipped"])
                self.assertEqual(result["reason"], "below_threshold")
                self.assertFalse(result["ran"])
            finally:
                db.close()
