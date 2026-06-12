import logging
import sqlite3
import threading
from pathlib import Path
from typing import Set, Tuple


def create_connection(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=30.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    fk_state = conn.execute("PRAGMA foreign_keys").fetchone()
    if not fk_state or int(fk_state[0]) != 1:
        logging.warning("SQLite foreign key enforcement is not active for %s", db_path)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
    except sqlite3.DatabaseError as exc:
        logging.debug("Could not enable WAL mode for %s: %s", db_path, exc)
    try:
        conn.execute("PRAGMA busy_timeout = 5000")
    except sqlite3.DatabaseError as exc:
        logging.debug("Could not set busy_timeout for %s: %s", db_path, exc)
    return conn


def foreign_key_violations(conn: sqlite3.Connection, db_path: Path) -> list[Tuple[str, int, str, int]]:
    try:
        rows = conn.execute("PRAGMA foreign_key_check").fetchall()
        return [(str(r[0]), int(r[1]), str(r[2]), int(r[3])) for r in rows]
    except sqlite3.DatabaseError as exc:
        logging.warning("Could not run foreign key check for %s: %s", db_path, exc)
        return []


def close_connections(
    connections: Set[sqlite3.Connection],
    connection_lock: threading.RLock,
    db_path: Path,
) -> tuple[list[sqlite3.Connection], threading.local]:
    with connection_lock:
        pending = list(connections)
        connections.clear()
        thread_state = threading.local()
    for conn in pending:
        try:
            conn.close()
        except Exception:
            logging.debug("Failed to close SQLite connection for %s", db_path, exc_info=True)
    return pending, thread_state
