import logging
import os
import sqlite3
from pathlib import Path
from typing import Any


EPHEMERAL_TABLES = (
    "staging_records",
    "staging_fts",
    "coherence_results",
    "refinement_candidates",
)
REMOVED_VERIFIED_ANCHOR_SESSION = "__removed_verified_anchors__"


def _normalized_path_value(path: Path | str | None) -> str:
    if path is None:
        return ""
    try:
        value = str(Path(path).resolve())
    except OSError:
        value = str(Path(path))
    return os.path.normcase(os.path.normpath(value))


def _placeholders(values: set[str]) -> str:
    return ", ".join("?" for _ in values)


def _all_session_ids(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT session_id FROM sessions WHERE session_id IS NOT NULL").fetchall()
    return {str(row["session_id"]) for row in rows if str(row["session_id"] or "").strip()}


def _session_ids_for_target(conn: sqlite3.Connection, target_root: Path | str | None) -> set[str]:
    if target_root is None:
        return _all_session_ids(conn)
    target_value = _normalized_path_value(target_root)
    rows = conn.execute("SELECT session_id, target_root FROM sessions WHERE session_id IS NOT NULL").fetchall()
    return {
        str(row["session_id"])
        for row in rows
        if str(row["session_id"] or "").strip()
        and _normalized_path_value(row["target_root"]) == target_value
    }


def _ephemeral_session_ids(conn: sqlite3.Connection) -> set[str]:
    session_ids: set[str] = set()
    for table in EPHEMERAL_TABLES:
        rows = conn.execute(f"SELECT DISTINCT session_id FROM {table} WHERE session_id IS NOT NULL").fetchall()
        session_ids.update(str(row["session_id"]) for row in rows if str(row["session_id"] or "").strip())
    rows = conn.execute(
        """
        SELECT DISTINCT session_id
        FROM anchor_profiles
        WHERE session_id IS NOT NULL
          AND state NOT IN ('verified', 'system')
          AND session_id NOT IN ('__system__', ?)
        """,
        (REMOVED_VERIFIED_ANCHOR_SESSION,),
    ).fetchall()
    session_ids.update(str(row["session_id"]) for row in rows if str(row["session_id"] or "").strip())
    return session_ids


def newest_restorable_staging_session(conn: sqlite3.Connection, target_root: Path | str | None = None) -> str:
    target_value = _normalized_path_value(target_root) if target_root is not None else None
    rows = conn.execute(
        """
        SELECT s.session_id, s.target_root
        FROM sessions s
        WHERE s.session_id IS NOT NULL
          AND EXISTS (
              SELECT 1 FROM staging_records sr WHERE sr.session_id = s.session_id
          )
        ORDER BY s.timestamp DESC
        """
    ).fetchall()
    for row in rows:
        session_id = str(row["session_id"] or "").strip()
        if not session_id:
            continue
        if target_value is None or _normalized_path_value(row["target_root"]) == target_value:
            return session_id
    return ""


def _count_for_sessions(conn: sqlite3.Connection, table: str, session_ids: set[str], extra_where: str = "") -> int:
    if not session_ids:
        return 0
    placeholders = _placeholders(session_ids)
    row = conn.execute(
        f"SELECT COUNT(*) FROM {table} WHERE session_id IN ({placeholders}) {extra_where}",
        tuple(session_ids),
    ).fetchone()
    return int(row[0] or 0)


def _delete_for_sessions(conn: sqlite3.Connection, table: str, session_ids: set[str], extra_where: str = "") -> None:
    if not session_ids:
        return
    placeholders = _placeholders(session_ids)
    conn.execute(
        f"DELETE FROM {table} WHERE session_id IN ({placeholders}) {extra_where}",
        tuple(session_ids),
    )


def prune_ephemeral_state(
    conn: sqlite3.Connection,
    keep_session_ids: set[str] | list[str] | tuple[str, ...] | None = None,
    target_root: Path | str | None = None,
    *,
    use_restorable_fallback: bool = True,
) -> dict[str, Any]:
    keep = {str(item).strip() for item in (keep_session_ids or set()) if str(item or "").strip()}
    if not keep and target_root is not None and use_restorable_fallback:
        fallback = newest_restorable_staging_session(conn, target_root)
        if fallback:
            keep.add(fallback)

    known_sessions = _all_session_ids(conn)
    scoped_sessions = _session_ids_for_target(conn, target_root)
    ephemeral_sessions = _ephemeral_session_ids(conn)
    orphan_sessions = ephemeral_sessions - known_sessions
    prune_sessions = ((ephemeral_sessions & scoped_sessions) | orphan_sessions) - keep

    stats: dict[str, Any] = {
        "kept_sessions": sorted(keep),
        "pruned_sessions": sorted(prune_sessions),
        "deleted": {},
        "pending_sessions_deleted": 0,
    }
    if not prune_sessions:
        return stats

    for table in EPHEMERAL_TABLES:
        stats["deleted"][table] = _count_for_sessions(conn, table, prune_sessions)
    stats["deleted"]["anchor_profiles"] = _count_for_sessions(
        conn,
        "anchor_profiles",
        prune_sessions,
        f"AND state NOT IN ('verified', 'system') AND session_id NOT IN ('__system__', '{REMOVED_VERIFIED_ANCHOR_SESSION}')",
    )

    for table in EPHEMERAL_TABLES:
        _delete_for_sessions(conn, table, prune_sessions)
    _delete_for_sessions(
        conn,
        "anchor_profiles",
        prune_sessions,
        f"AND state NOT IN ('verified', 'system') AND session_id NOT IN ('__system__', '{REMOVED_VERIFIED_ANCHOR_SESSION}')",
    )

    session_delete_rows = conn.execute(
        f"""
        SELECT s.session_id
        FROM sessions s
        WHERE s.session_id IN ({_placeholders(prune_sessions)})
          AND NOT EXISTS (SELECT 1 FROM records r WHERE r.session_id = s.session_id)
        """,
        tuple(prune_sessions),
    ).fetchall()
    deletable_sessions = {str(row["session_id"]) for row in session_delete_rows if str(row["session_id"] or "").strip()}
    if deletable_sessions:
        conn.executemany("DELETE FROM session_sources WHERE session_id = ?", [(session_id,) for session_id in deletable_sessions])
        conn.executemany("DELETE FROM sessions WHERE session_id = ?", [(session_id,) for session_id in deletable_sessions])
    stats["pending_sessions_deleted"] = len(deletable_sessions)
    return stats


def database_size_stats(conn: sqlite3.Connection) -> dict[str, int]:
    page_size = int(conn.execute("PRAGMA page_size").fetchone()[0] or 0)
    page_count = int(conn.execute("PRAGMA page_count").fetchone()[0] or 0)
    freelist_count = int(conn.execute("PRAGMA freelist_count").fetchone()[0] or 0)
    return {
        "page_size": page_size,
        "page_count": page_count,
        "freelist_count": freelist_count,
        "database_bytes": page_size * page_count,
        "reclaimable_bytes": page_size * freelist_count,
    }


def compact_if_worthwhile(
    conn: sqlite3.Connection,
    *,
    min_reclaim_mb: int = 512,
    min_reclaim_ratio: float = 0.25,
) -> dict[str, Any]:
    before = database_size_stats(conn)
    database_bytes = int(before.get("database_bytes") or 0)
    reclaimable_bytes = int(before.get("reclaimable_bytes") or 0)
    threshold_bytes = int(min_reclaim_mb) * 1024 * 1024
    reclaim_ratio = (reclaimable_bytes / database_bytes) if database_bytes else 0.0
    result: dict[str, Any] = {
        "ran": False,
        "skipped": False,
        "reason": "",
        "before": before,
        "after": before,
    }
    if reclaimable_bytes < threshold_bytes or reclaim_ratio < float(min_reclaim_ratio):
        result["skipped"] = True
        result["reason"] = "below_threshold"
        return result

    try:
        conn.execute("VACUUM")
    except sqlite3.OperationalError as exc:
        if "locked" in str(exc).lower() or "busy" in str(exc).lower():
            result["skipped"] = True
            result["reason"] = "database_busy"
            return result
        raise
    except sqlite3.DatabaseError as exc:
        logging.debug("Database compaction skipped: %s", exc)
        result["skipped"] = True
        result["reason"] = "database_error"
        return result

    result["ran"] = True
    result["after"] = database_size_stats(conn)
    return result
