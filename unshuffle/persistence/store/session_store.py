import sqlite3
import os
from pathlib import Path
from typing import Any, Optional


def _normalized_path_value(path: Path | str | None) -> str:
    if path is None:
        return ""
    try:
        value = str(Path(path).resolve())
    except OSError:
        value = str(Path(path))
    return os.path.normcase(os.path.normpath(value))


def register_session(conn: sqlite3.Connection, session_id: str, source: Path, target: Path, mode: str, is_flat: bool) -> None:
    source_value = _normalized_path_value(source)
    target_value = _normalized_path_value(target)
    conn.execute(
        """
        INSERT INTO sessions (session_id, source_path, target_root, mode, is_flat)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(session_id) DO UPDATE SET
            source_path=excluded.source_path,
            target_root=excluded.target_root,
            mode=excluded.mode,
            is_flat=excluded.is_flat,
            timestamp=CURRENT_TIMESTAMP
        """,
        (session_id, source_value, target_value, mode, is_flat),
    )


def set_session_sources(conn: sqlite3.Connection, session_id: str, sources: list[Path]) -> None:
    conn.execute("DELETE FROM session_sources WHERE session_id = ?", (session_id,))
    conn.executemany(
        "INSERT INTO session_sources (session_id, source_path, ordinal) VALUES (?, ?, ?)",
        [(session_id, str(source), ordinal) for ordinal, source in enumerate(sources)],
    )


def set_session_metadata(conn: sqlite3.Connection, session_id: str, key: str, value_json: str) -> None:
    conn.execute(
        """
        INSERT INTO session_metadata (session_id, key, value_json)
        VALUES (?, ?, ?)
        ON CONFLICT(session_id, key) DO UPDATE SET value_json = excluded.value_json
        """,
        (session_id, key, value_json),
    )


def get_session_metadata(conn: sqlite3.Connection, session_id: str, key: str) -> str | None:
    cursor = conn.execute(
        "SELECT value_json FROM session_metadata WHERE session_id = ? AND key = ?",
        (session_id, key),
    )
    row = cursor.fetchone()
    return str(row["value_json"]) if row else None


def remove_session_source(conn: sqlite3.Connection, session_id: str, source_path: str) -> None:
    conn.execute(
        "DELETE FROM session_sources WHERE session_id = ? AND source_path = ?",
        (session_id, source_path),
    )


def get_session(conn: sqlite3.Connection, session_id: str) -> Optional[dict[str, Any]]:
    cursor = conn.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,))
    row = cursor.fetchone()
    return dict(row) if row else None


def get_session_sources(conn: sqlite3.Connection, session_id: str) -> list[str]:
    cursor = conn.execute(
        "SELECT source_path FROM session_sources WHERE session_id = ? ORDER BY ordinal ASC",
        (session_id,),
    )
    sources = [row["source_path"] for row in cursor.fetchall()]
    if sources:
        return sources
    session = get_session(conn, session_id)
    if session and session.get("source_path"):
        return [session["source_path"]]
    return []


