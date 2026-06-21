import sqlite3
from pathlib import Path
import pydantic

from .models import SchemaVersion
from .schema_ddl import (
    create_coherence_tables,
    create_core_tables,
    create_indexes,
    create_search_objects,
)
from unshuffle.persistence.schema_migrations import ensure_feature_schema_columns, ensure_schema_version

class FileModel(pydantic.BaseModel):
    name: str
    version: int

def migrations_up(conn: sqlite3.Connection) -> None:
    # ensure schema_version table exists
    version = 0
    try:
        version = conn.execute("SELECT version FROM schema_version").fetchone()[0]
    except:
        pass

    migrations_folder = Path('../migrations')

    def _parse_version(_file_name: str)->int:
        return int(_file_name.split('_')[0])

    files = [
        FileModel(version=_parse_version(f.name), name=f)
        for f in migrations_folder.iterdir() if f.is_file() and _parse_version(f.name) > version
    ]

    # get current version from schema_version or 0
    # get migration file up to current version
    # for each run file, increase schema_version and move forward

    for f in files:
        try:
            conn.execute(f.open())
            version+=1
        except:
            print('error')

    SchemaVersion.update(version=version)



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


def ensure_coherence_schema(conn: sqlite3.Connection) -> None:
    create_coherence_tables(conn)
