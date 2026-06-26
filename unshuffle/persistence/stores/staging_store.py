import json
import sqlite3
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


def normalize_staging_records(records: list[tuple]) -> list[tuple]:
    normalized = []
    for record in records:
        if len(record) == 15:
            *base_fields, feature_vector, preserved_root, is_preserved = record
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
        else:
            raise ValueError(f"Unsupported staging row shape: expected 15, 19, or 20 items, got {len(record)}")
        if isinstance(evidence_json, str):
            normalized_evidence = evidence_json
        else:
            try:
                normalized_evidence = json.dumps(evidence_json or {}, default=str)
            except TypeError:
                normalized_evidence = "{}"
        normalized.append(
            (
                *base_fields,
                normalized_evidence,
                normalize_feature_vector(feature_vector),
                feature_space_version,
                feature_schema_json,
                analysis_status,
                analysis_tags_json,
                Path(preserved_root).as_posix() if preserved_root else None,
                1 if is_preserved else 0,
            )
        )
    return normalized


def add_staging_records_bulk(conn: sqlite3.Connection, session_id: str, records: list[tuple]) -> None:
    normalized = normalize_staging_records(records)
    conn.executemany(
        """
        INSERT INTO staging_records (
            row_id, session_id, source_path, sample_name, pack, category, subcategory,
            audio_type, tags, confidence, duration, hash, pack_candidates, evidence_json,
            feature_vector, feature_space_version, feature_schema_json, analysis_status, analysis_tags_json,
            preserved_root, is_preserved
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [(record[0], session_id, Path(record[1]).as_posix(), *record[2:]) for record in normalized],
    )


def get_staging_records(conn: sqlite3.Connection, session_id: str) -> list[dict]:
    cursor = conn.execute(
        "SELECT * FROM staging_records WHERE session_id = ? ORDER BY row_id ASC, id ASC",
        (session_id,),
    )
    return [dict(row) for row in cursor.fetchall()]


def update_staging_record(conn: sqlite3.Connection, session_id: str, row_id: int, data: dict[str, str]) -> None:
    fields = [f"{key} = ?" for key in data.keys()]
    params = list(data.values()) + [session_id, row_id]
    conn.execute(
        f"UPDATE staging_records SET {', '.join(fields)} WHERE session_id = ? AND row_id = ?",
        params,
    )
