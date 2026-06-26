from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

from unshuffle.persistence.stores import maintenance_store


def prune_ephemeral_state(
    db,
    keep_session_ids: Set[str] | List[str] | Tuple[str, ...] | None = None,
    target_root: Path | str | None = None,
    *,
    use_restorable_fallback: bool = True,
) -> Dict[str, Any]:
    with db._write_transaction():
        return maintenance_store.prune_ephemeral_state(
            db.conn,
            keep_session_ids,
            target_root,
            use_restorable_fallback=use_restorable_fallback,
        )


def newest_restorable_staging_session(db, target_root: Path | str | None = None) -> str:
    return maintenance_store.newest_restorable_staging_session(db.conn, target_root)


def database_size_stats(db) -> Dict[str, int]:
    return maintenance_store.database_size_stats(db.conn)


def compact_if_worthwhile(
    db,
    *,
    min_reclaim_mb: int = 512,
    min_reclaim_ratio: float = 0.25,
) -> Dict[str, Any]:
    with db._write_lock:
        db.conn.commit()
        return maintenance_store.compact_if_worthwhile(
            db.conn,
            min_reclaim_mb=min_reclaim_mb,
            min_reclaim_ratio=min_reclaim_ratio,
        )


def force_compact(db) -> Dict[str, Any]:
    with db._write_lock:
        before = maintenance_store.database_size_stats(db.conn)
        db.conn.commit()
        db.conn.execute("VACUUM")
        after = maintenance_store.database_size_stats(db.conn)
    return {"ran": True, "before": before, "after": after}
