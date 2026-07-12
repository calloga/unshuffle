from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from unshuffle.persistence.stores import scan_store


def create_scan_run(
    db,
    *,
    scan_id: str,
    session_id: str,
    target_root: Path | str,
    roots: Sequence[Path | str],
    mode: str = "new",
    versions: Mapping[str, str | None] | None = None,
) -> None:
    with db._write_transaction():
        scan_store.create_scan_run(
            db.conn,
            scan_id=scan_id,
            session_id=session_id,
            target_root=target_root,
            roots=roots,
            mode=mode,
            versions=versions,
        )


def get_scan_run(db, scan_id: str):
    return scan_store.get_scan_run(db.conn, scan_id)


def newest_resumable_scan(db, target_root: Path | str):
    return scan_store.newest_resumable_scan(db.conn, target_root)


def update_scan_run(db, scan_id: str, **values) -> None:
    with db._write_transaction():
        scan_store.update_scan_run(db.conn, scan_id, **values)


def update_session_scan_runs(db, session_id: str, *, state: str, phase: str | None = None) -> int:
    with db._write_transaction():
        return scan_store.update_session_scan_runs(db.conn, session_id, state=state, phase=phase)


def insert_scan_directories(db, scan_id: str, rows: Iterable[Sequence[Any]], batch_size: int = 2000) -> int:
    with db._write_transaction():
        return scan_store.insert_directories(db.conn, scan_id, rows, batch_size=batch_size)


def insert_scan_items(db, scan_id: str, rows: Iterable[Sequence[Any]], batch_size: int = 2000) -> int:
    with db._write_transaction():
        return scan_store.insert_items(db.conn, scan_id, rows, batch_size=batch_size)


def count_scan_items(db, scan_id: str, phase: str | None = None, state: str | None = None) -> int:
    return scan_store.count_items(db.conn, scan_id, phase=phase, state=state)


def claim_scan_items(db, scan_id: str, phase: str, owner: str, **options):
    with db._write_transaction():
        return scan_store.claim_items(db.conn, scan_id, phase, owner, **options)


def reset_stale_scan_claims(db, scan_id: str, phase: str, stale_after_seconds: int = 300) -> int:
    with db._write_transaction():
        return scan_store.reset_stale_claims(
            db.conn,
            scan_id,
            phase,
            stale_after_seconds=stale_after_seconds,
        )


def update_scan_items(db, scan_id: str, updates: Iterable[tuple[int, Mapping[str, Any]]]) -> int:
    with db._write_transaction():
        return scan_store.update_items(db.conn, scan_id, updates)


def update_scan_item_hashes_by_path(db, scan_id: str, rows, batch_size: int = 1000) -> int:
    with db._write_transaction():
        return scan_store.update_item_hashes_by_path(
            db.conn,
            scan_id,
            rows,
            batch_size=batch_size,
        )


def update_scan_item_hashes(db, scan_id: str, rows, batch_size: int = 1000) -> int:
    with db._write_transaction():
        return scan_store.update_item_hashes(db.conn, scan_id, rows, batch_size=batch_size)


def iter_fast_hash_collision_items(db, scan_id: str, batch_size: int = 1000):
    return scan_store.iter_fast_hash_collision_items(db.conn, scan_id, batch_size=batch_size)


def count_fast_hash_collision_items(db, scan_id: str) -> int:
    return scan_store.count_fast_hash_collision_items(db.conn, scan_id)


def iter_session_fast_hash_collision_items(db, session_id: str, batch_size: int = 1000):
    return scan_store.iter_session_fast_hash_collision_items(db.conn, session_id, batch_size=batch_size)


def count_session_fast_hash_collision_items(db, session_id: str) -> int:
    return scan_store.count_session_fast_hash_collision_items(db.conn, session_id)


def update_session_item_hashes(db, rows, batch_size: int = 1000) -> int:
    with db._write_transaction():
        return scan_store.update_session_item_hashes(db.conn, rows, batch_size=batch_size)


def iter_append_fast_hash_collision_items(db, session_id: str, scan_id: str, batch_size: int = 1000):
    return scan_store.iter_append_fast_hash_collision_items(
        db.conn,
        session_id,
        scan_id,
        batch_size=batch_size,
    )


def update_append_promoted_hashes(db, session_id: str, scan_id: str, rows) -> int:
    with db._write_transaction():
        return scan_store.update_append_promoted_hashes(db.conn, session_id, scan_id, rows)


def finalize_fast_hashes(db, scan_id: str) -> int:
    with db._write_transaction():
        return scan_store.finalize_fast_hashes(db.conn, scan_id)


def update_scan_item_classifications_by_path(db, scan_id: str, rows, batch_size: int = 1000) -> int:
    with db._write_transaction():
        return scan_store.update_item_classifications_by_path(
            db.conn,
            scan_id,
            rows,
            batch_size=batch_size,
        )


def iter_classified_scan_session_items(db, session_id: str, batch_size: int = 1000):
    return scan_store.iter_classified_session_items(db.conn, session_id, batch_size=batch_size)


def iter_classified_append_items(db, session_id: str, scan_ids, batch_size: int = 1000):
    return scan_store.iter_classified_append_items(
        db.conn,
        session_id,
        scan_ids,
        batch_size=batch_size,
    )


def iter_canonical_scan_audio_items(db, scan_id: str, batch_size: int = 2000):
    return scan_store.iter_canonical_audio_items(db.conn, scan_id, batch_size=batch_size)


def iter_scan_classification_items(db, scan_id: str, batch_size: int = 1000):
    return scan_store.iter_classification_items(db.conn, scan_id, batch_size=batch_size)


def update_scan_analysis_by_hash(db, scan_id: str, rows, batch_size: int = 1000) -> int:
    with db._write_transaction():
        return scan_store.update_analysis_by_hash(db.conn, scan_id, rows, batch_size=batch_size)


def classified_scan_session_stats(db, session_id: str):
    return scan_store.classified_session_stats(db.conn, session_id)


def exclude_classified_scan_session_paths(db, session_id: str, roots) -> int:
    with db._write_transaction():
        return scan_store.exclude_classified_session_paths(db.conn, session_id, roots)


def iter_scan_items(db, scan_id: str, **options):
    return scan_store.iter_items(db.conn, scan_id, **options)


def iter_scan_directories(db, scan_id: str, **options):
    return scan_store.iter_directories(db.conn, scan_id, **options)


def iter_discovered_scan_nodes(db, scan_id: str, batch_size: int = 1000):
    return scan_store.iter_discovered_nodes(db.conn, scan_id, batch_size=batch_size)


def fast_hash_collision_groups(db, scan_id: str):
    return scan_store.fast_hash_collision_groups(db.conn, scan_id)


def delete_scan_run(db, scan_id: str) -> None:
    with db._write_transaction():
        scan_store.delete_scan_run(db.conn, scan_id)
