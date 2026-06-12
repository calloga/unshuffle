"""Execution record payload helpers for runtime session persistence."""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class ExecutionResultCounts:
    copied: int = 0
    fallback_copies: int = 0
    duplicates: int = 0
    stale: int = 0
    failed: int = 0
    interrupted: int = 0

    def record(self, result: str, *, move: bool, record_action: str) -> bool:
        if result == "duplicate":
            self.duplicates += 1
        elif result == "copied":
            self.copied += 1
            if move and record_action == "copy":
                self.fallback_copies += 1
        elif result == "stale":
            self.stale += 1
        elif result == "interrupted":
            self.interrupted += 1
        elif result == "error":
            self.failed += 1
            return True
        return False


def failed_record_payload(record: Any, target_path: Path | None) -> dict[str, str]:
    return {
        "source_path": str(record.source_path),
        "target_path": str(target_path or ""),
        "file_name": record.source_path.name,
    }


def execution_step_status(result: str) -> str:
    if result in {"copied", "duplicate"}:
        return "COMMITTED"
    if result == "interrupted":
        return "INTERRUPTED"
    return "FAILED"


def execution_error_summary(counts: ExecutionResultCounts) -> str | None:
    if counts.failed:
        return f"{counts.failed} file(s) failed during build."
    if counts.stale:
        return f"{counts.stale} stale file(s) skipped during build."
    if counts.interrupted:
        return f"{counts.interrupted} file(s) were interrupted during build."
    return None


def empty_execution_result(*, dry_run: bool) -> dict[str, Any]:
    return {"total": 0, "copied": 0, "duplicates": 0, "dry_run": dry_run}


def invalid_tree_profile_result(total_files: int, dry_run: bool, message: str) -> dict[str, Any]:
    return {
        "total": total_files,
        "copied": 0,
        "duplicates": 0,
        "dry_run": dry_run,
        "error": f"Custom tree organization is invalid.\n{message}",
    }


def execution_result_payload(
    *,
    total_files: int,
    counts: ExecutionResultCounts,
    failed_records: list[dict[str, str]],
    dry_run: bool,
    move: bool,
    session_id: str,
    report_path: Path | None,
    error: str | None,
) -> dict[str, Any]:
    return {
        "total": total_files,
        "copied": counts.copied,
        "fallback_copies": counts.fallback_copies,
        "duplicates": counts.duplicates,
        "stale": counts.stale,
        "failed": counts.failed,
        "failed_records": failed_records[:50],
        "failed_record_count": len(failed_records),
        "interrupted": counts.interrupted,
        "dry_run": dry_run,
        "move": move,
        "session_id": session_id,
        "report_path": report_path,
        "error": error,
    }


def session_record_payload(
    record: Any,
    target_path: Path | None,
    result: str,
    record_hash: str,
    record_action: str,
    duplicate_trash_path: Path | None,
) -> dict[str, Any]:
    tags = getattr(record, "tags", [])
    return {
        "source_path": record.source_path,
        "target_path": target_path,
        "category": record.category,
        "subcategory": record.subcategory,
        "pack": record.pack,
        "confidence": float(record.confidence) if record.confidence else 0.0,
        "hash": record_hash,
        "status": result,
        "tags": json.dumps(tags),
        "step_status": execution_step_status(result),
        "original_action": record_action,
        "trash_path": duplicate_trash_path,
        "preserved_root": getattr(record, "preserved_root", None),
        "is_preserved": bool(getattr(record, "is_preserved", False)),
    }
