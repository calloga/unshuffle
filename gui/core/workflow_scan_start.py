from __future__ import annotations

from pathlib import Path
import json
import logging

from .workflow_records import build_dedupe_index


def known_duplicate_hashes_for_scan(current_records=None, append: bool = False) -> set:
    hashes = set()
    if append and current_records:
        for rec in current_records:
            for value in (getattr(rec, "hash", None), getattr(rec, "fast_hash", None)):
                if value:
                    hashes.add(value)
    return hashes


def existing_dedupe_keys(current_records=None, append: bool = False) -> dict:
    if not append or not current_records:
        return build_dedupe_index()
    return build_dedupe_index(current_records)


def compatible_resumable_session_id(target: Path, roots: list[Path]) -> str:
    from unshuffle.core.features import CURRENT_FEATURE_SPACE_VERSION
    from unshuffle.persistence import get_db

    db = get_db(Path(target))
    try:
        run = db.newest_resumable_scan(target) if hasattr(db, "newest_resumable_scan") else None
        if not run:
            return ""
        session_id = str(run.get("session_id") or "")
        if not session_id:
            return ""
        rows = db.conn.execute(
            "SELECT roots_json, hash_version, feature_version, taxonomy_version, classification_version "
            "FROM scan_runs WHERE session_id = ? AND state IN ('running', 'paused', 'failed', 'staged')",
            (session_id,),
        ).fetchall()
        persisted_roots = set()
        for row in rows:
            try:
                persisted_roots.update(Path(value).resolve().as_posix().lower() for value in json.loads(row[0] or "[]"))
            except (TypeError, json.JSONDecodeError, OSError):
                return ""
            if row[1] not in (None, "segmd5-v1"):
                return ""
            if row[2] not in (None, CURRENT_FEATURE_SPACE_VERSION):
                return ""
            if row[3] not in (None, "current") or row[4] not in (None, "current"):
                return ""
        requested_roots = {Path(root).resolve().as_posix().lower() for root in roots}
        if persisted_roots != requested_roots:
            logging.info(
                "Resumable scan ignored: selected roots changed (%d persisted, %d requested).",
                len(persisted_roots),
                len(requested_roots),
            )
            return ""
        logging.info("Resuming compatible scan session %s.", session_id)
        return session_id
    finally:
        db.close()


def detach_source_root(engine, root: Path) -> list[Path]:
    if not engine or not getattr(engine, "db", None):
        return []

    sid = engine.session_id
    resolved_root = Path(root).resolve()
    root_str = str(resolved_root)

    engine.db.remove_session_source(sid, root_str)
    engine.db.remove_staging_by_source(sid, root_str)

    remaining_roots = []
    for candidate in engine.session_source_roots:
        try:
            resolved = Path(candidate).resolve()
        except OSError:
            resolved = Path(candidate)
        if resolved == resolved_root:
            continue
        remaining_roots.append(resolved)

    engine.session_source_roots = remaining_roots
    engine.session_source_root = remaining_roots[0] if remaining_roots else None
    return remaining_roots
