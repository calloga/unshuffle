import sqlite3
import json
from pathlib import Path
from typing import Optional

from ..core.features import (
    CURRENT_EXTRACTOR_VERSION,
    CURRENT_FEATURE_SPACE_VERSION,
    CURRENT_VECTOR_SCHEMA,
    feature_blob_from_vector,
    vector_from_blob,
)


def normalize_feature_vector(value) -> Optional[bytes]:
    if value is None:
        return None
    vector = vector_from_blob(value)
    if not vector:
        return None
    return feature_blob_from_vector(vector)


def normalize_acoustic_vector(value) -> Optional[bytes]:
    return normalize_feature_vector(value)


def get_all_hashes(conn: sqlite3.Connection) -> dict[str, str]:
    cursor = conn.execute("SELECT hash, last_path FROM file_cache")
    return {row["hash"]: row["last_path"] for row in cursor.fetchall()}


def has_hash_in_library(conn: sqlite3.Connection, file_hash: str) -> bool:
    cursor = conn.execute(
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


def get_committed_hashes(conn: sqlite3.Connection) -> set[str]:
    cursor = conn.execute(
        """
        SELECT DISTINCT file_hash
        FROM records
        WHERE status IN ('moved', 'copied')
          AND file_hash IS NOT NULL
          AND (step_status IS NULL OR step_status = 'COMMITTED')
        """
    )
    return {row[0] for row in cursor.fetchall()}


def get_cached_hash(conn: sqlite3.Connection, path: Path, size: int, mtime: float) -> Optional[str]:
    cursor = conn.execute(
        "SELECT hash FROM file_cache WHERE last_path = ? AND size = ? AND mtime = ?",
        (Path(path).as_posix(), size, mtime),
    )
    row = cursor.fetchone()
    return row["hash"] if row else None


def get_cached_hashes(conn: sqlite3.Connection, file_stats: list[tuple[Path, int, float]]) -> dict[str, str]:
    if not file_stats:
        return {}
    stats_by_path = {Path(path).as_posix(): (size, mtime) for path, size, mtime in file_stats}
    result: dict[str, str] = {}
    paths = list(stats_by_path)
    for start in range(0, len(paths), 900):
        chunk = paths[start:start + 900]
        placeholders = ", ".join("?" for _ in chunk)
        cursor = conn.execute(
            f"""
            SELECT hash, last_path, size, mtime
            FROM file_cache
            WHERE last_path IN ({placeholders})
            """,
            chunk,
        )
        for row in cursor.fetchall():
            key = str(row["last_path"])
            expected = stats_by_path.get(key)
            if expected is None:
                continue
            expected_size, expected_mtime = expected
            if int(row["size"] or 0) == expected_size and float(row["mtime"] or 0.0) == expected_mtime:
                result[key] = row["hash"]
    return result


def get_feature_vectors_bulk(conn: sqlite3.Connection, file_hashes: list[str]) -> dict[str, bytes]:
    hashes = [str(item) for item in file_hashes if item]
    if not hashes:
        return {}
    result: dict[str, bytes] = {}
    for start in range(0, len(hashes), 900):
        chunk = hashes[start:start + 900]
        placeholders = ", ".join("?" for _ in chunk)
        cursor = conn.execute(
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
        for row in cursor.fetchall():
            if _schema_matches_current(row["feature_schema_json"]):
                result[str(row["hash"])] = row["feature_vector"]
    return result


def get_feature_vector(conn: sqlite3.Connection, file_hash: str) -> Optional[bytes]:
    cursor = conn.execute(
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
    if not row or not _schema_matches_current(row["feature_schema_json"]):
        return None
    return row["feature_vector"]


def clear_cache(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM file_cache")


def _schema_matches_current(raw_schema: str | None) -> bool:
    if not raw_schema:
        return False
    try:
        schema = json.loads(raw_schema)
    except (TypeError, json.JSONDecodeError):
        return False
    return list(schema) == list(CURRENT_VECTOR_SCHEMA)


def cache_row(
    file_hash: str,
    path: Path,
    size: int,
    mtime: float,
    vector: Optional[bytes] = None,
    feature_space_version: Optional[str] = None,
    extractor_version: Optional[str] = None,
    feature_schema_json: Optional[str] = None,
    analysis_status: Optional[str] = None,
    analysis_tags_json: Optional[str] = None,
) -> tuple:
    return (
        file_hash,
        Path(path).as_posix(),
        size,
        mtime,
        normalize_feature_vector(vector),
        feature_space_version,
        extractor_version,
        feature_schema_json,
        analysis_status,
        analysis_tags_json,
    )


def normalize_cache_rows(hash_list: list[tuple]) -> list[tuple]:
    normalized = []
    for row in hash_list:
        if len(row) == 4:
            file_hash, path, size, mtime = row
            normalized.append(cache_row(file_hash, Path(path), size, mtime))
            continue
        if len(row) == 9:
            file_hash, path, size, mtime, vector, feature_space, extractor, schema, status = row
            normalized.append(cache_row(file_hash, Path(path), size, mtime, vector, feature_space, extractor, schema, status))
            continue
        if len(row) == 10:
            file_hash, path, size, mtime, vector, feature_space, extractor, schema, status, tags = row
            normalized.append(cache_row(file_hash, Path(path), size, mtime, vector, feature_space, extractor, schema, status, tags))
            continue
        raise ValueError(f"Unsupported cache row shape: expected 4, 9, or 10 items, got {len(row)}")
    return normalized


def upsert_cache_rows(conn: sqlite3.Connection, rows: list[tuple]) -> None:
    conn.executemany(
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
