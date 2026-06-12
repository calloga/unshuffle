from __future__ import annotations

import logging
import sqlite3
import time

from . import connection
from .schema import initialize_v1_schema


def log_foreign_key_integrity(db) -> None:
    violations = db.foreign_key_violations()
    if violations:
        logging.warning(
            "Foreign key integrity check found %d issue(s) in %s; first issue: %s",
            len(violations),
            db.db_path,
            violations[0],
        )


def initialize_schema(db, schema_version: int) -> None:
    for attempt in range(5):
        try:
            with db._write_transaction():
                initialize_v1_schema(db.conn, schema_version)
                from .system_anchor_loader import load_system_anchors
                db.seed_system_anchors(load_system_anchors())
            return
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() and "busy" not in str(exc).lower():
                raise
            if attempt == 4:
                raise
            time.sleep(0.2 * (attempt + 1))


def close(db) -> None:
    with db._connection_lock:
        if db._closed:
            return
        db._closed = True
    _, db._thread_state = connection.close_connections(
        db._connections,
        db._connection_lock,
        db.db_path,
    )
