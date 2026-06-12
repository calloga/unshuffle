import subprocess
import sys
from pathlib import Path


def test_layer_import_guard_script_passes():
    root = Path(__file__).resolve().parent.parent
    result = subprocess.run(
        [sys.executable, "scripts/check_layer_imports.py"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "No layer-graph violations found." in result.stdout
