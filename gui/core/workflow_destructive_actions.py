from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from unshuffle.core.path_safety import is_path_within_directory


@dataclass
class PhysicalDeleteResult:
    deleted_paths: list[Path]
    failed_paths: list[tuple[Path, str]]


def active_source_roots(engine) -> list[Path]:
    roots = []
    for root in getattr(engine, "session_source_roots", []) or []:
        try:
            roots.append(Path(root).resolve())
        except (TypeError, OSError):
            continue
    root = getattr(engine, "session_source_root", None)
    if root:
        try:
            resolved = Path(root).resolve()
        except (TypeError, OSError):
            resolved = None
        if resolved is not None and resolved not in roots:
            roots.append(resolved)
    return roots


def physically_delete_records(records: Iterable, source_roots: list[Path]) -> PhysicalDeleteResult:
    deleted_paths = []
    failed_paths = []
    for rec in records:
        path = Path(rec.source_path)
        try:
            if source_roots and not any(is_path_within_directory(path, root) for root in source_roots):
                failed_paths.append((path, "outside active source roots"))
                continue
            if path.exists():
                path.unlink()
            deleted_paths.append(path)
        except Exception as exc:
            logging.error(f"Failed to delete {path}: {exc}")
            failed_paths.append((path, str(exc)))
    return PhysicalDeleteResult(deleted_paths=deleted_paths, failed_paths=failed_paths)


def remove_deleted_staging_rows(database, session_id: str, deleted_paths: Iterable[Path]) -> None:
    if not database:
        return
    for path in deleted_paths:
        database.remove_staging_by_source(session_id, path.as_posix())


def physical_delete_status_message(result: PhysicalDeleteResult) -> str:
    msg_parts = []
    if result.deleted_paths:
        msg_parts.append(f"Permanently deleted {len(result.deleted_paths)} file(s) from disk and workbench.")
    if result.failed_paths:
        msg_parts.append(f"Failed to delete {len(result.failed_paths)} file(s).")
    return "   ".join(msg_parts)


def physical_delete_error_details(failed_paths: list[tuple[Path, str]], *, limit: int = 10) -> str:
    details = "\n".join(f"- {path.name}: {error}" for path, error in failed_paths[:limit])
    if len(failed_paths) > limit:
        details += f"\n... and {len(failed_paths) - limit} more"
    return details
