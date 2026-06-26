import unittest
import tempfile
import os
from pathlib import Path
from unittest.mock import patch
from unshuffle import persistence
from unshuffle.core.paths import SYSTEM_FOLDER_NAME


class TestPersistence(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.TemporaryDirectory()
        self.target_dir = Path(self.test_dir.name)

    def tearDown(self):
        self.test_dir.cleanup()

    def test_get_system_dir_dry_run(self):
        """Dry run should use a local 'dry_run' folder."""
        dir_path = persistence.get_system_dir(self.target_dir, is_dry_run=True)
        self.assertEqual(dir_path, self.target_dir / "dry_run")
        self.assertTrue(dir_path.exists())

    @patch("unshuffle.persistence.get_global_system_dir")
    def test_get_system_dir_standard(self, mock_global):
        """Standard run should use the global AppData folder."""
        mock_global.return_value = self.target_dir / "GlobalApp"
        dir_path = persistence.get_system_dir(self.target_dir, is_dry_run=False)
        self.assertEqual(dir_path, self.target_dir / "GlobalApp")
        self.assertTrue(dir_path.exists())

    def test_get_local_system_dir(self):
        """Sidecar folder should be created in target_dir."""
        dir_path = persistence.get_local_system_dir(self.target_dir)
        self.assertEqual(dir_path, self.target_dir / SYSTEM_FOLDER_NAME)
        self.assertTrue(dir_path.exists())

    def test_get_local_system_dir_rejects_file_at_sidecar_path(self):
        """Sidecar metadata path must be a folder, not an existing file."""
        sidecar_path = self.target_dir / SYSTEM_FOLDER_NAME
        sidecar_path.write_text("not a directory", encoding="utf-8")

        with self.assertRaisesRegex(RuntimeError, "metadata path exists but is not a folder"):
            persistence.get_local_system_dir(self.target_dir)

    def test_database_open_error_names_blocked_folder_path(self):
        """Database initialization should surface the blocked path, not a vague SQLite error."""
        blocked_parent = self.target_dir / "blocked"
        blocked_parent.write_text("not a directory", encoding="utf-8")

        with self.assertRaisesRegex(RuntimeError, "Database folder path exists but is not a folder"):
            persistence.UnshuffleDB(blocked_parent / "unshuffle.db")

    def test_json_meta_io(self):
        """Verify atomic save and load of JSON metadata."""
        data = {"key": "value", "nested": [1, 2, 3]}
        filename = "test_meta.json"
        
        persistence.save_json_meta(self.target_dir, filename, data, is_dry_run=True)
        
        # Verify it exists in the dry_run folder
        file_path = self.target_dir / "dry_run" / filename
        self.assertTrue(file_path.exists())
        
        # Load it back
        loaded = persistence.load_json_meta(self.target_dir, filename, is_dry_run=True)
        self.assertEqual(loaded, data)

    def test_json_meta_corrupt(self):
        """Verify load returns None on corrupt JSON."""
        filename = "corrupt.json"
        dry_run_dir = self.target_dir / "dry_run"
        dry_run_dir.mkdir()
        with open(dry_run_dir / filename, "w") as f:
            f.write("{invalid json")
            
        loaded = persistence.load_json_meta(self.target_dir, filename, is_dry_run=True)
        self.assertIsNone(loaded)

    def test_pre_v1_cache_migration_api_is_not_part_of_v1(self):
        self.assertFalse(hasattr(persistence, "migrate_legacy_cache"))

if __name__ == "__main__":
    unittest.main()
