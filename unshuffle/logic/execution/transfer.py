import logging
import os
import shutil
import stat
import tempfile
from pathlib import Path
from typing import Literal, Optional

from ...core.hashing import get_file_hash
from ...core.path_safety import (
    _is_effectively_empty,
    ensure_unique_path,
    is_path_within_directory,
    is_symlink_or_reparse,
    to_filesystem_path,
)


def execute_file_transfer(
    owner,
    source_path: Path,
    dest_path: Path,
    dest_folder: Path,
    move: bool,
    source_hash: Optional[str] = None,
) -> Path | Literal["stale"] | None:
    """Copy or move one file to its destination with atomic verification."""
    tmp_path: Optional[Path] = None
    placed_dest = False
    try:
        if is_symlink_or_reparse(source_path):
            owner.log(f"  ! Refusing symlink source: {source_path}", level=logging.ERROR)
            return None
        if not is_path_within_directory(dest_folder, owner.target_dir):
            owner.log(f"  ! Refusing destination outside target: {dest_folder}", level=logging.ERROR)
            return None
        if not is_path_within_directory(dest_path, owner.target_dir):
            owner.log(f"  ! Refusing destination outside target: {dest_path}", level=logging.ERROR)
            return None
        dest_folder.mkdir(parents=True, exist_ok=True)

        if move and source_path.exists() and source_path.stat().st_nlink > 1:
            move = False
            owner._last_effective_action = "copy"
            owner.log(f"Hardlink detected for {source_path.name}. Falling back to copy.", level=logging.WARNING)
        dest_path = ensure_unique_path(dest_path)

        temp_fd, temp_name = tempfile.mkstemp(
            prefix=f".{dest_path.name}.",
            suffix=".unshuffletmp",
            dir=to_filesystem_path(dest_folder),
        )
        os.close(temp_fd)
        tmp_path = Path(temp_name)
        shutil.copy2(to_filesystem_path(source_path), to_filesystem_path(tmp_path))

        is_valid_hash = (
            source_hash
            and all(char in "0123456789abcdefABCDEF" for char in source_hash)
            and len(source_hash) >= 32
        )

        if not is_valid_hash:
            source_hash = get_file_hash(source_path)
            if not source_hash:
                return None
        owner._last_record_hash = source_hash

        temp_hash = get_file_hash(tmp_path)

        if source_hash != temp_hash:
            raise IOError(f"STALE_DATA: {source_path.name} has changed since scan.")

        os.replace(to_filesystem_path(tmp_path), to_filesystem_path(dest_path))
        placed_dest = True

        if move:
            try:
                os.chmod(to_filesystem_path(source_path), stat.S_IREAD | stat.S_IWRITE)
            except OSError:
                pass
            os.remove(to_filesystem_path(source_path))

        return dest_path
    except Exception as exc:
        if tmp_path and tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
        if move and placed_dest and dest_path.exists():
            try:
                os.chmod(to_filesystem_path(dest_path), stat.S_IREAD | stat.S_IWRITE)
            except OSError:
                pass
            try:
                os.remove(to_filesystem_path(dest_path))
            except OSError:
                pass

        if "STALE_DATA" in str(exc):
            return "stale"

        owner.log(f"  ! TRANSFER ERROR: {exc}", level=logging.ERROR)
        return None


def execute_folder_transfer(owner, source_dir: Path, dest_dir: Path, move: bool) -> bool:
    """Copy or move one directory tree with merge logic."""
    if not source_dir.exists():
        return False
    if is_symlink_or_reparse(source_dir):
        owner.log(f"  ! Refusing symlink folder source: {source_dir}", level=logging.ERROR)
        return False
    if not is_path_within_directory(dest_dir, owner.target_dir):
        owner.log(f"  ! Refusing folder destination outside target: {dest_dir}", level=logging.ERROR)
        return False
    if not validate_folder_transfer_tree(owner, source_dir):
        return False

    try:
        if not dest_dir.exists():
            if move:
                shutil.move(to_filesystem_path(source_dir), to_filesystem_path(dest_dir))
            else:
                shutil.copytree(to_filesystem_path(source_dir), to_filesystem_path(dest_dir))
        else:
            all_transferred = True
            for item in os.listdir(to_filesystem_path(source_dir)):
                source_item = source_dir / item
                dest_item = dest_dir / item
                if source_item.is_dir():
                    if not owner._execute_folder_transfer(source_item, dest_item, move):
                        all_transferred = False
                else:
                    result = owner._execute_file_transfer(source_item, dest_item, dest_dir, move)
                    if not isinstance(result, Path):
                        all_transferred = False
            if move:
                try:
                    if _is_effectively_empty(source_dir):
                        shutil.rmtree(to_filesystem_path(source_dir))
                    else:
                        logging.warning("Source directory %s not empty after move. Skipping deletion.", source_dir)
                except OSError as err:
                    logging.debug("Failed to remove source directory %s: %s", source_dir, err)
                    all_transferred = False
            if not all_transferred:
                return False
        return True
    except Exception as exc:
        owner.log(f"  ! FOLDER TRANSFER ERROR: {exc}", level=logging.ERROR)
        return False


def validate_folder_transfer_tree(owner, source_dir: Path) -> bool:
    stack = [source_dir]
    while stack:
        current = stack.pop()
        if is_symlink_or_reparse(current):
            owner.log(f"  ! Refusing symlink/reparse entry in preserved folder: {current}", level=logging.ERROR)
            return False
        try:
            if current.is_dir():
                with os.scandir(to_filesystem_path(current)) as entries:
                    for entry in entries:
                        child = Path(entry.path)
                        if is_symlink_or_reparse(child):
                            owner.log(
                                f"  ! Refusing symlink/reparse entry in preserved folder: {child}",
                                level=logging.ERROR,
                            )
                            return False
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(child)
        except OSError as exc:
            owner.log(f"  ! Could not inspect preserved folder entry {current}: {exc}", level=logging.ERROR)
            return False
    return True
