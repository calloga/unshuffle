"""Exercise dismissal against a real X11 QDialog when the Linux tools are available."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import textwrap

import pytest


@pytest.mark.skipif(
    sys.platform != "linux" or not all(shutil.which(tool) for tool in ("xvfb-run", "xauth", "xdotool")),
    reason="Requires Linux, Xvfb, xauth, and xdotool",
)
def test_real_x11_dialog_closes_on_escape_press(tmp_path):
    launcher = tmp_path / "launcher"
    launcher.write_text(
        f"#!{sys.executable}\n" + textwrap.dedent("""\
        import os
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication, QDialog
        app = QApplication([])
        dialog = QDialog()
        dialog.setWindowTitle("Unshuffle Launcher")
        dialog.show()
        QTimer.singleShot(10000, lambda: os._exit(2))
        dialog.exec()
        """),
        encoding="utf-8",
    )
    launcher.chmod(0o755)
    driver = """
from pathlib import Path
import sys
from scripts.check_linux_bundle import desktop_probe, isolated_environment
root = Path(sys.argv[1])
for i in range(3):
    case = root / str(i)
    case.mkdir()
    desktop_probe(root / 'launcher', isolated_environment(case), case)
"""
    env = dict(os.environ)
    env.pop("QT_QPA_PLATFORM", None)
    result = subprocess.run(
        ["xvfb-run", "-a", sys.executable, "-c", driver, str(tmp_path)],
        cwd=Path(__file__).resolve().parents[1], env=env,
        capture_output=True, text=True, timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.count("Desktop launcher smoke passed") == 3
