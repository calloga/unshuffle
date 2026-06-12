from __future__ import annotations

from pathlib import Path

from .workflow_records import record_dedupe_key


def known_duplicate_hashes_for_scan(current_records=None, append: bool = False) -> set:
    hashes = set()
    if append and current_records:
        hashes.update(rec.hash for rec in current_records if getattr(rec, "hash", None))
    return hashes


def existing_dedupe_keys(current_records=None, append: bool = False) -> set:
    if not append or not current_records:
        return set()
    return {record_dedupe_key(record) for record in current_records}


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
