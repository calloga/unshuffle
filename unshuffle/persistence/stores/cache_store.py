import json
import sqlite3
from abc import ABC, abstractmethod
from typing import Optional, Callable

from pathlib import Path
from peewee import SqliteDatabase, fn

from unshuffle.core.features import (
    CURRENT_EXTRACTOR_VERSION,
    CURRENT_FEATURE_SPACE_VERSION,
    CURRENT_VECTOR_SCHEMA,
)
from unshuffle.persistence.schema.enums import RecordStepStatus, RecordStatus
from unshuffle.persistence.schema.models import db_proxy, FileCache, Record

class ThreadAwareSqliteDatabase(SqliteDatabase):
    def __init__(self, get_connection: Callable[[], sqlite3.Connection]):
        self._get_connection = get_connection
        super().__init__(':memory:') # not real file for hacking InterfaceError

    def _connect(self):
        return self._get_connection()

    def close(self):
        # lifecycle in UnshuffleDB, not here
        pass

    def close_all(self):
        # lifecycle in UnshuffleDB, not here
        pass

class CacheStore(ABC):
    @abstractmethod
    def get_all_hashes(self) -> dict[str, str]:
        pass

    @abstractmethod
    def has_hash_in_library(self, file_hash: str) -> bool:
        pass

    @abstractmethod
    def get_committed_hashes(self) -> set[str]:
        pass

    @abstractmethod
    def get_cached_hash(self, path: Path, size: int, mtime: float) -> Optional[str]:
        pass

    @abstractmethod
    def _get_cached_hashes(self, chunk):
        pass

    def get_cached_hashes(self, file_stats: list[tuple[Path, int, float]]) -> dict[str, str]:
        if not file_stats:
            return {}
        stats_by_path = {Path(path).as_posix(): (size, mtime) for path, size, mtime in file_stats}
        result: dict[str, str] = {}
        paths = list(stats_by_path)
        for start in range(0, len(paths), 900):
            chunk = paths[start:start + 900]
            rows = self._get_cached_hashes(chunk)
            for row in rows:
                key = str(row["last_path"])
                expected = stats_by_path.get(key)
                if expected is None:
                    continue
                expected_size, expected_mtime = expected
                if int(row["size"] or 0) == expected_size and float(row["mtime"] or 0.0) == expected_mtime:
                    result[key] = row["hash"]
        return result

    @abstractmethod
    def _get_feature_vectors(self, chunk):
        pass

    def get_feature_vectors_bulk(self, file_hashes: list[str]) -> dict[str, bytes]:
        hashes = [str(item) for item in file_hashes if item]
        if not hashes:
            return {}
        result: dict[str, bytes] = {}
        for start in range(0, len(hashes), 900):
            chunk = hashes[start:start + 900]
            rows = self._get_feature_vectors(chunk)
            for row in rows:
                if self._schema_matches_current(row["feature_schema_json"]):
                    result[str(row["hash"])] = row["feature_vector"]
        return result

    @abstractmethod
    def get_feature_vector(self, file_hash: str) -> Optional[bytes]:
        pass

    @abstractmethod
    def clear_cache(self) -> None:
        pass

    @abstractmethod
    def get_cached_path_by_hash(self, file_hash: str) -> Optional[str]:
        pass

    @abstractmethod
    def upsert_cache_rows(self, rows: list[tuple]) -> None:
        pass


