from __future__ import annotations

import logging

from .workflow_records import undo_result_summary


def rollback_matches_result(rollback, res) -> bool:
    return bool(
        rollback
        and isinstance(res, dict)
        and str(res.get("session_id") or "") == str(rollback.get("session_id") or "")
    )


def undo_error_message(res, error: str) -> str:
    summary = undo_result_summary(res) if isinstance(res, dict) else ""
    details = []
    failed_paths = res.get("failed_delete_paths") if isinstance(res, dict) else None
    cleanup_paths = res.get("cleanup_failed_paths") if isinstance(res, dict) else None
    if failed_paths:
        details.append("Failed deletes:\n" + "\n".join(str(path) for path in failed_paths[:5]))
    if cleanup_paths:
        details.append("Cleanup failed:\n" + "\n".join(str(path) for path in cleanup_paths[:5]))
    message = f"{summary}\n\n{error}" if summary else f"Undo failed:\n{error}"
    if details:
        message += "\n\n" + "\n\n".join(details)
    return message


def mark_undo_retryable(history_page, session_id: str) -> None:
    if history_page is not None and hasattr(history_page, "mark_retryable"):
        history_page.mark_retryable(session_id)


def refresh_undo_history(history_page, target_root: str, session_id: str) -> None:
    try:
        from ..utils.history import invalidate_history_cache

        invalidate_history_cache(target_root, session_id)
    except Exception:
        logging.debug("Undo history cache invalidation skipped.", exc_info=True)
    if history_page is not None:
        if hasattr(history_page, "mark_undone"):
            history_page.mark_undone(session_id)
        if hasattr(history_page, "refresh_from_target"):
            history_page.refresh_from_target(target_root)


def cancelled_build_rollback_message(rollback) -> str:
    return (
        "Build canceled.\n\n"
        f"Changes were undone for {int(rollback.get('committed_count') or 0)} committed item(s)."
    )
