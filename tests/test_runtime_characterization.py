from typing import Any
from typing import cast
import json
import logging
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

from unshuffle.runtime.engine import RuntimeUnshuffler as Unshuffler
from unshuffle.core.hashing import get_file_hash
from unshuffle.core.paths import SYSTEM_FOLDER_NAME
from unshuffle.runtime import acquire_lock, release_lock
from unshuffle.runtime.cache import CacheMixin


class EngineLockTests(unittest.TestCase):
    def _lock_dir(self, target: Path) -> Path:
        return target / SYSTEM_FOLDER_NAME / "lock"

    def test_cross_host_lock_is_not_taken_over_automatically(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            log = mock.Mock()
            first = acquire_lock(target, "session-a", log)
            try:
                lock_path = self._lock_dir(target) / "lock.json"
                data = json.loads(lock_path.read_text(encoding="utf-8"))
                data["pid"] = os.getpid() + 1000
                data["hostname"] = "remote-machine"
                lock_path.write_text(json.dumps(data), encoding="utf-8")

                with mock.patch("socket.gethostname", return_value="local-machine"):
                    with self.assertRaises(RuntimeError) as ctx:
                        acquire_lock(target, "session-b", log)

                self.assertIn("another machine", str(ctx.exception))
            finally:
                release_lock(first, log)

    def test_corrupt_lock_metadata_refuses_automatic_takeover(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            log = mock.Mock()
            lock_dir = self._lock_dir(target)
            lock_dir.mkdir(parents=True)
            lock_path = lock_dir / "lock.json"
            lock_path.write_text("{not-json", encoding="utf-8")

            with self.assertRaises(RuntimeError) as ctx:
                acquire_lock(target, "session-a", log)

            self.assertIn("metadata is missing or corrupt", str(ctx.exception))
            self.assertEqual(lock_path.read_text(encoding="utf-8"), "{not-json")

    def test_corrupt_lock_metadata_can_be_forced_after_manual_verification(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            log = mock.Mock()
            lock_dir = self._lock_dir(target)
            lock_dir.mkdir(parents=True)
            lock_path = lock_dir / "lock.json"
            lock_path.write_text("{not-json", encoding="utf-8")

            with mock.patch.dict(os.environ, {"UNSHUFFLE_FORCE_LOCK_TAKEOVER": "1"}):
                acquired = acquire_lock(target, "session-a", log)
            try:
                data = json.loads(lock_path.read_text(encoding="utf-8"))
                self.assertEqual(data["session_id"], "session-a")
            finally:
                release_lock(acquired, log)

    def test_exclusive_guard_blocks_simultaneous_takeover_negotiation(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            log = mock.Mock()
            guard_path = self._lock_dir(target) / "lock.exclusive"
            guard_path.parent.mkdir(parents=True)
            guard_path.write_text("", encoding="utf-8")

            with self.assertRaises(RuntimeError) as ctx:
                acquire_lock(target, "session-a", log)

            self.assertIn("negotiating", str(ctx.exception))

    def test_stale_exclusive_guard_is_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            log = mock.Mock()
            guard_path = self._lock_dir(target) / "lock.exclusive"
            guard_path.parent.mkdir(parents=True)
            guard_path.write_text("", encoding="utf-8")
            old_time = 1
            os.utime(guard_path, (old_time, old_time))

            acquired = acquire_lock(target, "session-a", log)
            try:
                self.assertTrue(acquired.exists())
                self.assertFalse(guard_path.exists())
            finally:
                release_lock(acquired, log)

    def test_release_lock_ignores_closed_logging_handler_after_unlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            log = mock.Mock()
            acquired = acquire_lock(target, "session-a", log)

            def closed_handler_log(*args, **kwargs):
                raise ValueError("I/O operation on closed file")

            release_lock(acquired, closed_handler_log)

            self.assertFalse(acquired.exists())

    def test_release_lock_can_release_without_logging(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            acquired = acquire_lock(target, "session-a", mock.Mock())

            release_lock(acquired)

            self.assertFalse(acquired.exists())

    def test_release_lock_does_not_unlink_lock_owned_by_other_process(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            log = mock.Mock()
            acquired = acquire_lock(target, "session-a", log)
            try:
                lock_data = json.loads(acquired.read_text(encoding="utf-8"))
                lock_data["pid"] = os.getpid() + 10000
                acquired.write_text(json.dumps(lock_data), encoding="utf-8")

                release_lock(acquired, log)

                self.assertTrue(acquired.exists())
            finally:
                acquired.unlink(missing_ok=True)

    def test_lock_is_created_under_sidecar_lock_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            acquired = acquire_lock(target, "session-a", mock.Mock())
            try:
                self.assertEqual(acquired, target / SYSTEM_FOLDER_NAME / "lock" / "lock.json")
                self.assertTrue(acquired.exists())
                self.assertFalse((target / ".unshuffle" / "lock.json").exists())
            finally:
                release_lock(acquired)

    def test_legacy_lock_folder_with_known_lock_files_is_removed_after_acquire(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            legacy = target / ".unshuffle"
            legacy.mkdir()
            (legacy / "lock.json").write_text("{}", encoding="utf-8")
            (legacy / "lock.exclusive").write_text("", encoding="utf-8")
            (legacy / "lock.json.123.tmp").write_text("", encoding="utf-8")

            acquired = acquire_lock(target, "session-a", mock.Mock())
            try:
                self.assertFalse(legacy.exists())
            finally:
                release_lock(acquired)

    def test_legacy_lock_folder_with_unknown_content_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            legacy = target / ".unshuffle"
            legacy.mkdir()
            (legacy / "notes.txt").write_text("keep", encoding="utf-8")
            (legacy / "metadata").mkdir()

            acquired = acquire_lock(target, "session-a", mock.Mock())
            try:
                self.assertTrue(legacy.exists())
                self.assertTrue((legacy / "notes.txt").exists())
                self.assertTrue((legacy / "metadata").exists())
            finally:
                release_lock(acquired)

    def test_legacy_lock_cleanup_failure_does_not_fail_acquire(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            legacy = target / ".unshuffle"
            legacy.mkdir()
            (legacy / "lock.json").write_text("{}", encoding="utf-8")

            with mock.patch("unshuffle.runtime.locking.Path.rmdir", side_effect=OSError("busy")):
                acquired = acquire_lock(target, "session-a", mock.Mock())
            try:
                self.assertTrue(acquired.exists())
                self.assertTrue(legacy.exists())
            finally:
                release_lock(acquired)


class EngineSessionTests(unittest.TestCase):
    def test_prepare_plan_persists_all_source_roots(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_a = root / "VendorA"
            source_b = root / "VendorB"
            target = root / "Library"
            source_a.mkdir()
            source_b.mkdir()
            (source_a / "kick.wav").touch()
            (source_b / "snare.wav").touch()

            from unshuffle.persistence import UnshuffleDB
            class DummyBootstrapper:
                def setup_logging_fn(self, *args, **kwargs):
                    pass
                def get_local_db_fn(self, root):
                    db_path = root / ".unshuffle" / "staging.db"
                    db_path.parent.mkdir(parents=True, exist_ok=True)
                    return UnshuffleDB(db_path)
                def run_plan_fn(self, *args, **kwargs):
                    return []
            
            engine = Unshuffler(target, bootstrapper=DummyBootstrapper())
            engine.db = engine.local_db
            try:
                engine.prepare_plan([source_a, source_b])
                self.assertEqual(
                    engine.db.get_session_sources(engine.session_id),
                    [str(source_a.resolve()), str(source_b.resolve())],
                )
            finally:
                engine.close()


class UndoSafetyTests(unittest.TestCase):
    def test_undo_uses_local_session_mode_when_global_history_is_missing(self):
        class FakeDB:
            def __init__(self, records=None, session=None):
                self._records = records or []
                self._session = session
                self.deleted = []
                self.removed_paths = []

            def get_session_records(self, session_id):
                return list(self._records)

            def get_session(self, session_id):
                return self._session

            def remove_from_cache_by_paths(self, path_list):
                self.removed_paths.extend(path_list)

            def delete_session(self, session_id):
                self.deleted.append(session_id)

            def get_all_hashes(self):
                return {}

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / f"undo_src_{uuid.uuid4().hex}.wav"
            tgt = root / f"undo_tgt_{uuid.uuid4().hex}.wav"
            tgt.write_bytes(b"data")
            engine = Unshuffler.__new__(Unshuffler)
            engine.target_dir = root
            engine.progress_callback = None
            engine.log = lambda *args, **kwargs: None
            engine.seen_hashes = {}
            engine.interrupted = False
            engine.db = cast(Any, FakeDB(records=[], session=None))
            engine.local_db =cast(Any, FakeDB(
                records=[{
                    "source_path": src,
                    "target_path": tgt,
                    "status": "copied",
                    "file_hash": get_file_hash(tgt),
                    "step_status": "COMMITTED",
                    "original_action": "move",
                }],
                session={"mode": "move"},
            ))
            result = engine.undo_session("s1")

            self.assertNotIn("error", result)
            self.assertTrue(src.exists())
            self.assertFalse(tgt.exists())
        logging.shutdown()


class CacheRebuildTests(unittest.TestCase):
    class _Harness(CacheMixin):
        def __init__(self, target_dir: Path):
            self.target_dir = target_dir
            self.interrupted = False
            self.progress_callback = None
            self.seen_hashes = {}
            self.seen_hash_metadata = {}
            self.logged = []
            self.db = mock.Mock()

        def log(self, message, level=logging.INFO):
            self.logged.append((level, message))

        def _initialize_cache_state(self):
            return None

    def test_rebuild_index_walks_library_once_and_preserves_stat_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sample = root / "kick.wav"
            sample.write_bytes(b"audio")
            harness = self._Harness(root)

            with mock.patch.object(harness, "_find_audio_files", wraps=harness._find_audio_files) as finder:
                with mock.patch("unshuffle.runtime.cache.get_file_hash", return_value="hash-kick"):
                    harness._rebuild_index()

            self.assertEqual(finder.call_count, 1)
            self.assertEqual(harness.seen_hashes["hash-kick"], "kick.wav")
            size, mtime = harness.seen_hash_metadata["hash-kick"]
            self.assertEqual(size, sample.stat().st_size)
            self.assertEqual(mtime, sample.stat().st_mtime)

            harness.save_cache()
            harness.db.update_cache_bulk.assert_called_once()
            saved_row = harness.db.update_cache_bulk.call_args.args[0][0]
            self.assertEqual(saved_row[0], "hash-kick")
            self.assertEqual(saved_row[2], sample.stat().st_size)

    def test_load_cache_rebuild_persists_rebuilt_hashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sample = root / "folder" / "kick.wav"
            sample.parent.mkdir()
            sample.write_bytes(b"audio")
            harness = self._Harness(root)

            with mock.patch("unshuffle.runtime.cache.get_file_hash", return_value="hash-kick"):
                self.assertTrue(harness.load_cache(rebuild=True))

            harness.db.update_cache_bulk.assert_called_once()
            saved_row = harness.db.update_cache_bulk.call_args.args[0][0]
            self.assertEqual(saved_row[0], "hash-kick")
            self.assertEqual(saved_row[1], "folder/kick.wav")

    def test_force_cache_reset_clears_cache_rows_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness = self._Harness(Path(tmp))
            harness.seen_hashes = {"hash-old": "old.wav"}
            harness.seen_hash_metadata = {"hash-old": (1, 1.0)}

            self.assertTrue(harness.load_cache(force_reset=True))

            harness.db.clear_cache.assert_called_once_with()
            harness.db.update_cache_bulk.assert_not_called()
            self.assertEqual(harness.seen_hashes, {})
            self.assertEqual(harness.seen_hash_metadata, {})


class AnalysisHashingTests(unittest.TestCase):
    def test_build_node_graph_uses_batched_hash_cache_before_hashing(self):
        from unshuffle.logic.analysis.service import AnalysisContext, build_node_graph

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Source"
            root.mkdir()
            cached = root / "cached.wav"
            uncached = root / "uncached.wav"
            cached.write_bytes(b"cached")
            uncached.write_bytes(b"uncached")

            class _DB:
                def __init__(self):
                    self.file_stats = None

                def get_cached_hashes(self, file_stats):
                    self.file_stats = list(file_stats)
                    return {cached.as_posix(): "hash-cached"}

            db = _DB()
            context = AnalysisContext(root, db=db)

            with mock.patch("unshuffle.core.hashing.get_file_hash", return_value="hash-uncached") as hash_mock:
                build_node_graph(root, context)

            assert db.file_stats is not None
            self.assertEqual(len(db.file_stats), 2)
            self.assertEqual(context.nodes[cached].hash, "hash-cached")
            self.assertEqual(context.nodes[uncached].hash, "hash-uncached")
            hash_mock.assert_called_once_with(uncached)
