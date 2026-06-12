import unittest
import tempfile
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch
from unshuffle.persistence import UnshuffleDB

class TestUnshuffleDB(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.test_dir.name) / "test_unshuffle.db"
        self.db = UnshuffleDB(self.db_path)

    def tearDown(self):
        self.db.close()
        self.test_dir.cleanup()

    def test_initialization(self):
        """Verify that the database file is created and schema is initialized."""
        self.assertTrue(self.db_path.exists())
        
        cursor = self.db.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row['name'] for row in cursor.fetchall()}
        expected_tables = {'file_cache', 'sessions', 'session_sources', 'records', 
                           'token_adjustments', 'aliases', 'config_lists', 'exclusions', 
                           'suppression_rules', 'sub_taxonomy', 'staging_records'}
        for table in expected_tables:
            self.assertIn(table, tables)

    def test_cache_operations(self):
        """Test basic CRUD for the file hash cache."""
        file_hash = "abc123hash"
        path = Path("some/audio/file.wav")
        size = 1024
        mtime = 1234567.89
        
        self.db.update_cache(file_hash, path, size, mtime)

        cached_hash = self.db.get_cached_hash(path, size, mtime)
        self.assertEqual(cached_hash, file_hash)

        all_hashes = self.db.get_all_hashes()
        self.assertEqual(all_hashes[file_hash], path.as_posix())

    def test_session_management(self):
        """Test session registration and retrieval."""
        session_id = "test-session-uuid"
        source = Path("/source")
        target = Path("/target")
        
        self.db.register_session(session_id, source, target, "move", is_flat=True)
        
        session = self.db.get_session(session_id)
        self.assertIsNotNone(session)
        assert session is not None
        self.assertEqual(session['session_id'], session_id)
        self.assertEqual(session['mode'], "move")
        self.assertEqual(session['is_flat'], 1)

    def test_register_session_conflict_refreshes_paths_and_mode(self):
        session_id = "refresh-session"
        source_a = Path(self.test_dir.name) / "source-a"
        source_b = Path(self.test_dir.name) / "source-b"
        target_a = Path(self.test_dir.name) / "target-a"
        target_b = Path(self.test_dir.name) / "target-b"

        self.db.register_session(session_id, source_a, target_a, "pending", is_flat=False)
        self.db.register_session(session_id, source_b, target_b, "copy", is_flat=True)

        session = self.db.get_session(session_id)
        self.assertIsNotNone(session)
        assert session is not None
        self.assertEqual(Path(session["source_path"]), source_b.resolve())
        self.assertEqual(Path(session["target_root"]), target_b.resolve())
        self.assertEqual(session["mode"], "copy")
        self.assertEqual(session["is_flat"], 1)

    def test_recent_sessions_are_target_scoped_and_committed_only(self):
        target_a = Path(self.test_dir.name) / "target-a"
        target_b = Path(self.test_dir.name) / "target-b"
        self.db.register_session("a-good", Path(self.test_dir.name) / "src-a", target_a, "copy")
        self.db.register_session("a-failed", Path(self.test_dir.name) / "src-a", target_a, "copy")
        self.db.register_session("b-good", Path(self.test_dir.name) / "src-b", target_b, "copy")
        self.db.add_records_bulk("a-good", [{
            "source_path": "a.wav",
            "target_path": "target/a.wav",
            "category": "Kicks",
            "pack": "Pack",
            "status": "copied",
            "hash": "hash-a",
            "step_status": "COMMITTED",
        }])
        self.db.add_records_bulk("a-failed", [{
            "source_path": "failed.wav",
            "target_path": "target/failed.wav",
            "category": "Kicks",
            "pack": "Pack",
            "status": "error",
            "hash": "hash-failed",
            "step_status": "FAILED",
        }])
        self.db.add_records_bulk("b-good", [{
            "source_path": "b.wav",
            "target_path": "target/b.wav",
            "category": "Kicks",
            "pack": "Pack",
            "status": "copied",
            "hash": "hash-b",
            "step_status": "COMMITTED",
        }])

        sessions = self.db.get_recent_sessions(limit=10, only_executed=True, target_root=target_a)

        self.assertEqual([session["session_id"] for session in sessions], ["a-good"])

    def test_clear_history_for_target_preserves_other_targets_and_staging(self):
        target_a = Path(self.test_dir.name) / "target-a"
        target_b = Path(self.test_dir.name) / "target-b"
        self.db.register_session("a-history", Path(self.test_dir.name) / "src-a", target_a, "copy")
        self.db.register_session("a-staging", Path(self.test_dir.name) / "src-a", target_a, "pending")
        self.db.register_session("a-staging-history", Path(self.test_dir.name) / "src-a", target_a, "copy")
        self.db.register_session("b-history", Path(self.test_dir.name) / "src-b", target_b, "copy")
        for session_id in ["a-history", "a-staging-history", "b-history"]:
            self.db.add_records_bulk(session_id, [{
                "source_path": f"{session_id}.wav",
                "target_path": f"target/{session_id}.wav",
                "category": "Kicks",
                "pack": "Pack",
                "status": "copied",
                "hash": f"hash-{session_id}",
                "step_status": "UNDONE" if session_id == "a-staging-history" else "COMMITTED",
            }])
        self.db.add_staging_records_bulk(
            "a-staging",
            [(1, "staged.wav", "Staged", "Pack", "Drums", "Kick", "Oneshot", "[]", 1.0, 0.1, "hash-staged", "[]", None, None, 0)],
        )
        self.db.add_staging_records_bulk(
            "a-staging-history",
            [(2, "staged-history.wav", "Staged", "Pack", "Drums", "Kick", "Oneshot", "[]", 1.0, 0.1, "hash-staged-history", "[]", None, None, 0)],
        )

        self.db.clear_history_for_target(target_a)

        self.assertIsNone(self.db.get_session("a-history"))
        self.assertIsNotNone(self.db.get_session("a-staging"))
        self.assertIsNotNone(self.db.get_session("a-staging-history"))
        self.assertEqual(self.db.get_session_records("a-staging-history"), [])
        self.assertIsNotNone(self.db.get_session("b-history"))

    def test_committed_hashes_ignore_failed_and_duplicates(self):
        self.db.register_session("hashes", Path(self.test_dir.name) / "src", Path(self.test_dir.name) / "target", "copy")
        self.db.add_records_bulk("hashes", [
            {
                "source_path": "good.wav",
                "target_path": "target/good.wav",
                "category": "Kicks",
                "pack": "Pack",
                "status": "copied",
                "hash": "hash-good",
                "step_status": "COMMITTED",
            },
            {
                "source_path": "failed.wav",
                "target_path": "target/failed.wav",
                "category": "Kicks",
                "pack": "Pack",
                "status": "copied",
                "hash": "hash-failed",
                "step_status": "FAILED",
            },
            {
                "source_path": "duplicate.wav",
                "target_path": "target/duplicate.wav",
                "category": "Kicks",
                "pack": "Pack",
                "status": "duplicate",
                "hash": "hash-dupe",
                "step_status": "COMMITTED",
            },
        ])

        self.assertEqual(self.db.get_committed_hashes(), {"hash-good"})
        self.assertTrue(self.db.has_hash_in_library("hash-good"))
        self.assertFalse(self.db.has_hash_in_library("hash-failed"))
        self.assertFalse(self.db.has_hash_in_library("hash-dupe"))

    def test_bulk_records(self):
        """Test bulk insertion of records."""
        session_id = "bulk-session"
        self.db.register_session(session_id, Path("/s"), Path("/t"), "copy")
        
        records = [
            {
                'source_path': 'src1.wav',
                'target_path': 'tgt1.wav',
                'category': 'Kicks',
                'pack': 'TestPack',
                'status': 'copied',
                'trash_path': 'trash/src1.wav',
            },
            {
                'source_path': 'src2.wav',
                'target_path': 'tgt2.wav',
                'category': 'Snares',
                'pack': 'TestPack',
                'status': 'copied'
            }
        ]
        
        self.db.add_records_bulk(session_id, records)
        
        saved_records = self.db.get_session_records(session_id)
        self.assertEqual(len(saved_records), 2)
        self.assertEqual(saved_records[0]['category'], "Kicks")
        self.assertEqual(saved_records[0]['trash_path'], "trash/src1.wav")
        self.assertEqual(saved_records[1]['category'], "Snares")

    def test_config_seeding(self):
        """Test seeding of configuration lists."""
        noise_words = ["loop", "wav", "test"]
        self.db.seed_config_list('noise_word', noise_words, clear=True)
        
        retrieved = self.db.get_config_list('noise_word')
        self.assertEqual(set(retrieved), set(noise_words))

    def test_fts5_search_sync(self):
        """Verify that FTS5 triggers work when inserting into staging_records."""
        session_id = "search-session"
        self.db.register_session(session_id, Path("/s"), Path("/t"), "copy")
        records = [
            (1, "path/kick.wav", "Kick Drum", "MyPack", "Drums", "Kick", "Oneshot", "[]", "0.9", 0.5, "h1", "[]", None, None, 0),
            (2, "path/snare.wav", "Snare Drum", "MyPack", "Drums", "Snare", "Oneshot", "[]", "0.8", 0.4, "h2", "[]", None, None, 0),
        ]
        
        self.db.add_staging_records_bulk(session_id, records)
        
        cursor = self.db.conn.execute("SELECT row_id FROM staging_fts WHERE sample_name MATCH 'Kick'")
        results = cursor.fetchall()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['row_id'], 1)

    def test_write_transaction_rollback(self):
        """Verify that transactions roll back on error."""
        try:
            with self.db.write_transaction():
                self.db.conn.execute("INSERT INTO config_lists (list_type, value) VALUES (?, ?)", ("test", "val1"))
                raise RuntimeError("Force failure")
        except RuntimeError:
            pass
            
        cursor = self.db.conn.execute("SELECT COUNT(*) FROM config_lists WHERE list_type = 'test'")
        self.assertEqual(cursor.fetchone()[0], 0)

if __name__ == "__main__":
    unittest.main()
