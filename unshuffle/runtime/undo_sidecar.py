"""Disposable target sidecar helpers for undo cleanup."""

import os
from pathlib import Path
from typing import Any

from ..core.paths import DB_FILE_NAME, SYSTEM_FOLDER_NAME


def target_local_db_is_disposable(database: Any) -> bool:
    conn = getattr(database, "conn", None)
    if conn is None:
        return False

    state_tables = {
        "file_cache",
        "sessions",
        "session_sources",
        "records",
        "token_adjustments",
        "aliases",
        "config_lists",
        "exclusions",
        "suppression_rules",
        "sub_taxonomy",
        "staging_records",
        "coherence_results",
        "coherence_review_decisions",
        "refinement_candidates",
        "anchor_profiles",
    }
    existing = {str(row["name"]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()}
    for table in sorted(state_tables & existing):
        try:
            if table == "anchor_profiles":
                query = "SELECT COUNT(*) FROM anchor_profiles WHERE session_id != '__system__'"
            else:
                query = f'SELECT COUNT(*) FROM "{table}"'
            if int(conn.execute(query).fetchone()[0] or 0) > 0:
                return False
        except Exception:
            return False
    return True


def remove_disposable_target_sidecar(target_dir: Path) -> bool:
    sidecar = target_dir / SYSTEM_FOLDER_NAME
    if not sidecar.exists() or not sidecar.is_dir():
        return False

    allowed_files = {
        DB_FILE_NAME,
        f"{DB_FILE_NAME}-wal",
        f"{DB_FILE_NAME}-shm",
        f"{DB_FILE_NAME}-journal",
        "lock.json",
        "lock.exclusive",
    }
    removable_files: list[Path] = []
    removable_dirs: list[Path] = []
    try:
        for path in sorted(sidecar.rglob("*"), key=lambda item: len(item.parts), reverse=True):
            rel = path.relative_to(sidecar)
            if path.is_dir():
                if rel.parts and rel.parts[0] in {"lock", "trash"}:
                    removable_dirs.append(path)
                    continue
                return False
            if path.name in allowed_files or path.name.startswith("lock.json.") and path.name.endswith(".tmp"):
                removable_files.append(path)
                continue
            return False

        for path in removable_files:
            try:
                os.remove(os.fspath(path))
            except FileNotFoundError:
                pass
        for path in removable_dirs:
            try:
                path.rmdir()
            except OSError:
                return False
        sidecar.rmdir()
        return True
    except OSError:
        return False
