"""Filesystem cleanup helpers used after undo operations."""

import logging
import os
import shutil
import stat
from pathlib import Path
from typing import Callable, Iterable

from ..core.constants import IGNORED_SYSTEM_ARTIFACT_NAMES
from ..core.path_safety import _is_effectively_empty
from ..core.paths import SYSTEM_FOLDER_NAME
from ..persistence import DRY_RUN_FOLDER_NAME


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
                log(f"  - Cleaned empty category: {folder.name}")
        except OSError as exc:
            cleanup_failures.append(str(folder))
            log(f"  ! Cleanup Error for {folder}: {exc}", level=logging.WARNING)
    return cleanup_failures
