"""Filesystem cleanup helpers used after undo operations."""

import logging
import os
import shutil
import stat
import string
from pathlib import Path
from typing import Any, Callable, Iterable

from ..core.constants import IGNORED_SYSTEM_ARTIFACT_NAMES
from ..core.path_safety import _is_effectively_empty, is_path_within_directory
from ..core.paths import SYSTEM_FOLDER_NAME
from ..persistence import DRY_RUN_FOLDER_NAME


_TRANSFER_TEMP_SUFFIX = ".unshuffletmp"
_TRANSFER_TEMP_TOKEN_LENGTH = 8
_TRANSFER_TEMP_TOKEN_CHARS = frozenset(string.ascii_lowercase + string.digits + "_")


def _is_transfer_temp_for_target(name: str, target_name: str) -> bool:
    prefix = f".{target_name}."
    if not name.startswith(prefix) or not name.endswith(_TRANSFER_TEMP_SUFFIX):
        return False
    token = name[len(prefix):-len(_TRANSFER_TEMP_SUFFIX)]
    return (
        len(token) == _TRANSFER_TEMP_TOKEN_LENGTH
        and all(char in _TRANSFER_TEMP_TOKEN_CHARS for char in token)
    )


def cleanup_session_transfer_temps(
    target_dir: Path,
    records: Iterable[dict[str, Any]],
    log: Callable[..., None],
) -> tuple[set[Path], list[str]]:
    """Remove abandoned atomic-transfer files associated with session targets."""
    affected_folders: set[Path] = set()
    cleanup_failures: set[str] = set()
    inspected_targets: set[Path] = set()

    for record in records:
        raw_target = record.get("target_path")
        if not raw_target:
            continue
        target_path = Path(raw_target)
        if not is_path_within_directory(target_path, target_dir):
            continue

        parent = target_path.parent
        affected_folders.add(parent)
        if target_path in inspected_targets or not parent.exists():
            continue
        inspected_targets.add(target_path)

        try:
            with os.scandir(parent) as entries:
                for entry in entries:
                    if not _is_transfer_temp_for_target(entry.name, target_path.name):
                        continue
                    candidate = Path(entry.path)
                    if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                        cleanup_failures.add(str(parent))
                        log(f"  ! Refusing unsafe transfer temp artifact: {candidate}", level=logging.WARNING)
                        continue
                    try:
                        os.chmod(entry.path, stat.S_IREAD | stat.S_IWRITE)
                    except OSError:
                        pass
                    try:
                        os.remove(entry.path)
                        log(f"  + Removed abandoned transfer temp: {candidate.name}")
                    except OSError as exc:
                        cleanup_failures.add(str(parent))
                        log(f"  ! Cleanup Error for {candidate}: {exc}", level=logging.WARNING)
        except OSError as exc:
            cleanup_failures.add(str(parent))
            log(f"  ! Could not inspect transfer temp folder {parent}: {exc}", level=logging.WARNING)

    return affected_folders, sorted(cleanup_failures)


def remove_prefix_legend(target_dir: Path, log: Callable[..., None]) -> None:
    legend_path = target_dir / "prefix_legend.csv"
    if not legend_path.exists():
        return
    try:
        os.chmod(os.fspath(legend_path), stat.S_IREAD | stat.S_IWRITE)
        os.remove(os.fspath(legend_path))
        log("  + Removed: prefix_legend.csv")
    except OSError as exc:
        log(f"  ! Cleanup Error for prefix_legend.csv: {exc}", level=logging.WARNING)


def cleanup_empty_target_folders(
    target_dir: Path,
    target_folders: Iterable[Path],
    log: Callable[..., None],
) -> list[str]:
    all_affected_folders = set()
    for folder in target_folders:
        current = folder
        while current and current != current.parent:
            if current == target_dir:
                break
            if current.name in (SYSTEM_FOLDER_NAME, DRY_RUN_FOLDER_NAME):
                break

            all_affected_folders.add(current)
            current = current.parent

    cleanup_failures = []
    cleaned_count = 0
    for folder in sorted(list(all_affected_folders), key=lambda path: len(path.parts), reverse=True):
        try:
            if folder.exists() and _is_effectively_empty(folder):
                hidden_files = {
                    ".ds_store",
                    "thumbs.db",
                    "desktop.ini",
                    "prefix_legend.csv",
                    *(str(name).lower() for name in IGNORED_SYSTEM_ARTIFACT_NAMES),
                }
                for item in os.scandir(folder):
                    if item.name.lower() in hidden_files:
                        try:
                            os.chmod(item.path, stat.S_IWRITE)
                        except OSError:
                            pass
                        if item.is_dir(follow_symlinks=False):
                            shutil.rmtree(item.path)
                        else:
                            os.remove(item.path)
                os.rmdir(folder)
                cleaned_count += 1
        except OSError as exc:
            cleanup_failures.append(str(folder))
            log(f"  ! Cleanup Error for {folder}: {exc}", level=logging.WARNING)
    if cleaned_count:
        log(f"  - Cleaned {cleaned_count} empty categor{'y' if cleaned_count == 1 else 'ies'}.")
    return cleanup_failures
