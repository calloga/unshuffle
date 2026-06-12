import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from unshuffle.diagnostics import MAX_LAUNCHER_LOG_BYTES, get_version_report, write_launcher_crash_log


class DiagnosticsTests(unittest.TestCase):
    def test_launcher_crash_log_is_written_to_global_support_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            support_dir = Path(tmp)
            with mock.patch("unshuffle.diagnostics.get_global_system_dir", return_value=support_dir):
                path = write_launcher_crash_log("gui_launcher", trace_text="trace line")

            self.assertIsNotNone(path)
            self.assertTrue(path.exists())
            contents = path.read_text(encoding="utf-8")
            self.assertIn("Unshuffle launcher crash", contents)
            self.assertIn("trace line", contents)

    def test_version_report_handles_available_extractor_probe(self):
        fake_extractor = Path("C:/tmp/unshuffle_extractor.exe") if os.name == "nt" else Path("/tmp/unshuffle_extractor")

        class _FakeEngine:
            def __init__(self):
                self.extractor_path = str(fake_extractor)

        completed = subprocess.CompletedProcess(
            args=[str(fake_extractor), "--version"],
            returncode=0,
            stdout="unshuffle_extractor 2.0.0\n",
            stderr="",
        )

        with mock.patch("unshuffle.diagnostics.SimilarityEngine", _FakeEngine), \
             mock.patch("pathlib.Path.exists", return_value=True), \
             mock.patch("subprocess.run", return_value=completed):
            report = get_version_report()

        self.assertEqual(report["app_version"], "1.0.0")
        self.assertEqual(report["native_available"], "yes")
        self.assertEqual(report["native_version"], "unshuffle_extractor 2.0.0")

    def test_extractor_version_policy_is_v1(self):
        from unshuffle.core.features import CURRENT_EXTRACTOR_VERSION

        self.assertEqual(CURRENT_EXTRACTOR_VERSION, "unshuffle_extractor 1.0.0")
        source = Path("unshuffle_extractor/unshuffle_extractor.cpp").read_text(encoding="utf-8")
        self.assertIn("unshuffle_extractor 1.0.0", source)
        self.assertNotIn("unshuffle_extractor 1.1.0", source)

        if os.name == "nt":
            completed = subprocess.run(
                [str(Path.cwd() / "bin" / "windows" / "unshuffle_extractor.exe"), "--version"],
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertEqual(completed.stdout.strip(), "unshuffle_extractor 1.0.0")

    def test_launcher_crash_log_is_trimmed_when_it_grows_too_large(self):
        with tempfile.TemporaryDirectory() as tmp:
            support_dir = Path(tmp)
            log_path = support_dir / "gui_launcher_crash.log"
            log_path.write_text("x" * (MAX_LAUNCHER_LOG_BYTES + 128), encoding="utf-8")

            with mock.patch("unshuffle.diagnostics.get_global_system_dir", return_value=support_dir):
                path = write_launcher_crash_log("gui_launcher", trace_text="trimmed trace")

            self.assertEqual(path, log_path)
            self.assertLessEqual(path.stat().st_size, MAX_LAUNCHER_LOG_BYTES + 1024)
            self.assertIn("trimmed trace", path.read_text(encoding="utf-8"))
