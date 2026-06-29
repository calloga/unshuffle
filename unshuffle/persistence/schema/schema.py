import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .models import SchemaVersion, db_proxy
from .schema_ddl import (
    create_coherence_tables,
    create_core_tables,
    create_indexes,
    create_search_objects, ensure_id_fields,
)
from unshuffle.persistence.schema_migrations import ensure_feature_schema_columns, ensure_schema_version
from ...core.logging import logger


@dataclass
class FileModel:
    name: str
    version: int
    file: Path

def _ensure_new_schema_version(conn: sqlite3.Connection):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_version
        (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            version INTEGER NOT NULL
        )
        """
    )
    _version_exists = conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0]
    if not _version_exists:
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (0,))

        return 0

    return conn.execute("SELECT version FROM schema_version").fetchone()[0]

def migrations_up(connection: sqlite3.Connection ) -> None:
    # ensure schema_version table exists
    # get current version from schema_version or 0
    version = _ensure_new_schema_version(connection)

    migrations_folder = Path(__file__).parents[1].joinpath('migrations').resolve()

    def _parse_version(_file_name: str)->int:
        return int(_file_name.split('_')[0])

    # migrate from old versioning
    _file_models=[
        FileModel(version=_parse_version(f.name), name=str(f), file=f)
        for f in migrations_folder.iterdir() if f.is_file()
    ]

    if not _file_models:
        return

    # old version are greater than new versions
    if (version - _file_models[-1].version) > 0:
        version=_file_models[-1].version
        connection.execute('UPDATE schema_version SET version=?', (version,))

    files = [f for f in _file_models if f.version > version]

    # get migration file up to current version
    # for each run file, increase schema_version and move forward

    for f in files:
        try:
            with f.file.open("r", encoding="utf-8") as _f:
                _migration_text = _f.read()
                connection.executescript(_migration_text)
                connection.execute('UPDATE schema_version SET version=?', (f.version,))

        except Exception as e:
            logger.error(f"Error running migration {f.name}: {type(e)} {e}")

def initialize_v1_schema(conn: sqlite3.Connection, schema_version: int) -> None:
    # ensure schema_version table exists
    # get current version from schema_version or 0
    # get migration file up to current version
    # for each run file, increase schema_version and move forward

    ensure_schema_version(conn, schema_version)
    create_core_tables(conn)
    ensure_coherence_schema(conn)
    create_search_objects(conn)
    ensure_feature_schema_columns(conn)
    create_indexes(conn)
    ensure_id_fields(conn)


def ensure_coherence_schema(conn: sqlite3.Connection) -> None:
    create_coherence_tables(conn)
