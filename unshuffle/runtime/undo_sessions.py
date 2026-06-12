"""Undo session database finalization helpers."""

from pathlib import Path
from typing import Any


def undo_result_sources(database: Any, session_id: str, session: dict | None) -> list:
    sources = []
    if hasattr(database, "get_session_sources"):
        sources = database.get_session_sources(session_id)
    if not sources and session and session.get("source_path"):
        sources = [session["source_path"]]
    return sources


def _mark_session_undone(database: Any, session_id: str) -> None:
    if database is None:
        return
    mark_undone = getattr(database, "mark_session_undone", None)
    if callable(mark_undone):
        mark_undone(session_id)
        return
    conn = getattr(database, "conn", None)
    if conn is not None:
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


def _db_has_session_records(database: Any, session_id: str) -> bool:
    if database is None:
        return False
    try:
        records = database.get_session_records(session_id)
    except Exception:
        return False
    return bool(records)


def mirror_undo_session_to_global(global_db: Any, source_db: Any, session_id: str, session: dict | None, records: list) -> None:
    if global_db is None or source_db is None or global_db is source_db or _db_has_session_records(global_db, session_id):
        return
    if not session or not records:
        return
    try:
        global_db.register_session(
            session_id=session_id,
            source=Path(session.get("source_path") or "."),
            target=Path(session.get("target_root") or "."),
            mode=session.get("mode") or "copy",
            is_flat=bool(session.get("is_flat")),
        )
        sources = []
        if hasattr(source_db, "get_session_sources"):
            sources = source_db.get_session_sources(session_id)
        if sources and hasattr(global_db, "set_session_sources"):
            global_db.set_session_sources(session_id, [Path(source) for source in sources if source])
        global_db.add_records_bulk(session_id, records)
    except Exception:
        return


def finalize_global_undo_session(database: Any, session_id: str) -> None:
    _mark_session_undone(database, session_id)


def delete_local_undo_session(database: Any, session_id: str) -> None:
    if database is None:
        return
    try:
        database.clear_staging(session_id)
    except Exception:
        pass
    try:
        database.delete_session(session_id)
    except Exception:
        pass
