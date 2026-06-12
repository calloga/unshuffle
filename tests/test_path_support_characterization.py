import tempfile
import unittest
from pathlib import Path
from unittest import mock

from unshuffle.core.constants import IGNORED_SYSTEM_ARTIFACT_NAMES, RESERVED_NAMES
from unshuffle.core.path_safety import _is_effectively_empty, to_filesystem_path
from unshuffle.core.paths import get_global_system_dir, get_trash_dir
from unshuffle.logic.analysis.service import AnalysisContext, build_node_graph


class PathSupportTests(unittest.TestCase):
    def test_effectively_empty_uses_configured_hidden_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / ".gitkeep").write_text("", encoding="utf-8")

            with mock.patch("unshuffle.core.path_safety.get_config", return_value={"HIDDEN_SYSTEM_FILES": [".gitkeep"]}):
                self.assertTrue(_is_effectively_empty(folder))

    def test_effectively_empty_treats_ignored_artifact_directories_as_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            macosx = folder / "__MACOSX"
            macosx.mkdir()
            (macosx / "._kick.wav").write_text("", encoding="utf-8")

            self.assertTrue(_is_effectively_empty(folder))

    def test_reserved_names_only_include_unshuffle_owned_artifacts(self):
        self.assertIn(".unshuffle", RESERVED_NAMES)
        self.assertIn("DO_NOT_DELETE_unshuffle", RESERVED_NAMES)
        self.assertIn(".unshuffle_hashes.json", RESERVED_NAMES)
        self.assertNotIn("Organized", RESERVED_NAMES)
        self.assertNotIn("Uncategorized", RESERVED_NAMES)
        self.assertNotIn("Non-Audio Assets", RESERVED_NAMES)
        self.assertIn("__MACOSX", IGNORED_SYSTEM_ARTIFACT_NAMES)
        self.assertIn(".ds_store", {name.casefold() for name in IGNORED_SYSTEM_ARTIFACT_NAMES})

    def test_windows_global_system_dir_falls_back_when_appdata_is_missing(self):
        fallback_home = Path("C:/Users/UmU")

        with mock.patch("unshuffle.core.paths.os.name", "nt"), \
             mock.patch("unshuffle.core.paths.os.getenv", side_effect=lambda key, default=None: None), \
             mock.patch("pathlib.Path.home", return_value=fallback_home), \
             mock.patch("pathlib.Path.mkdir"):
            path = get_global_system_dir()

        self.assertEqual(path, fallback_home / "AppData" / "Roaming" / "Unshuffle")

    def test_to_filesystem_path_adds_windows_long_path_prefix_for_absolute_drive_paths(self):
        with mock.patch("unshuffle.core.path_safety.os.name", "nt"):
            result = to_filesystem_path(Path("C:/Samples/Kick.wav"))

        self.assertEqual(result, "\\\\?\\C:\\Samples\\Kick.wav")

    def test_to_filesystem_path_adds_windows_unc_prefix_for_network_paths(self):
        unc_path = "\\\\server\\share\\Kick.wav"
        with mock.patch("unshuffle.core.path_safety.os.name", "nt"):
            result = to_filesystem_path(unc_path)

        self.assertEqual(result, "\\\\?\\UNC\\server\\share\\Kick.wav")

    def test_get_trash_dir_warns_when_session_trash_accumulates(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            trash_root = target / "DO_NOT_DELETE_unshuffle" / "trash"
            for idx in range(26):
                (trash_root / f"session-{idx}").mkdir(parents=True, exist_ok=True)

            with mock.patch("unshuffle.core.paths.logging.warning") as warning_mock:
                folder = get_trash_dir(target, "active-session")

            self.assertEqual(folder, trash_root / "active-session")
            warning_mock.assert_called_once()

    def test_scan_ignores_internal_unshuffle_folders(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".unshuffle").mkdir()
            (root / ".unshuffle" / "lock.json").write_text("{}", encoding="utf-8")
            (root / "DO_NOT_DELETE_unshuffle").mkdir()
            (root / "DO_NOT_DELETE_unshuffle" / "unshuffle.db-wal").write_text("", encoding="utf-8")
            (root / "Audio").mkdir()
            audio = root / "Audio" / "kick.wav"
            audio.write_bytes(b"sound")

            context = AnalysisContext(root)
            build_node_graph(root, context)

            scanned_paths = {path.relative_to(root).as_posix() for path in context.nodes}
            self.assertIn("Audio/kick.wav", scanned_paths)
            self.assertNotIn(".unshuffle", scanned_paths)
            self.assertNotIn(".unshuffle/lock.json", scanned_paths)
            self.assertNotIn("DO_NOT_DELETE_unshuffle", scanned_paths)
            self.assertNotIn("DO_NOT_DELETE_unshuffle/unshuffle.db-wal", scanned_paths)

    def test_scan_allows_output_category_named_folders_but_ignores_system_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            files = [
                root / "Organized" / "kick.wav",
                root / "Uncategorized" / "mystery.wav",
                root / "Non-Audio Assets" / "Pack" / "manual.pdf",
                root / "Oneshots" / "snare.wav",
                root / "Loops" / "break.wav",
                root / "__MACOSX" / "._junk.wav",
            ]
            for path in files:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"data")
            (root / ".DS_Store").write_text("", encoding="utf-8")

            context = AnalysisContext(root)
            build_node_graph(root, context)

            scanned_paths = {path.relative_to(root).as_posix() for path in context.nodes}
            self.assertIn("Organized/kick.wav", scanned_paths)
            self.assertIn("Uncategorized/mystery.wav", scanned_paths)
            self.assertIn("Non-Audio Assets/Pack/manual.pdf", scanned_paths)
            self.assertIn("Oneshots/snare.wav", scanned_paths)
            self.assertIn("Loops/break.wav", scanned_paths)
            self.assertNotIn("__MACOSX", scanned_paths)
            self.assertNotIn("__MACOSX/._junk.wav", scanned_paths)
            self.assertNotIn(".DS_Store", scanned_paths)
