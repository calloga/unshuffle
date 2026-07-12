import json
import sqlite3
from collections.abc import Iterable, Iterator
from itertools import islice
from pathlib import Path
from typing import Optional

from unshuffle.persistence.utils.cache_utils import normalize_feature_vector


REMOVED_VERIFIED_ANCHOR_SESSION = "__removed_verified_anchors__"


def clear_staging(conn: sqlite3.Connection, session_id: Optional[str] = None) -> None:
    if session_id:
        conn.execute("DELETE FROM staging_records WHERE session_id = ?", (session_id,))
        conn.execute("DELETE FROM staging_fts WHERE session_id = ?", (session_id,))
        conn.execute("DELETE FROM coherence_results WHERE session_id = ?", (session_id,))
        conn.execute("DELETE FROM refinement_candidates WHERE session_id = ?", (session_id,))
        conn.execute("DELETE FROM anchor_profiles WHERE session_id = ? AND state = 'candidate'", (session_id,))
        return
    conn.execute("DELETE FROM staging_records")
    conn.execute("DELETE FROM staging_fts")
    conn.execute("DELETE FROM coherence_results")
    conn.execute("DELETE FROM refinement_candidates")
    conn.execute(
        "DELETE FROM anchor_profiles WHERE state NOT IN ('verified', 'system') AND session_id != ?",
        (REMOVED_VERIFIED_ANCHOR_SESSION,),
    )


def remove_staging_by_source(conn: sqlite3.Connection, session_id: str, source_path: str) -> None:
    normalized = source_path.rstrip("/\\")
    forward_exact = normalized.replace("\\", "/")
    backward_exact = normalized.replace("/", "\\")
    forward_pattern = _literal_like_prefix(normalized.replace("\\", "/")) + "/%"
    backward_pattern = _literal_like_prefix(normalized.replace("/", "\\")) + "\\%"
    conn.execute(
        """
        DELETE FROM staging_records
        WHERE session_id = ?
          AND (
              source_path = ?
              OR REPLACE(source_path, '\\', '/') = ?
              OR REPLACE(source_path, '/', '\\') = ?
              OR REPLACE(source_path, '\\', '/') LIKE ? ESCAPE '!'
              OR REPLACE(source_path, '/', '\\') LIKE ? ESCAPE '!'
          )
        """,
        (session_id, normalized, forward_exact, backward_exact, forward_pattern, backward_pattern),
    )


def _literal_like_prefix(value: str) -> str:
    return (
        value.rstrip("/\\")
        .replace("!", "!!")
        .replace("%", "!%")
        .replace("_", "!_")
    )


def normalize_staging_record(record: tuple) -> tuple:
    if len(record) == 15:
        *base_fields, feature_vector, preserved_root, is_preserved = record
        fast_hash = None
        feature_space_version = None
        feature_schema_json = None
        analysis_status = None
        analysis_tags_json = None
        evidence_json = "{}"
    elif len(record) == 19:
        (
            *base_fields,
            feature_vector,
            feature_space_version,
            feature_schema_json,
            analysis_status,
            analysis_tags_json,
            preserved_root,
            is_preserved,
        ) = record
        evidence_json = "{}"
        fast_hash = None
    elif len(record) == 20:
        (
            *base_fields,
            evidence_json,
            feature_vector,
            feature_space_version,
            feature_schema_json,
            analysis_status,
            analysis_tags_json,
            preserved_root,
            is_preserved,
        ) = record
        fast_hash = None

    elif len(record) == 21:
        (
            *hash_fields,
            fast_hash,
            pack_candidates,
            evidence_json,
            feature_vector,
            feature_space_version,
            feature_schema_json,
            analysis_status,
            analysis_tags_json,
            preserved_root,
            is_preserved,
        ) = record
        base_fields = [*hash_fields, pack_candidates]
    else:
        raise ValueError(f"Unsupported staging row shape: expected 15, 19, 20, or 21 items, got {len(record)}")
    if isinstance(evidence_json, str):
        normalized_evidence = evidence_json
    else:
        try:
            normalized_evidence = json.dumps(evidence_json or {}, default=str)
        except TypeError:
            normalized_evidence = "{}"
    return (
        *base_fields[:-1],
        fast_hash,
        base_fields[-1],
        normalized_evidence,
        normalize_feature_vector(feature_vector),
        feature_space_version,
        feature_schema_json,
        analysis_status,
        analysis_tags_json,
        Path(preserved_root).as_posix() if preserved_root else None,
        1 if is_preserved else 0,
    )


def normalize_staging_records(records: Iterable[tuple]) -> list[tuple]:
    return [normalize_staging_record(record) for record in records]


