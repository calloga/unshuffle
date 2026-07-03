from __future__ import annotations

from pathlib import Path

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
