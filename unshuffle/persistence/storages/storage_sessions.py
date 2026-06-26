from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from unshuffle.persistence.stores import staging_store, session_store


def register_session(db, session_id: str, source: Path, target: Path, mode: str, is_flat: bool = False) -> None:
    with db._write_transaction():
        session_store.register_session(db.conn, session_id, source, target, mode, is_flat)


def set_session_sources(db, session_id: str, sources: List[Path]) -> None:
    with db._write_transaction():
        session_store.set_session_sources(db.conn, session_id, sources)


def set_session_metadata(db, session_id: str, key: str, value_json: str) -> None:
    with db._write_transaction():
        session_store.set_session_metadata(db.conn, session_id, key, value_json)


def get_session_metadata(db, session_id: str, key: str) -> str | None:
    return session_store.get_session_metadata(db.conn, session_id, key)


def remove_session_source(db, session_id: str, source_path: str) -> None:
    with db._write_transaction():
        session_store.remove_session_source(db.conn, session_id, source_path)


def get_session_sources(db, session_id: str) -> List[str]:
    return session_store.get_session_sources(db.conn, session_id)


def add_records_bulk(db, session_id: str, records_list: List[Dict[str, Any]]) -> None:
    if not records_list:
        return
    with db._write_transaction():
        session_store.add_records_bulk(db.conn, session_id, records_list)


def get_session_records(db, session_id: str) -> List[Dict]:
    return session_store.get_session_records(db.conn, session_id)


def get_recent_sessions(
    db,
    limit: int = 10,
    only_executed: bool = False,
    target_root: Path | str | None = None,
) -> List[Dict]:
    return session_store.get_recent_sessions(db.conn, limit, only_executed, target_root)


def get_session(db, session_id: str) -> Optional[Dict[str, Any]]:
    return session_store.get_session(db.conn, session_id)


def mark_session_undone(db, session_id: str) -> None:
    with db._write_transaction():
        session_store.mark_session_undone(db.conn, session_id)


def delete_session(db, session_id: str) -> None:
    with db._write_transaction():
        session_store.delete_session(db.conn, session_id)


def clear_staging(db, session_id: Optional[str] = None) -> None:
    with db._write_transaction():
        staging_store.clear_staging(db.conn, session_id)


def remove_staging_by_source(db, session_id: str, source_path: str) -> None:
    with db._write_transaction():
        staging_store.remove_staging_by_source(db.conn, session_id, source_path)


def add_staging_records_bulk(db, session_id: str, records: List[Tuple]) -> None:
    if not records:
        return
    with db._write_transaction():
        staging_store.add_staging_records_bulk(db.conn, session_id, records)


def get_staging_records(db, session_id: str) -> List[Dict]:
    return staging_store.get_staging_records(db.conn, session_id)


def update_staging_record(db, session_id: str, row_id: int, data: Dict[str, str]) -> None:
    with db._write_transaction():
        staging_store.update_staging_record(db.conn, session_id, row_id, data)


def clear_all_history(db) -> None:
    with db._write_transaction():
        session_store.clear_all_history(db.conn)


def clear_history_for_target(db, target_root: Path | str) -> None:
    with db._write_transaction():
        session_store.clear_history_for_target(db.conn, target_root)
