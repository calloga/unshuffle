from pathlib import Path
from typing import Optional

from unshuffle.core.features import vector_from_blob, feature_blob_from_vector


def normalize_feature_vector(value) -> Optional[bytes]:
    if value is None:
        return None
    vector = vector_from_blob(value)
    if not vector:
        return None
    return feature_blob_from_vector(vector)


def normalize_acoustic_vector(value) -> Optional[bytes]:
    return normalize_feature_vector(value)


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
