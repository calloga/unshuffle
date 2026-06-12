import sqlite3

from .schema_ddl import (
    create_coherence_tables,
    create_core_tables,
    create_indexes,
    create_search_objects,
)
from .schema_migrations import ensure_feature_schema_columns, ensure_schema_version


def initialize_v1_schema(conn: sqlite3.Connection, schema_version: int) -> None:
    ensure_schema_version(conn, schema_version)
    create_core_tables(conn)
    ensure_coherence_schema(conn)
    create_search_objects(conn)
    ensure_feature_schema_columns(conn)
    create_indexes(conn)


def ensure_coherence_schema(conn: sqlite3.Connection) -> None:
    create_coherence_tables(conn)
