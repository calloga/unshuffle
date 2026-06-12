from pathlib import Path
from typing import Dict, List, Optional, Set

from . import cache_store


def get_all_hashes(db) -> Dict[str, str]:
    return cache_store.get_all_hashes(db.conn)


def has_hash_in_library(db, file_hash: str) -> bool:
    return cache_store.has_hash_in_library(db.conn, file_hash)


def get_committed_hashes(db) -> Set[str]:
    return cache_store.get_committed_hashes(db.conn)


def get_cached_hash(db, path: Path, size: int, mtime: float) -> Optional[str]:
    return cache_store.get_cached_hash(db.conn, path, size, mtime)


def get_cached_hashes(db, file_stats: List[tuple[Path, int, float]]) -> Dict[str, str]:
    return cache_store.get_cached_hashes(db.conn, file_stats)


def update_cache(
    db,
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
) -> None:
    row = cache_store.cache_row(
        file_hash,
        path,
        size,
        mtime,
        vector,
        feature_space_version,
        extractor_version,
        feature_schema_json,
        analysis_status,
        analysis_tags_json,
    )
    with db._write_transaction():
        cache_store.upsert_cache_rows(db.conn, [row])


def get_feature_vector(db, file_hash: str) -> Optional[bytes]:
    return cache_store.get_feature_vector(db.conn, file_hash)


def get_feature_vectors_bulk(db, file_hashes: List[str]) -> Dict[str, bytes]:
    return cache_store.get_feature_vectors_bulk(db.conn, file_hashes)


def get_acoustic_vector(db, file_hash: str) -> Optional[bytes]:
    return get_feature_vector(db, file_hash)


def get_cached_path_by_hash(db, file_hash: str) -> Optional[str]:
    cursor = db.conn.execute("SELECT last_path FROM file_cache WHERE hash = ?", (file_hash,))
    row = cursor.fetchone()
    return row["last_path"] if row else None


def update_cache_bulk(db, hash_list: List[tuple]) -> None:
    if not hash_list:
        return
    normalized = cache_store.normalize_cache_rows(hash_list)
    with db._write_transaction():
        cache_store.upsert_cache_rows(db.conn, normalized)


def remove_from_cache_by_paths(db, path_list: List[str]) -> None:
    if not path_list:
        return
    with db._write_transaction():
        db.conn.executemany("DELETE FROM file_cache WHERE last_path = ?", [(p,) for p in path_list])


def clear_cache(db) -> None:
    with db._write_transaction():
        cache_store.clear_cache(db.conn)
