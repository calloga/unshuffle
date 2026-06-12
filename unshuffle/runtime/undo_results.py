"""Undo result payload helpers."""

from pathlib import Path
from typing import Any, Sequence


def undo_failure_result(
    *,
    session_id: str,
    undone: int,
    already_undone: int,
    failed_deletes: Sequence[str],
    cleanup_failures: Sequence[str],
    interrupted: bool,
) -> dict[str, Any]:
    details = []
    if failed_deletes:
        details.append(f"Failed to delete {len(failed_deletes)} copied file(s).")
    if cleanup_failures:
        details.append(f"Final cleanup failed for {len(cleanup_failures)} folder(s).")
    if interrupted:
        details.append("Undo was interrupted.")
    details.append("Session history was preserved so you can retry.")
    return {
        "session_id": session_id,
        "undone": undone,
        "already_undone": already_undone,
        "failed_delete_count": len(failed_deletes),
        "failed_delete_paths": list(failed_deletes[:10]),
        "cleanup_failed_count": len(cleanup_failures),
        "cleanup_failed_paths": list(cleanup_failures[:10]),
        "error": " ".join(details),
    }


def undo_success_result(
    *,
    session_id: str,
    target_dir: Path,
    undone: int,
    sources: Sequence[Any],
    skipped_records: int,
    already_undone: int,
    sidecar_removed: bool,
    sidecar_cleanup_pending: bool,
) -> dict[str, Any]:
    result = {
        "session_id": session_id,
        "target_root": str(target_dir),
        "undone": undone,
        "sources": sources,
    }
    if skipped_records:
        result["skipped_non_committed"] = skipped_records
    if already_undone:
        result["already_undone"] = already_undone
    if sidecar_removed:
        result["sidecar_removed"] = True
    elif sidecar_cleanup_pending:
        result["sidecar_cleanup_pending"] = True
    return result