class SqliteCacheStore(CacheStore):

    def _get_feature_vectors(self, chunk):
        placeholders = ", ".join("?" for _ in chunk)
        cursor = self._conn.execute(
            f"""
                    SELECT hash, feature_vector, feature_schema_json
                    FROM file_cache
                    WHERE hash IN ({placeholders})
                      AND feature_vector IS NOT NULL
                      AND feature_space_version = ?
                      AND extractor_version = ?
                    """,
            [*chunk, CURRENT_FEATURE_SPACE_VERSION, CURRENT_EXTRACTOR_VERSION],
        )

        return cursor.fetchall()

    def _get_cached_hashes(self, chunk):
        placeholders = ", ".join("?" for _ in chunk)
        cursor = self._conn.execute(
            f"""
                   SELECT hash, last_path, size, mtime
                   FROM file_cache
                   WHERE last_path IN ({placeholders})
                   """,
            chunk,
        )
        return cursor.fetchall()

    def __init__(self, connection: sqlite3.Connection):
        self._conn = connection
        
    def get_all_hashes(self) -> dict[str, str]:
        cursor = self._conn.execute("SELECT hash, last_path FROM file_cache")
        return {row["hash"]: row["last_path"] for row in cursor.fetchall()}

    def has_hash_in_library(self, file_hash: str) -> bool:
        cursor = self._conn.execute(
            """
            SELECT 1
            FROM records
            WHERE file_hash = ?
              AND status IN ('moved', 'copied')
              AND (step_status IS NULL OR step_status = 'COMMITTED')
            LIMIT 1
            """,
            (file_hash,),
        )
        return cursor.fetchone() is not None

    def get_committed_hashes(self,) -> set[str]:
        cursor = self._conn.execute(
            """
            SELECT DISTINCT file_hash
            FROM records
            WHERE status IN ('moved', 'copied')
              AND file_hash IS NOT NULL
              AND (step_status IS NULL OR step_status = 'COMMITTED')
            """
        )
        return {row[0] for row in cursor.fetchall()}

    def get_cached_hash(self, path: Path, size: int, mtime: float) -> Optional[str]:
        cursor = self._conn.execute(
            "SELECT hash FROM file_cache WHERE last_path = ? AND size = ? AND mtime = ?",
            (Path(path).as_posix(), size, mtime),
        )
        row = cursor.fetchone()
        return row["hash"] if row else None

    def get_feature_vector(self, file_hash: str) -> Optional[bytes]:
        cursor = self._conn.execute(
            """
            SELECT feature_vector, feature_schema_json
            FROM file_cache
            WHERE hash = ?
              AND feature_vector IS NOT NULL
              AND feature_space_version = ?
              AND extractor_version = ?
            """,
            (file_hash, CURRENT_FEATURE_SPACE_VERSION, CURRENT_EXTRACTOR_VERSION),
        )
        row = cursor.fetchone()
        if not row or not self._schema_matches_current(row["feature_schema_json"]):
            return None
        return row["feature_vector"]

    def clear_cache(self) -> None:
        self._conn.execute("DELETE FROM file_cache")

    def get_cached_path_by_hash(self, file_hash: str) -> Optional[str]:
        cursor = self._conn.execute("SELECT last_path FROM file_cache WHERE hash = ?", (file_hash,))
        row = cursor.fetchone()
        return row["last_path"] if row else None

    def upsert_cache_rows(self, rows: list[tuple]) -> None:
        self._conn.executemany(
            """
            INSERT OR REPLACE INTO file_cache (
                hash, last_path, size, mtime, feature_vector,
                feature_space_version, extractor_version, feature_schema_json,
                analysis_status, analysis_tags_json, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            rows,
        )

    @staticmethod
    def _schema_matches_current(raw_schema: str | None) -> bool:
        if not raw_schema:
            return False
        try:
            schema = json.loads(raw_schema)
        except (TypeError, json.JSONDecodeError):
            return False
        return list(schema) == list(CURRENT_VECTOR_SCHEMA)

class PeeweeCacheStore(SqliteCacheStore):
    def __init__(self, get_connection: Callable[[], sqlite3.Connection]):
        self._db = ThreadAwareSqliteDatabase(get_connection)
        db_proxy.initialize(self._db)
        super().__init__(get_connection())

    def get_all_hashes(self) -> dict[str, str]:
        return {x.hash: x.last_path for x in FileCache.select()}

    def has_hash_in_library(self, file_hash: str) -> bool:
        _count = Record.select(fn.COUNT(Record.id)).where(
            (Record.status.in_((RecordStatus.COPIED, RecordStatus.MOVED)))
            & (Record.file_hash == file_hash)
            & (Record.step_status >> RecordStepStatus.COMMITTED)
        ).count()

        return _count > 0

    def get_committed_hashes(self) -> set[str]:
        _hashes = Record.select(Record.file_hash.distinct()).where(
            (Record.status.in_((RecordStatus.COPIED, RecordStatus.MOVED)))
            & (Record.file_hash.is_null(False))
            & (Record.step_status >> RecordStepStatus.COMMITTED)
        ).execute()

        return set(h.file_hash for h in _hashes)

    def get_cached_hash(self, path: Path, size: int, mtime: float) -> Optional[str]:
        _hash = FileCache.select().where(
            (FileCache.last_path==Path(path).as_posix())
            & (FileCache.size==size)
            & (FileCache.mtime==mtime)
        ).first()

        return _hash.hash if _hash else None

    def _get_cached_hashes(self, chunk):
        return FileCache.select(
            FileCache.hash, FileCache.last_path, FileCache.size, FileCache.mtime,
        ).where(
            FileCache.last_path.in_(chunk)
        ).dicts()

    def _get_feature_vectors(self, chunk):
        return FileCache.select(
            FileCache.hash, FileCache.feature_vector, FileCache.feature_schema_json,
        ).where(
            (FileCache.hash.in_(chunk))
            & (FileCache.feature_vector.is_null(False))
            & (FileCache.feature_space_version == CURRENT_FEATURE_SPACE_VERSION)
            & (FileCache.extractor_version == CURRENT_EXTRACTOR_VERSION)
        ).dicts()

    def get_feature_vector(self, file_hash: str) -> Optional[bytes]:
        cache = FileCache.select(
            FileCache.feature_vector, FileCache.feature_schema_json,
        ).where(
            (FileCache.hash == file_hash)
            & (FileCache.feature_vector.is_null(False))
            & (FileCache.feature_space_version == CURRENT_FEATURE_SPACE_VERSION)
            & (FileCache.extractor_version == CURRENT_EXTRACTOR_VERSION)
        ).first()

        if not cache or not self._schema_matches_current(cache.feature_schema_json):
            return None
        return cache.feature_vector

    def clear_cache(self) -> None:
        FileCache.delete()

    def get_cached_path_by_hash(self, file_hash: str) -> Optional[str]:
        _row = FileCache.select(FileCache.last_path).where(FileCache.hash == file_hash).first()
        return _row.last_path if _row else None

    def upsert_cache_rows(self, rows: list[tuple]) -> None:
        (
            FileCache
            .insert_many(
                rows,
                fields=[
                    FileCache.hash,
                    FileCache.last_path,
                    FileCache.size,
                    FileCache.mtime,
                    FileCache.feature_vector,
                    FileCache.feature_space_version,
                    FileCache.extractor_version,
                    FileCache.feature_schema_json,
                    FileCache.analysis_status,
                    FileCache.analysis_tags_json,
                ],
            )
            .on_conflict_replace()
            .execute()
        )
