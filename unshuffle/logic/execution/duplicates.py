import logging
from pathlib import Path
from typing import Callable

from ...core.path_safety import ensure_unique_path, to_filesystem_path


def existing_duplicate_path(owner, file_hash: str) -> str | None:
    if file_hash in owner.seen_hashes:
        return owner.seen_hashes[file_hash]
    if hasattr(owner, "db") and hasattr(owner.db, "bloom_filter") and file_hash in owner.db.bloom_filter:
        return owner.db.get_cached_path_by_hash(file_hash)
    return None


def handle_duplicate_record(
    owner,
    record,
    file_hash: str,
    *,
    move: bool,
    dry_run: bool,
    move_file: Callable[[str, str], object],
) -> str | None:
    existing_path_str = existing_duplicate_path(owner, file_hash)
    if not existing_path_str:
        return None
    if not (owner.target_dir / existing_path_str).exists():
        return None
    if dry_run:
        owner.log("  - Result: [DRY RUN] (File exists in library, would skip duplicate)")
        return "duplicate"
    owner.log("  - Result: ALREADY EXISTS in library (Staging duplicate to trash)")
    if not move:
        return "duplicate"
    try:
        from ...persistence import get_trash_dir

        trash_dir = get_trash_dir(owner.target_dir, owner.session_id)
        trash_path = ensure_unique_path(trash_dir / record.source_path.name)
        move_file(
            to_filesystem_path(record.source_path),
            to_filesystem_path(trash_path),
        )
        owner._last_duplicate_trash_path = trash_path
        owner._last_effective_action = "move"
    except Exception as err:
        logging.error(
            "Failed to move duplicate to trash: %s. Source file left untouched to prevent data loss.",
            err,
        )
        return "error"
    return "duplicate"