def add_records_bulk(conn: sqlite3.Connection, session_id: str, records_list: list[dict[str, Any]]) -> None:
    conn.executemany(
        """
        INSERT INTO records (session_id, source_path, target_path, category, subcategory, pack, file_hash, confidence, status, tags, step_status, original_action, trash_path, preserved_root, is_preserved)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                session_id,
                Path(record["source_path"]).as_posix(),
                Path(record["target_path"]).as_posix(),
                record["category"],
                record.get("subcategory"),
                record["pack"],
                record.get("hash", record.get("file_hash")),
                record.get("confidence", 0.0),
                record.get("status"),
                record.get("tags"),
                record.get("step_status", "PENDING"),
                record.get("original_action"),
                Path(record["trash_path"]).as_posix() if record.get("trash_path") else None,
                Path(record["preserved_root"]).as_posix() if record.get("preserved_root") else None,
                1 if record.get("is_preserved") else 0,
            )
            for record in records_list
        ],
    )


def get_session_records(conn: sqlite3.Connection, session_id: str) -> list[dict]:
    cursor = conn.execute("SELECT * FROM records WHERE session_id = ?", (session_id,))
    return [dict(row) for row in cursor.fetchall()]


def get_recent_sessions(conn: sqlite3.Connection, limit: int, only_executed: bool, target_root: Path | str | None = None) -> list[dict]:
    where_clause = "WHERE r.file_count > 0" if only_executed else ""
    cursor = conn.execute(
        f"""
        SELECT
            s.*,
            COALESCE(r.file_count, 0) AS file_count,
            COALESCE(r.undoable_count, 0) AS undoable_count,
            COALESCE(r.undone_count, 0) AS undone_count,
            CASE
                WHEN COALESCE(r.undoable_count, 0) > 0 THEN 'undoable'
                WHEN COALESCE(r.undone_count, 0) > 0 THEN 'undone'
                ELSE 'pending'
            END AS history_state,
            COALESCE(ss.source_count, CASE WHEN s.source_path IS NOT NULL THEN 1 ELSE 0 END) AS source_count
        FROM sessions s
        LEFT JOIN (
            SELECT
                session_id,
                COUNT(*) AS file_count,
                SUM(CASE WHEN step_status IS NULL OR step_status = 'COMMITTED' THEN 1 ELSE 0 END) AS undoable_count,
                SUM(CASE WHEN step_status = 'UNDONE' THEN 1 ELSE 0 END) AS undone_count
            FROM records
            WHERE status IN ('copied', 'duplicate')
              AND (step_status IS NULL OR step_status IN ('COMMITTED', 'UNDONE'))
            GROUP BY session_id
        ) r ON s.session_id = r.session_id
        LEFT JOIN (
            SELECT session_id, COUNT(*) AS source_count
            FROM session_sources
            GROUP BY session_id
        ) ss ON s.session_id = ss.session_id
        {where_clause}
        ORDER BY s.timestamp DESC
        """,
    )
    sessions = [dict(row) for row in cursor.fetchall()]
    if target_root is not None:
        target_value = _normalized_path_value(target_root)
        sessions = [
            session for session in sessions
            if _normalized_path_value(session.get("target_root")) == target_value
        ]
    return sessions[:limit]


def mark_session_undone(conn: sqlite3.Connection, session_id: str) -> None:
    conn.execute(
        """
        UPDATE records
        SET step_status = 'UNDONE'
        WHERE session_id = ?
          AND status IN ('copied', 'duplicate')
          AND (step_status IS NULL OR step_status = 'COMMITTED')
        """,
        (session_id,),
    )


def delete_session(conn: sqlite3.Connection, session_id: str) -> None:
    conn.execute("DELETE FROM session_sources WHERE session_id = ?", (session_id,))
    conn.execute("DELETE FROM session_metadata WHERE session_id = ?", (session_id,))
    conn.execute("DELETE FROM records WHERE session_id = ?", (session_id,))
    conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))


def clear_all_history(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM records")
    conn.execute(
        """
        DELETE FROM session_sources
        WHERE session_id NOT IN (
            SELECT DISTINCT session_id FROM staging_records WHERE session_id IS NOT NULL
        )
        """
    )
    conn.execute(
        """
        DELETE FROM sessions
        WHERE session_id NOT IN (
            SELECT DISTINCT session_id FROM staging_records WHERE session_id IS NOT NULL
        )
        """
    )


def clear_history_for_target(conn: sqlite3.Connection, target_root: Path | str) -> None:
    target_value = _normalized_path_value(target_root)
    cursor = conn.execute("SELECT session_id, target_root FROM sessions")
    session_ids = [
        row["session_id"]
        for row in cursor.fetchall()
        if _normalized_path_value(row["target_root"]) == target_value
    ]
    if not session_ids:
        return

    conn.executemany("DELETE FROM records WHERE session_id = ?", [(session_id,) for session_id in session_ids])

    staging_cursor = conn.execute(
        "SELECT DISTINCT session_id FROM staging_records WHERE session_id IS NOT NULL"
    )
    staging_sessions = {row["session_id"] for row in staging_cursor.fetchall()}
    removable_session_ids = [session_id for session_id in session_ids if session_id not in staging_sessions]
    if not removable_session_ids:
        return

    conn.executemany("DELETE FROM session_sources WHERE session_id = ?", [(session_id,) for session_id in removable_session_ids])
    conn.executemany("DELETE FROM sessions WHERE session_id = ?", [(session_id,) for session_id in removable_session_ids])
