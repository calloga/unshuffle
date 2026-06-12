import os
import re
from pathlib import Path
from typing import Union

from .config import get_config
from .constants import IGNORED_SYSTEM_ARTIFACT_NAMES, RESERVED_NAMES


_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize_filename(name: str) -> str:
    """Removes invalid characters for cross-platform folder names."""
    sanitized = _INVALID_FILENAME_CHARS.sub("_", name).strip(" .")
    return sanitized if sanitized else "Unknown_Pack"


def ensure_unique_path(path: Path) -> Path:
    """Appends a numeric suffix until the candidate path is unused."""
    if not path.exists():
        return path

    counter = 1
    original_stem = path.stem
    original_suffix = path.suffix
    parent = path.parent
    candidate = path
    while candidate.exists():
        candidate = parent / f"{original_stem}_{counter}{original_suffix}"
        counter += 1
    return candidate


def is_path_within_directory(path: Path, directory: Path) -> bool:
    """Return True only when path resolves inside directory or is directory itself."""
    try:
        resolved_path = path.resolve()
        resolved_directory = directory.resolve()
        resolved_path.relative_to(resolved_directory)
        return True
    except (OSError, ValueError):
        return False


def is_symlink_or_reparse(path: Path) -> bool:
    """Return True for symlinks and Windows junction/reparse directory entries."""
    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        return bool(is_junction and is_junction())
    except OSError:
        return True


def to_filesystem_path(path: Union[Path, str]) -> str:
    """Return a path string suitable for long Windows filesystem operations."""
    value = str(path)
    if os.name != "nt":
        return value
    if value.startswith("\\\\?\\"):
        return value

    is_unc = value.startswith("\\\\")
    is_drive_absolute = len(value) >= 3 and value[1] == ":" and value[2] in {"\\", "/"}
    if not is_unc and not is_drive_absolute:
        return value

    if is_unc:
        return "\\\\?\\UNC\\" + value.lstrip("\\")

    return "\\\\?\\" + value


def _is_protected_path(path: Path, target_dir: Path) -> bool:
    """
    Returns True if the path should be skipped during scan/traversal.
    Allows scanning of unorganized subfolders inside the target_dir while
    protecting the organized ones.
    """
    path = path.resolve()
    target_dir = target_dir.resolve()
    return _is_protected_path_resolved(path, target_dir)


def _is_protected_path_resolved(path: Path, target_dir: Path) -> bool:
    """Resolved-path variant for hot scan loops that already normalize roots."""
    if path == target_dir:
        return True

    reserved = {str(name).casefold() for name in RESERVED_NAMES}
    if path.parent == target_dir and path.name.casefold() in reserved:
        return True

    return False


def _is_effectively_empty(folder: Path) -> bool:
    """Returns True if the folder is empty or only contains OS hidden files."""
    if not folder.exists() or not folder.is_dir():
        return False

    config_hidden = get_config().get("HIDDEN_SYSTEM_FILES", [])
    hidden_files = {str(name).lower() for name in config_hidden}
    hidden_files.update(str(name).lower() for name in IGNORED_SYSTEM_ARTIFACT_NAMES)
    for item in os.scandir(folder):
        if item.name.lower() not in hidden_files:
            return False
    return True
