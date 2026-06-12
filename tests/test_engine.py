import unittest
import tempfile
import os
from pathlib import Path
from unittest.mock import MagicMock, patch, call
from unshuffle.runtime.engine import RuntimeUnshuffler as Unshuffler
from unshuffle.core import PlanRecord, get_file_hash
from gui.core.workflow_records import build_result_summary

class TestEngine(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.TemporaryDirectory()
        self.target_dir = Path(self.test_dir.name) / "Library"
        self.target_dir.mkdir()
        
        self.mock_db = MagicMock()
        self.mock_db.get_token_adjustments.return_value = {}
        self.mock_db.get_exclusions.return_value = []
        
        self.mock_bootstrapper = MagicMock()
        self.mock_bootstrapper.get_local_db_fn.return_value = MagicMock()
        
        self.patcher_global_db = patch("unshuffle.persistence.get_db", return_value=self.mock_db)
        self.patcher_cache = patch("unshuffle.runtime.engine.CacheMixin.load_cache")
        self.patcher_lock = patch("unshuffle.runtime.engine.RuntimeUnshuffler._acquire_lock")
        
        self.patcher_global_db.start()
        self.patcher_cache.start()
        self.patcher_lock.start()
        
        self.engine = Unshuffler(self.target_dir, bootstrapper=self.mock_bootstrapper)
        self.engine.db = self.mock_db

    def tearDown(self):
        self.patcher_global_db.stop()
        self.patcher_cache.stop()
        self.patcher_lock.stop()
        self.test_dir.cleanup()

    def test_prepare_plan_source_deduplication(self):
        """Verify that overlapping source paths are deduplicated."""
        source1 = self.target_dir / "Source"
        source2 = source1 / "Subfolder"
        source1.mkdir()
        source2.mkdir()
        
        with patch("unshuffle.logic.planning.run_plan", return_value=[]):
            self.engine.prepare_plan([source1, source2])
            
            self.assertEqual(len(self.engine.session_source_roots), 1)
            self.assertEqual(self.engine.session_source_roots[0], source1)

    def test_execute_plan_low_battery(self):
        """Verify emergency suspension when battery is critical."""
        mock_psutil = MagicMock()
        mock_psutil.sensors_battery.return_value = MagicMock(percent=3, power_plugged=False)
        
        plan = [PlanRecord(Path("test.wav"), "Pack", "Cat", "Type", "1.0")]
        
        with patch.dict("sys.modules", {"psutil": mock_psutil}):
            with patch("unshuffle.runtime.engine.RuntimeUnshuffler._process_single_record") as mock_proc:
                result = self.engine.execute_plan(plan)
                
                self.assertEqual(result["error"], "Low Battery Emergency")
                self.assertEqual(mock_proc.call_count, 0)

    def test_execute_plan_aggregation(self):
        """Verify result aggregation from record processing."""
        plan = [
            PlanRecord(Path("a.wav"), "P", "C", "T", "1.0"),
            PlanRecord(Path("b.wav"), "P", "C", "T", "1.0")
        ]
        
        def mock_proc(rec, move, dry_run, flat, no_prefix, csv_writer):
            if "a.wav" in str(rec.source_path):
                return "copied", Path("target/a.wav")
            return "duplicate", Path("target/b.wav")

        with patch("unshuffle.runtime.engine.RuntimeUnshuffler._process_single_record", side_effect=mock_proc):
            with patch("unshuffle.core.logging.setup_logging"):
                result = self.engine.execute_plan(plan, dry_run=False)
                
                self.assertEqual(result["total"], 2)
                self.assertEqual(result["copied"], 1)
                self.assertEqual(result["duplicates"], 1)

    def test_execute_plan_uses_available_local_db_when_global_db_is_missing(self):
        plan = [PlanRecord(Path("a.wav"), "P", "C", "T", "1.0")]
        local_db = MagicMock()
        local_db.get_session_sources.return_value = []
        self.engine.db = None
        self.engine.local_db = local_db

        def mock_proc(rec, move, dry_run, flat, no_prefix, csv_writer):
            self.engine._last_record_hash = "computed-hash"
            self.engine._last_effective_action = "copy"
            self.engine.seen_hashes["computed-hash"] = "a.wav"
            self.engine.seen_hash_metadata["computed-hash"] = (123, 456.0)
            return "copied", self.target_dir / "a.wav"

        with patch("unshuffle.runtime.engine.RuntimeUnshuffler._process_single_record", side_effect=mock_proc):
            result = self.engine.execute_plan(plan, move=False, dry_run=False)

        self.assertIsNone(result["error"])
        self.assertEqual(result["copied"], 1)
        local_db.register_session.assert_called_once()
        local_db.add_records_bulk.assert_called_once()
        local_db.update_cache_bulk.assert_called_once()
        self.assertIsNone(self.engine.db)

    def test_execute_plan_persists_computed_hash_and_effective_action(self):
        plan = [PlanRecord(Path("a.wav"), "P", "C", "T", "1.0")]

        def mock_proc(rec, move, dry_run, flat, no_prefix, csv_writer):
            self.engine._last_record_hash = "computed-hash"
            self.engine._last_effective_action = "copy"
            return "copied", self.target_dir / "a.wav"

        with patch("unshuffle.runtime.engine.RuntimeUnshuffler._process_single_record", side_effect=mock_proc):
            result = self.engine.execute_plan(plan, move=True, dry_run=False)

        self.assertIsNone(result["error"])
        self.assertEqual(result["fallback_copies"], 1)
        records = self.mock_db.add_records_bulk.call_args.args[1]
        self.assertEqual(records[0]["hash"], "computed-hash")
        self.assertEqual(records[0]["original_action"], "copy")
        self.assertEqual(records[0]["step_status"], "COMMITTED")

    def test_execute_plan_persists_preserved_metadata(self):
        preserved_root = Path("Source/HANDSOFF")
        plan = [PlanRecord(Path("Source/HANDSOFF/file.wav"), "P", "Preserved", "Utility", "1.0")]
        plan[0].is_preserved = True
        plan[0].preserved_root = preserved_root

        def mock_proc(rec, move, dry_run, flat, no_prefix, csv_writer):
            self.engine._last_record_hash = "PRESERVED_SESS_SKIPPED"
            self.engine._last_effective_action = "copy"
            return "copied", self.target_dir / "HANDSOFF"

        with patch("unshuffle.runtime.engine.RuntimeUnshuffler._process_single_record", side_effect=mock_proc):
            self.engine.execute_plan(plan, move=False, dry_run=False)

        records = self.mock_db.add_records_bulk.call_args.args[1]
        self.assertTrue(records[0]["is_preserved"])
        self.assertEqual(records[0]["preserved_root"], preserved_root)
        self.assertEqual(records[0]["hash"], "PRESERVED_SESS_SKIPPED")

    def test_execute_plan_reports_failed_records(self):
        plan = [PlanRecord(Path("a.wav"), "P", "C", "T", "1.0")]

        def mock_proc(rec, move, dry_run, flat, no_prefix, csv_writer):
            self.engine._last_record_hash = None
            self.engine._last_effective_action = "copy"
            return "error", self.target_dir / "a.wav"

        with patch("unshuffle.runtime.engine.RuntimeUnshuffler._process_single_record", side_effect=mock_proc):
            result = self.engine.execute_plan(plan, dry_run=False)

        self.assertEqual(result["failed"], 1)
        self.assertIn("failed", result["error"])
        self.assertEqual(result["failed_record_count"], 1)
        self.assertEqual(result["failed_records"][0]["source_path"], "a.wav")
        records = self.mock_db.add_records_bulk.call_args.args[1]
        self.assertEqual(records[0]["step_status"], "FAILED")

    def test_duplicate_trash_move_failure_is_not_recorded_as_committed_duplicate(self):
        source = self.target_dir / "source.wav"
        existing = self.target_dir / "existing.wav"
        source.write_bytes(b"sound")
        existing.write_bytes(b"sound")
        record = PlanRecord(source, "P", "C", "T", "1.0")
        record.hash = "a" * 32
        self.engine.seen_hashes = {record.hash: existing.name}

        with patch("unshuffle.logic.execution.service.shutil.move", side_effect=OSError("locked")):
            result, _dest = self.engine._process_single_record(record, move=True, dry_run=False, flat=False, no_prefix=False, csv_writer=None)

        self.assertEqual(result, "error")
        self.assertTrue(source.exists())

    def test_preserved_folder_child_failure_is_reported_as_error(self):
        source_root = self.target_dir.parent / "Source"
        preserved_root = source_root / "HANDSOFF"
        preserved_root.mkdir(parents=True)
        source_file = preserved_root / "file.wav"
        source_file.write_bytes(b"sound")
        (self.target_dir / "HANDSOFF").mkdir()
        record = PlanRecord(source_file, "P", "Preserved", "Utility", "1.0")
        record.is_preserved = True
        record.preserved_root = preserved_root
        self.engine.session_source_roots = [source_root]

        with patch.object(self.engine, "_execute_file_transfer", return_value=None):
            result, _dest = self.engine._process_single_record(record, move=True, dry_run=False, flat=False, no_prefix=False, csv_writer=None)

        self.assertEqual(result, "error")
        self.assertNotIn(preserved_root, self.engine.moved_preserved_roots)

    def test_move_transfer_removes_readonly_source_after_verified_copy(self):
        source = self.target_dir.parent / "source" / "readonly.wav"
        source.parent.mkdir()
        source.write_bytes(b"sound")
        source.chmod(0o444)
        dest_folder = self.target_dir / "Oneshots" / "Kicks"
        dest = dest_folder / "readonly.wav"

        try:
            result = self.engine._execute_file_transfer(source, dest, dest_folder, move=True, source_hash=get_file_hash(source))
        finally:
            if source.exists():
                source.chmod(0o666)

        self.assertEqual(result, dest)
        self.assertFalse(source.exists())
        self.assertTrue(dest.exists())

    def test_build_result_summary_includes_partial_failure_counts(self):
        summary = build_result_summary({
            "total": 5,
            "copied": 2,
            "duplicates": 1,
            "failed": 1,
            "stale": 1,
            "interrupted": 1,
            "move": False,
        })

        self.assertIn("Copied 2 of 5 files.", summary)
        self.assertIn("Skipped 1 duplicates.", summary)
        self.assertIn("Failed 1.", summary)
        self.assertIn("Stale 1.", summary)
        self.assertIn("Interrupted 1.", summary)

    def test_build_result_summary_reports_hardlink_fallback_copies_in_move_mode(self):
        summary = build_result_summary({
            "total": 10,
            "copied": 8,
            "fallback_copies": 6,
            "duplicates": 0,
            "failed": 2,
            "stale": 0,
            "interrupted": 0,
            "move": True,
        })

        self.assertIn("Moved 2 of 10 files.", summary)
        self.assertIn("Copied 6 hardlinked file(s) instead", summary)
        self.assertIn("originals remain in the source", summary)
        self.assertIn("Failed 2.", summary)

    def test_build_result_summary_reports_scan_skipped_duplicates(self):
        summary = build_result_summary({
            "total": 10,
            "copied": 8,
            "skipped_duplicates": 384,
            "move": True,
        })

        self.assertIn("Moved 8 of 10 files.", summary)
        self.assertIn("384 duplicate source file(s) were skipped during scan", summary)
        self.assertIn("left in place", summary)
        self.assertIn("not part of this undo session", summary)

    def test_undo_session_trash_restoration(self):
        """Verify that duplicates are restored from trash during undo-move."""
        session_id = "undo-test"
        src = self.target_dir / "Original" / "duplicate.wav"
        tgt = self.target_dir / "Library" / "duplicate.wav"
        trash_dir = self.target_dir / "DO_NOT_DELETE_unshuffle" / "trash" / session_id
        trash_dir.mkdir(parents=True)
        trash_file = trash_dir / "duplicate.wav"
        trash_file.touch()
        
        records = [{
            "source_path": str(src),
            "target_path": str(tgt),
            "status": "duplicate"
        }]
        
        self.mock_db.get_session_records.return_value = records
        self.mock_db.get_session.return_value = {"mode": "move"}
        
        with patch("shutil.move") as mock_move:
            self.engine.undo_session(session_id)

            mock_move.assert_called_with(str(trash_file), str(src))

    def test_undo_session_empty_folder_cleanup(self):
        """Verify that empty category folders are pruned after undo."""
        session_id = "prune-test"
        cat_dir = self.target_dir / "Kicks"
        cat_dir.mkdir()
        tgt = cat_dir / "kick.wav"
        tgt.write_bytes(b"sound")
        
        records = [{
            "source_path": "any",
            "target_path": str(tgt),
            "status": "copied",
            "file_hash": get_file_hash(tgt),
            "step_status": "COMMITTED",
            "original_action": "copy",
        }]
        
        self.mock_db.get_session_records.return_value = records
        self.mock_db.get_session.return_value = {"mode": "copy"}
        
        with patch("unshuffle.core.path_safety._is_effectively_empty", return_value=True):
            with patch("os.rmdir") as mock_rmdir:
                self.engine.undo_session(session_id)
                mock_rmdir.assert_called_with(cat_dir)

if __name__ == "__main__":
    unittest.main()