_STAGING_INSERT_SQL = """
    INSERT INTO staging_records (
        row_id, session_id, source_path, sample_name, pack, category, subcategory,
        audio_type, tags, confidence, duration, hash, fast_hash, pack_candidates, evidence_json,
        feature_vector, feature_space_version, feature_schema_json, analysis_status, analysis_tags_json,
        preserved_root, is_preserved
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def add_staging_records_iter(
    conn: sqlite3.Connection,
    session_id: str,
    records: Iterable[tuple],
    *,
    batch_size: int = 1000,
) -> int:
    iterator = iter(records)
    size = max(1, int(batch_size))
    inserted = 0
    while True:
        batch = normalize_staging_records(islice(iterator, size))
        if not batch:
            return inserted
        conn.executemany(
            _STAGING_INSERT_SQL,
            (
                (record[0], session_id, Path(record[1]).as_posix(), *record[2:])
                for record in batch
            ),
        )
        inserted += len(batch)


def add_staging_records_bulk(conn: sqlite3.Connection, session_id: str, records: list[tuple]) -> None:
    add_staging_records_iter(conn, session_id, records)


def get_staging_records(conn: sqlite3.Connection, session_id: str) -> list[dict]:
    return [row for batch in iter_staging_records(conn, session_id) for row in batch]


def iter_staging_records(
    conn: sqlite3.Connection,
    session_id: str,
    *,
    batch_size: int = 1000,
) -> Iterator[list[dict]]:
    cursor = conn.execute(
        "SELECT * FROM staging_records WHERE session_id = ? ORDER BY row_id ASC, id ASC",
        (session_id,),
    )
    size = max(1, int(batch_size))
    while True:
        rows = cursor.fetchmany(size)
        if not rows:
            break
        yield [dict(row) for row in rows]


def get_coherence_staging_records(conn: sqlite3.Connection, session_id: str) -> list[dict]:
    return [row for batch in iter_coherence_staging_records(conn, session_id) for row in batch]


def iter_coherence_staging_records(
    conn: sqlite3.Connection,
    session_id: str,
    *,
    batch_size: int = 1000,
) -> Iterator[list[dict]]:
    cursor = conn.execute(
        """
        SELECT
            row_id,
            id,
            source_path,
            pack,
            category,
            subcategory,
            audio_type,
            confidence,
            feature_vector,
            evidence_json,
            is_preserved
        FROM staging_records
        WHERE session_id = ?
          AND COALESCE(is_preserved, 0) = 0
          AND COALESCE(category, '') NOT IN ('Non-Audio Assets', 'Metadata')
          AND feature_vector IS NOT NULL
        ORDER BY row_id ASC, id ASC
        """,
        (session_id,),
    )
    size = max(1, int(batch_size))
    while True:
        rows = cursor.fetchmany(size)
        if not rows:
            break
        yield [dict(row) for row in rows]


def iter_coherence_group_keys(conn: sqlite3.Connection, session_id: str) -> Iterator[tuple[str, str, str]]:
    cursor = conn.execute(
        """
        SELECT COALESCE(audio_type, ''), COALESCE(category, ''), COALESCE(subcategory, '')
        FROM staging_records
        WHERE session_id = ?
          AND COALESCE(is_preserved, 0) = 0
          AND COALESCE(category, '') NOT IN ('Non-Audio Assets', 'Metadata')
          AND feature_vector IS NOT NULL
          AND COALESCE(CASE WHEN json_valid(COALESCE(evidence_json, ''))
              THEN json_extract(evidence_json, '$.duplicate_shadow.is_shadow') ELSE 0 END, 0) != 1
        GROUP BY COALESCE(audio_type, ''), COALESCE(category, ''), COALESCE(subcategory, '')
        ORDER BY COALESCE(audio_type, ''), COALESCE(category, ''), COALESCE(subcategory, '')
        """,
        (session_id,),
    )
    for row in cursor:
        yield str(row[0] or ""), str(row[1] or ""), str(row[2] or "")


def coherence_group_records(
    conn: sqlite3.Connection,
    session_id: str,
    group_key: tuple[str, str, str],
) -> list[dict]:
    audio_type, category, subcategory = group_key
    cursor = conn.execute(
        """
        SELECT row_id, id, source_path, pack, category, subcategory, audio_type,
               confidence, feature_vector, evidence_json, is_preserved
        FROM staging_records
        WHERE session_id = ?
          AND COALESCE(audio_type, '') = ?
          AND COALESCE(category, '') = ?
          AND COALESCE(subcategory, '') = ?
          AND COALESCE(is_preserved, 0) = 0
          AND feature_vector IS NOT NULL
          AND COALESCE(CASE WHEN json_valid(COALESCE(evidence_json, ''))
              THEN json_extract(evidence_json, '$.duplicate_shadow.is_shadow') ELSE 0 END, 0) != 1
        ORDER BY row_id ASC, id ASC
        """,
        (session_id, audio_type, category, subcategory),
    )
    return [dict(row) for row in cursor]


def coherence_records_by_row_ids(
    conn: sqlite3.Connection,
    session_id: str,
    row_ids: list[int],
) -> list[dict]:
    if not row_ids:
        return []
    rows: list[dict] = []
    for start in range(0, len(row_ids), 900):
        chunk = row_ids[start : start + 900]
        placeholders = ",".join("?" for _ in chunk)
        cursor = conn.execute(
            f"""
            SELECT row_id, id, source_path, pack, category, subcategory, audio_type,
                   confidence, feature_vector, evidence_json, is_preserved
            FROM staging_records
            WHERE session_id = ? AND row_id IN ({placeholders})
            """,
            [session_id, *chunk],
        )
        rows.extend(dict(row) for row in cursor)
    return rows


def update_staging_record(conn: sqlite3.Connection, session_id: str, row_id: int, data: dict[str, str]) -> None:
    fields = [f"{key} = ?" for key in data.keys()]
    params = list(data.values()) + [session_id, row_id]
    conn.execute(
        f"UPDATE staging_records SET {', '.join(fields)} WHERE session_id = ? AND row_id = ?",
        params,
    )
