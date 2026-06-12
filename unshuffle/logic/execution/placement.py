import os
from pathlib import Path

from ...core.path_safety import _is_effectively_empty, _is_protected_path, to_filesystem_path


def remember_seen_hash(owner, file_hash: str, dest_path: Path) -> None:
    owner.seen_hashes[file_hash] = (
        str(dest_path.relative_to(owner.target_dir))
        if dest_path.is_relative_to(owner.target_dir)
        else str(dest_path)
    )


def prune_empty_source_parents(owner, source_path: Path) -> None:
    try:
        current_parent = source_path.parent
        while current_parent and current_parent != current_parent.parent:
            if not owner._path_under_session_source_root(current_parent):
                break
            if _is_protected_path(current_parent, owner.target_dir):
                break
            if not _is_effectively_empty(current_parent):
                break
            os.rmdir(to_filesystem_path(current_parent))
            current_parent = current_parent.parent
    except OSError:
        pass


def place_record_file(owner, record, dest_path: Path, dest_folder: Path, file_hash: str, *, move: bool, dry_run: bool) -> tuple[str, Path]:
    if not dry_run:
        final_path = owner._execute_file_transfer(record.source_path, dest_path, dest_folder, move, source_hash=file_hash)
        if isinstance(final_path, Path):
            dest_path = final_path
            remember_seen_hash(owner, file_hash, dest_path)
        elif final_path == "stale":
            return "stale", dest_path
        else:
            return "error", dest_path

        if move:
            prune_empty_source_parents(owner, record.source_path)
        return "copied", dest_path

    owner.log(f"  * Result: DRY RUN (Would {'move' if move else 'copy'} to {dest_path.relative_to(owner.target_dir)})")
    remember_seen_hash(owner, file_hash, dest_path)
    return "copied", dest_path

