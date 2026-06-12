from __future__ import annotations

from pathlib import Path

from unshuffle.core.constants import PRESERVED_MARKER


def create_preserved_marker(path: Path) -> None:
    target_path = Path(path)
    if not target_path.exists():
        target_path.mkdir(parents=True, exist_ok=True)
    (target_path / PRESERVED_MARKER).touch()


def remove_preserved_marker(path: Path) -> bool:
    marker_file = Path(path) / PRESERVED_MARKER
    if not marker_file.exists():
        return False
    marker_file.unlink()
    return True
