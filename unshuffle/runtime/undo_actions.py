"""Per-record undo actions."""

import os
import logging
import shutil
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..persistence import get_trash_dir


@dataclass
class UndoRecordOutcome:
    undone: int = 0
    already_undone: int = 0
    completed: int = 0
    failed: bool = False
    relative_paths: list[str] = field(default_factory=list)
    target_folders: set[Path] = field(default_factory=set)


def delete_undo_target(path: Path) -> None:
    try:
        os.chmod(os.fspath(path), stat.S_IREAD | stat.S_IWRITE)
    except OSError:
        pass
    os.remove(os.fspath(path))


def target_relative_path(target_dir: Path, target_path: Path) -> str:
    try:
        return str(target_path.relative_to(target_dir))
    except ValueError:
        return str(target_path)


def undo_record_action(
    *,
    record: dict[str, Any],
    mode: str,
    session_id: str,
    target_dir: Path,
    record_action_fn: Callable[[dict, str], str],
    duplicate_trash_path_fn: Callable[[dict, Path], Path],
    log: Callable[..., None],
) -> UndoRecordOutcome:
    src, tgt = Path(record["source_path"]), Path(record["target_path"])
    record_action = record_action_fn(record, mode)

    if record.get("status") == "duplicate":
        if record_action == "move":
            trash_dir = get_trash_dir(target_dir, session_id)
            trash_path = duplicate_trash_path_fn(record, trash_dir)
            if trash_path.exists():
                src.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(trash_path), str(src))
                log(f"  + Restored duplicate from Trash: {src.name}")
                return UndoRecordOutcome(undone=1, completed=1)
            log(f"  ! Trash missing for duplicate: {src.name}", level=logging.WARNING)
            return UndoRecordOutcome(failed=True)
        log(f"  - Duplicate skip (already in source): {src.name}")
        return UndoRecordOutcome(completed=1)

    if bool(record.get("is_preserved")):
        if record_action == "move":
            src.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(tgt), str(src))
            log(f"  + Restored preserved folder: {src.name}")
        else:
            if tgt.exists():
                shutil.rmtree(str(tgt))
                log(f"  + Removed preserved folder: {tgt.name}")
        return UndoRecordOutcome(undone=1, completed=1, target_folders={tgt.parent})

    record_undone = False
    already_undone = 0
    if record_action == "move":
        src.parent.mkdir(parents=True, exist_ok=True)
        if src.name.lower() == tgt.name.lower() and src.name != tgt.name:
            temp_tgt = tgt.with_name(tgt.name + ".undotmp")
            shutil.move(str(tgt), str(temp_tgt))
            shutil.move(str(temp_tgt), str(src))
        else:
            shutil.move(str(tgt), str(src))
        log(f"  + Restored: {src.name}")
        record_undone = True
    else:
        if tgt.exists():
            delete_undo_target(tgt)
            log(f"  + Removed: {tgt.name}")
            record_undone = True
        else:
            already_undone = 1
            log(f"  - Already removed: {tgt.name}")

    return UndoRecordOutcome(
        undone=1 if record_undone else 0,
        already_undone=already_undone,
        completed=1,
        relative_paths=[target_relative_path(target_dir, tgt)],
        target_folders={tgt.parent},
    )
