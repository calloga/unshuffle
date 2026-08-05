from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Iterator, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from itertools import islice
from pathlib import Path
from typing import Any


_PHASE_COLUMNS = {
    "hash": "hash_state",
    "analysis": "analysis_state",
    "classification": "classification_state",
    "staging": "staging_state",
}

_ITEM_UPDATE_COLUMNS = {
    "hash_state",
    "fast_hash",
    "effective_hash",
    "analysis_state",
    "analysis_error_code",
    "analysis_error_text",
    "analysis_attempts",
    "canonical_analysis_item_id",
    "classification_state",
    "pack",
    "category",
    "subcategory",
    "audio_type",
    "confidence",
    "duration",
    "tags",
    "pack_candidates",
    "evidence_json",
    "analysis_status",
    "analysis_tags_json",
    "duplicate_of_item_id",
    "staging_state",
    "claimed_at",
    "claim_owner",
}


def _utcnow_text() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _normalized_path(value: Path | str) -> str:
    return Path(value).as_posix()


def create_scan_run(
    conn: sqlite3.Connection,
    *,
    scan_id: str,
    session_id: str,
    target_root: Path | str,
    roots: Sequence[Path | str],
    mode: str = "new",
    versions: Mapping[str, str | None] | None = None,
) -> None:
    version_values = dict(versions or {})
    now = _utcnow_text()
    conn.execute(
        """
        INSERT INTO scan_runs (
            scan_id, session_id, target_root, roots_json, mode, state, phase,
            hash_version, feature_version, taxonomy_version, classification_version,
            tagging_version, coherence_version, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, 'running', 'discovery', ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(scan_id) DO UPDATE SET
            session_id = excluded.session_id,
            target_root = excluded.target_root,
            roots_json = excluded.roots_json,
            mode = excluded.mode,
            hash_version = excluded.hash_version,
            feature_version = excluded.feature_version,
            taxonomy_version = excluded.taxonomy_version,
            classification_version = excluded.classification_version,
            tagging_version = excluded.tagging_version,
            coherence_version = excluded.coherence_version,
            state = 'running',
            updated_at = excluded.updated_at
        """,
        (
            str(scan_id),
            str(session_id),
            _normalized_path(target_root),
            json.dumps([_normalized_path(root) for root in roots]),
            str(mode or "new"),
            version_values.get("hash"),
            version_values.get("feature"),
            version_values.get("taxonomy"),
            version_values.get("classification"),
            version_values.get("tagging"),
            version_values.get("coherence"),
            now,
            now,
        ),
    )


def get_scan_run(conn: sqlite3.Connection, scan_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM scan_runs WHERE scan_id = ?", (scan_id,)).fetchone()
    return dict(row) if row is not None else None


def newest_resumable_scan(conn: sqlite3.Connection, target_root: Path | str) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT * FROM scan_runs
        WHERE target_root = ? AND state IN ('running', 'paused', 'failed')
        ORDER BY updated_at DESC LIMIT 1
        """,
        (_normalized_path(target_root),),
    ).fetchone()
    return dict(row) if row is not None else None


def update_scan_run(
    conn: sqlite3.Connection,
    scan_id: str,
    *,
    state: str | None = None,
    phase: str | None = None,
    discovered_count: int | None = None,
    completed_count: int | None = None,
    error_count: int | None = None,
    source_signature: str | None = None,
    last_error: Mapping[str, Any] | None = None,
) -> None:
    updates: list[str] = ["updated_at = ?"]
    params: list[Any] = [_utcnow_text()]
    values = {
        "state": state,
        "phase": phase,
        "discovered_count": discovered_count,
        "completed_count": completed_count,
        "error_count": error_count,
        "source_signature": source_signature,
    }
    for column, value in values.items():
        if value is not None:
            updates.append(f"{column} = ?")
            params.append(value)
    if last_error is not None:
        updates.append("last_error_json = ?")
        params.append(json.dumps(dict(last_error), default=str))
    params.append(scan_id)
    conn.execute(f"UPDATE scan_runs SET {', '.join(updates)} WHERE scan_id = ?", params)


def update_session_scan_runs(
    conn: sqlite3.Connection,
    session_id: str,
    *,
    state: str,
    phase: str | None = None,
) -> int:
    if phase is None:
        cursor = conn.execute(
            "UPDATE scan_runs SET state = ?, updated_at = ? WHERE session_id = ?",
            (state, _utcnow_text(), session_id),
        )
    else:
        cursor = conn.execute(
            "UPDATE scan_runs SET state = ?, phase = ?, updated_at = ? WHERE session_id = ?",
            (state, phase, _utcnow_text(), session_id),
        )
    return max(0, int(cursor.rowcount or 0))


def insert_directories(
    conn: sqlite3.Connection,
    scan_id: str,
    rows: Iterable[Sequence[Any]],
    *,
    batch_size: int = 2000,
) -> int:
    sql = """
        INSERT INTO scan_directories (
            scan_id, directory_id, discovery_order, parent_directory_id, depth, normalized_path,
            display_name, is_preserved, is_protected
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(scan_id, normalized_path) DO UPDATE SET
            parent_directory_id = excluded.parent_directory_id,
            depth = excluded.depth,
            display_name = excluded.display_name,
            is_preserved = excluded.is_preserved,
            is_protected = excluded.is_protected
    """
    return _batched_executemany(
        conn,
        sql,
        (
            (
                scan_id,
                int(row[0]),
                int(row[1]),
                int(row[2]) if row[2] is not None else None,
                int(row[3]),
                _normalized_path(row[4]),
                str(row[5]),
                int(bool(row[6])),
                int(bool(row[7])),
            )
            for row in rows
        ),
        batch_size,
    )


def insert_items(
    conn: sqlite3.Connection,
    scan_id: str,
    rows: Iterable[Sequence[Any]],
    *,
    batch_size: int = 2000,
) -> int:
    sql = """
        INSERT INTO scan_items (
            scan_id, item_id, discovery_order, parent_directory_id, normalized_path, sample_name,
            extension, size, mtime, mtime_ns, is_preserved, is_protected,
            is_supported_audio
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(scan_id, normalized_path) DO UPDATE SET
            parent_directory_id = excluded.parent_directory_id,
            sample_name = excluded.sample_name,
            extension = excluded.extension,
            size = excluded.size,
            mtime = excluded.mtime,
            mtime_ns = excluded.mtime_ns,
            is_preserved = excluded.is_preserved,
            is_protected = excluded.is_protected,
            is_supported_audio = excluded.is_supported_audio
    """
    return _batched_executemany(
        conn,
        sql,
        (
            (
                scan_id,
                int(row[0]),
                int(row[1]),
                int(row[2]),
                _normalized_path(row[3]),
                str(row[4]),
                str(row[5] or ""),
                int(row[6] or 0),
                float(row[7]) if row[7] is not None else None,
                int(row[8]) if row[8] is not None else None,
                int(bool(row[9])),
                int(bool(row[10])),
                int(bool(row[11])),
            )
            for row in rows
        ),
        batch_size,
    )


def count_items(conn: sqlite3.Connection, scan_id: str, *, phase: str | None = None, state: str | None = None) -> int:
    where = ["scan_id = ?"]
    params: list[Any] = [scan_id]
    if phase is not None:
        column = _phase_column(phase)
        if state is not None:
            where.append(f"{column} = ?")
            params.append(state)
    row = conn.execute(f"SELECT COUNT(*) FROM scan_items WHERE {' AND '.join(where)}", params).fetchone()
    return int(row[0] if row is not None else 0)


def reset_stale_claims(
    conn: sqlite3.Connection,
    scan_id: str,
    phase: str,
    *,
    stale_after_seconds: int = 300,
) -> int:
    column = _phase_column(phase)
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=max(1, stale_after_seconds))).isoformat(timespec="seconds")
    cursor = conn.execute(
        f"""
        UPDATE scan_items SET {column} = 'pending', claimed_at = NULL, claim_owner = NULL
        WHERE scan_id = ? AND {column} = 'in_progress' AND claimed_at < ?
        """,
        (scan_id, cutoff),
    )
    return max(0, int(cursor.rowcount or 0))


def claim_items(
    conn: sqlite3.Connection,
    scan_id: str,
    phase: str,
    owner: str,
    *,
    limit: int = 1000,
    columns: str = "*",
) -> list[dict[str, Any]]:
    column = _phase_column(phase)
    ids = [
        int(row[0])
        for row in conn.execute(
            f"SELECT item_id FROM scan_items WHERE scan_id = ? AND {column} = 'pending' ORDER BY item_id LIMIT ?",
            (scan_id, max(1, int(limit))),
        )
    ]
    if not ids:
        return []
    placeholders = ", ".join("?" for _ in ids)
    now = _utcnow_text()
    conn.execute(
        f"UPDATE scan_items SET {column} = 'in_progress', claimed_at = ?, claim_owner = ? "
        f"WHERE scan_id = ? AND item_id IN ({placeholders}) AND {column} = 'pending'",
        [now, owner, scan_id, *ids],
    )
    cursor = conn.execute(
        f"SELECT {columns} FROM scan_items WHERE scan_id = ? AND item_id IN ({placeholders}) "
        "AND claim_owner = ? ORDER BY item_id",
        [scan_id, *ids, owner],
    )
    return [dict(row) for row in cursor]


def update_items(
    conn: sqlite3.Connection,
    scan_id: str,
    updates: Iterable[tuple[int, Mapping[str, Any]]],
) -> int:
    changed = 0
    for item_id, values in updates:
        invalid = set(values) - _ITEM_UPDATE_COLUMNS
        if invalid:
            raise ValueError(f"Unsupported scan item update column(s): {sorted(invalid)}")
        if not values:
            continue
        assignments = ", ".join(f"{column} = ?" for column in values)
        params = [*values.values(), scan_id, int(item_id)]
        cursor = conn.execute(
            f"UPDATE scan_items SET {assignments} WHERE scan_id = ? AND item_id = ?",
            params,
        )
        changed += max(0, int(cursor.rowcount or 0))
    return changed


def update_item_hashes_by_path(
    conn: sqlite3.Connection,
    scan_id: str,
    rows: Iterable[tuple[Path | str, str | None, str | None]],
    *,
    batch_size: int = 1000,
) -> int:
    sql = """
        UPDATE scan_items
        SET fast_hash = ?, effective_hash = ?, hash_state = 'done',
            claimed_at = NULL, claim_owner = NULL
        WHERE scan_id = ? AND normalized_path = ?
    """
    return _batched_executemany(
        conn,
        sql,
        (
            (fast_hash, effective_hash, scan_id, _normalized_path(path))
            for path, fast_hash, effective_hash in rows
        ),
        batch_size,
    )


def update_item_hashes(
    conn: sqlite3.Connection,
    scan_id: str,
    rows: Iterable[tuple[int, str | None, str | None, str]],
    *,
    batch_size: int = 1000,
) -> int:
    return _batched_executemany(
        conn,
        """
        UPDATE scan_items
        SET fast_hash = ?, effective_hash = ?, hash_state = ?,
            claimed_at = NULL, claim_owner = NULL
        WHERE scan_id = ? AND item_id = ?
        """,
        (
            (fast_hash, effective_hash, state, scan_id, int(item_id))
            for item_id, fast_hash, effective_hash, state in rows
        ),
        batch_size,
    )


def iter_fast_hash_collision_items(
    conn: sqlite3.Connection,
    scan_id: str,
    *,
    batch_size: int = 1000,
) -> Iterator[list[dict[str, Any]]]:
    cursor = conn.execute(
        """
        WITH collision_groups AS (
            SELECT size, fast_hash
            FROM scan_items
            WHERE scan_id = ? AND hash_state = 'fast_new' AND fast_hash IS NOT NULL
            GROUP BY size, fast_hash HAVING COUNT(*) > 1
        )
        SELECT item.item_id, item.normalized_path, item.fast_hash
        FROM scan_items AS item
        JOIN collision_groups AS collision
          ON collision.size = item.size AND collision.fast_hash = item.fast_hash
        WHERE item.scan_id = ? AND item.hash_state = 'fast_new'
        ORDER BY item.item_id
        """,
        (scan_id, scan_id),
    )
    size = max(1, int(batch_size))
    while True:
        rows = cursor.fetchmany(size)
        if not rows:
            return
        yield [dict(row) for row in rows]


def count_fast_hash_collision_items(conn: sqlite3.Connection, scan_id: str) -> int:
    row = conn.execute(
        """
        SELECT COALESCE(SUM(item_count), 0)
        FROM (
            SELECT COUNT(*) AS item_count
            FROM scan_items
            WHERE scan_id = ? AND hash_state = 'fast_new' AND fast_hash IS NOT NULL
            GROUP BY size, fast_hash HAVING COUNT(*) > 1
        )
        """,
        (scan_id,),
    ).fetchone()
    return int(row[0] if row is not None else 0)


def iter_session_fast_hash_collision_items(
    conn: sqlite3.Connection,
    session_id: str,
    *,
    batch_size: int = 1000,
) -> Iterator[list[dict[str, Any]]]:
    cursor = conn.execute(
        """
        WITH collision_groups AS (
            SELECT item.size, item.fast_hash
            FROM scan_items AS item
            JOIN scan_runs AS run ON run.scan_id = item.scan_id
            WHERE run.session_id = ?
              AND item.fast_hash IS NOT NULL
              AND item.effective_hash = item.fast_hash
            GROUP BY item.size, item.fast_hash HAVING COUNT(*) > 1
        )
        SELECT item.scan_id, item.item_id, item.normalized_path, item.fast_hash
        FROM scan_items AS item
        JOIN scan_runs AS run ON run.scan_id = item.scan_id
        JOIN collision_groups AS collision
          ON collision.size = item.size AND collision.fast_hash = item.fast_hash
        WHERE run.session_id = ? AND item.effective_hash = item.fast_hash
        ORDER BY run.rowid, item.discovery_order, item.item_id
        """,
        (session_id, session_id),
    )
    size = max(1, int(batch_size))
    while True:
        rows = cursor.fetchmany(size)
        if not rows:
            return
        yield [dict(row) for row in rows]


def count_session_fast_hash_collision_items(conn: sqlite3.Connection, session_id: str) -> int:
    row = conn.execute(
        """
        SELECT COALESCE(SUM(item_count), 0) FROM (
            SELECT COUNT(*) AS item_count
            FROM scan_items AS item
            JOIN scan_runs AS run ON run.scan_id = item.scan_id
            WHERE run.session_id = ?
              AND item.fast_hash IS NOT NULL
              AND item.effective_hash = item.fast_hash
            GROUP BY item.size, item.fast_hash HAVING COUNT(*) > 1
        )
        """,
        (session_id,),
    ).fetchone()
    return int(row[0] if row is not None else 0)


def update_session_item_hashes(
    conn: sqlite3.Connection,
    rows: Iterable[tuple[str, int, str | None, str | None]],
    *,
    batch_size: int = 1000,
) -> int:
    materialized = list(rows)
    for scan_id, item_id, fast_hash, effective_hash in materialized:
        if not fast_hash or not effective_hash or fast_hash == effective_hash:
            continue
        conn.execute(
            """
            INSERT OR IGNORE INTO file_cache (
                hash, fast_hash, last_path, size, mtime, first_seen, feature_vector,
                feature_space_version, extractor_version, feature_schema_json,
                analysis_status, analysis_tags_json, updated_at
            )
            SELECT ?, fast_hash, last_path, size, mtime, first_seen, feature_vector,
                   feature_space_version, extractor_version, feature_schema_json,
                   analysis_status, analysis_tags_json, updated_at
            FROM file_cache WHERE hash = ?
            """,
            (effective_hash, fast_hash),
        )
    return _batched_executemany(
        conn,
        """
        UPDATE scan_items SET fast_hash = ?, effective_hash = ?, hash_state = 'done'
        WHERE scan_id = ? AND item_id = ?
        """,
        (
            (fast_hash, effective_hash, scan_id, int(item_id))
            for scan_id, item_id, fast_hash, effective_hash in materialized
        ),
        batch_size,
    )


def iter_append_fast_hash_collision_items(
    conn: sqlite3.Connection,
    session_id: str,
    scan_id: str,
    *,
    batch_size: int = 1000,
) -> Iterator[list[dict[str, Any]]]:
    cursor = conn.execute(
        """
        WITH matching_fast_hashes AS (
            SELECT DISTINCT item.fast_hash
            FROM scan_items AS item
            JOIN staging_records AS staging
              ON staging.session_id = ? AND staging.fast_hash = item.fast_hash
            WHERE item.scan_id = ? AND item.fast_hash IS NOT NULL
        )
        SELECT 'scan' AS source_kind, item.item_id AS source_id,
               item.normalized_path, item.fast_hash, item.effective_hash
        FROM scan_items AS item
        WHERE item.scan_id = ?
          AND item.fast_hash IN (SELECT fast_hash FROM matching_fast_hashes)
          AND item.effective_hash = item.fast_hash
        UNION ALL
        SELECT 'staging' AS source_kind, staging.row_id AS source_id,
               staging.source_path AS normalized_path, staging.fast_hash,
               staging.hash AS effective_hash
        FROM staging_records AS staging
        WHERE staging.session_id = ?
          AND staging.fast_hash IN (SELECT fast_hash FROM matching_fast_hashes)
          AND staging.hash = staging.fast_hash
        ORDER BY fast_hash, source_kind, source_id
        """,
        (session_id, scan_id, scan_id, session_id),
    )
    size = max(1, int(batch_size))
    while True:
        rows = cursor.fetchmany(size)
        if not rows:
            return
        yield [dict(row) for row in rows]


def update_append_promoted_hashes(
    conn: sqlite3.Connection,
    session_id: str,
    scan_id: str,
    rows: Iterable[tuple[str, int, str | None, str | None]],
) -> int:
    changed = 0
    for source_kind, source_id, fast_hash, full_hash in rows:
        if fast_hash and full_hash and fast_hash != full_hash:
            conn.execute(
                """
                INSERT OR IGNORE INTO file_cache (
                    hash, fast_hash, last_path, size, mtime, first_seen, feature_vector,
                    feature_space_version, extractor_version, feature_schema_json,
                    analysis_status, analysis_tags_json, updated_at
                )
                SELECT ?, fast_hash, last_path, size, mtime, first_seen, feature_vector,
                       feature_space_version, extractor_version, feature_schema_json,
                       analysis_status, analysis_tags_json, updated_at
                FROM file_cache WHERE hash = ?
                """,
                (full_hash, fast_hash),
            )
        if source_kind == "scan":
            cursor = conn.execute(
                """
                UPDATE scan_items SET fast_hash = ?, effective_hash = ?, hash_state = 'done'
                WHERE scan_id = ? AND item_id = ?
                """,
                (fast_hash, full_hash, scan_id, int(source_id)),
            )
        else:
            cursor = conn.execute(
                """
                UPDATE staging_records SET fast_hash = ?, hash = ?
                WHERE session_id = ? AND row_id = ?
                """,
                (fast_hash, full_hash, session_id, int(source_id)),
            )
        changed += max(0, int(cursor.rowcount or 0))
    return changed


def finalize_fast_hashes(conn: sqlite3.Connection, scan_id: str) -> int:
    cursor = conn.execute(
        "UPDATE scan_items SET hash_state = 'done' WHERE scan_id = ? AND hash_state = 'fast_new'",
        (scan_id,),
    )
    return max(0, int(cursor.rowcount or 0))


def update_item_classifications_by_path(
    conn: sqlite3.Connection,
    scan_id: str,
    rows: Iterable[Mapping[str, Any]],
    *,
    batch_size: int = 1000,
) -> int:
    sql = """
        UPDATE scan_items SET
            pack = ?, category = ?, subcategory = ?, audio_type = ?, confidence = ?,
            duration = ?, tags = ?, pack_candidates = ?, evidence_json = ?,
            analysis_status = ?, analysis_tags_json = ?, classification_state = ?,
            claimed_at = NULL, claim_owner = NULL
        WHERE scan_id = ? AND normalized_path = ?
    """
    return _batched_executemany(
        conn,
        sql,
        (
            (
                row.get("pack"),
                row.get("category"),
                row.get("subcategory"),
                row.get("audio_type"),
                row.get("confidence"),
                row.get("duration"),
                row.get("tags"),
                row.get("pack_candidates"),
                row.get("evidence_json"),
                row.get("analysis_status"),
                row.get("analysis_tags_json"),
                row.get("classification_state", "done"),
                scan_id,
                _normalized_path(row["source_path"]),
            )
            for row in rows
        ),
        batch_size,
    )


def iter_classified_session_items(
    conn: sqlite3.Connection,
    session_id: str,
    *,
    batch_size: int = 1000,
) -> Iterator[list[dict[str, Any]]]:
    cursor = conn.execute(
        """
        WITH ordered AS (
            SELECT
                item.*,
                run.rowid AS run_order,
                ROW_NUMBER() OVER (
                    PARTITION BY COALESCE(item.effective_hash, '__item__' || item.scan_id || ':' || item.item_id)
                    ORDER BY run.rowid, item.discovery_order, item.item_id
                ) AS duplicate_rank,
                FIRST_VALUE(item.scan_id) OVER (
                    PARTITION BY COALESCE(item.effective_hash, '__item__' || item.scan_id || ':' || item.item_id)
                    ORDER BY run.rowid, item.discovery_order, item.item_id
                ) AS canonical_scan_id,
                FIRST_VALUE(item.item_id) OVER (
                    PARTITION BY COALESCE(item.effective_hash, '__item__' || item.scan_id || ':' || item.item_id)
                    ORDER BY run.rowid, item.discovery_order, item.item_id
                ) AS canonical_item_id
            FROM scan_items AS item
            JOIN scan_runs AS run ON run.scan_id = item.scan_id
            WHERE run.session_id = ? AND item.classification_state = 'done'
        )
        SELECT
            item.*,
            canonical.normalized_path AS canonical_path,
            canonical.effective_hash AS canonical_hash,
            canonical.pack AS canonical_pack,
            canonical.category AS canonical_category,
            canonical.subcategory AS canonical_subcategory,
            canonical.audio_type AS canonical_audio_type,
            canonical.confidence AS canonical_confidence,
            canonical.duration AS canonical_duration,
            canonical.pack_candidates AS canonical_pack_candidates,
            canonical.analysis_status AS canonical_analysis_status,
            canonical.analysis_tags_json AS canonical_analysis_tags_json,
            cache.feature_vector,
            cache.feature_space_version,
            cache.feature_schema_json
        FROM ordered AS item
        LEFT JOIN scan_items AS canonical
          ON item.duplicate_rank > 1
         AND canonical.scan_id = item.canonical_scan_id
         AND canonical.item_id = item.canonical_item_id
        LEFT JOIN file_cache AS cache ON cache.hash = item.effective_hash
        ORDER BY item.run_order, item.discovery_order, item.item_id
        """,
        (session_id,),
    )
    size = max(1, int(batch_size))
    while True:
        rows = cursor.fetchmany(size)
        if not rows:
            return
        yield [dict(row) for row in rows]


def iter_classified_append_items(
    conn: sqlite3.Connection,
    session_id: str,
    scan_ids: Sequence[str],
    *,
    batch_size: int = 1000,
) -> Iterator[list[dict[str, Any]]]:
    selected = [str(scan_id) for scan_id in scan_ids if str(scan_id or "").strip()]
    if not selected:
        return
    placeholders = ", ".join("?" for _ in selected)
    cursor = conn.execute(
        f"""
        WITH candidates AS (
            SELECT
                0 AS source_kind, staging.row_id AS source_order,
                COALESCE(staging.hash, '__staging__' || staging.row_id) AS identity_key,
                NULL AS scan_id, staging.row_id AS item_id,
                staging.source_path AS normalized_path, staging.sample_name,
                staging.fast_hash, staging.hash AS effective_hash,
                staging.pack, staging.category, staging.subcategory, staging.audio_type,
                staging.confidence, staging.duration, staging.tags,
                staging.pack_candidates, staging.evidence_json,
                staging.analysis_status, staging.analysis_tags_json,
                staging.feature_vector, staging.feature_space_version, staging.feature_schema_json
            FROM staging_records AS staging
            WHERE staging.session_id = ?
            UNION ALL
            SELECT
                1 AS source_kind, (run.rowid * 1000000000) + item.discovery_order AS source_order,
                COALESCE(item.effective_hash, '__item__' || item.scan_id || ':' || item.item_id) AS identity_key,
                item.scan_id, item.item_id, item.normalized_path, item.sample_name,
                item.fast_hash, item.effective_hash,
                item.pack, item.category, item.subcategory, item.audio_type,
                item.confidence, item.duration, item.tags,
                item.pack_candidates, item.evidence_json,
                item.analysis_status, item.analysis_tags_json,
                cache.feature_vector, cache.feature_space_version, cache.feature_schema_json
            FROM scan_items AS item
            JOIN scan_runs AS run ON run.scan_id = item.scan_id
            LEFT JOIN file_cache AS cache ON cache.hash = item.effective_hash
            WHERE item.scan_id IN ({placeholders}) AND item.classification_state = 'done'
        ), ranked AS (
            SELECT candidates.*,
                ROW_NUMBER() OVER (
                    PARTITION BY identity_key ORDER BY source_kind, source_order, item_id
                ) AS duplicate_rank,
                FIRST_VALUE(normalized_path) OVER (
                    PARTITION BY identity_key ORDER BY source_kind, source_order, item_id
                ) AS canonical_path,
                FIRST_VALUE(effective_hash) OVER (
                    PARTITION BY identity_key ORDER BY source_kind, source_order, item_id
                ) AS canonical_hash,
                FIRST_VALUE(pack) OVER (
                    PARTITION BY identity_key ORDER BY source_kind, source_order, item_id
                ) AS canonical_pack,
                FIRST_VALUE(category) OVER (
                    PARTITION BY identity_key ORDER BY source_kind, source_order, item_id
                ) AS canonical_category,
                FIRST_VALUE(subcategory) OVER (
                    PARTITION BY identity_key ORDER BY source_kind, source_order, item_id
                ) AS canonical_subcategory,
                FIRST_VALUE(audio_type) OVER (
                    PARTITION BY identity_key ORDER BY source_kind, source_order, item_id
                ) AS canonical_audio_type,
                FIRST_VALUE(confidence) OVER (
                    PARTITION BY identity_key ORDER BY source_kind, source_order, item_id
                ) AS canonical_confidence,
                FIRST_VALUE(duration) OVER (
                    PARTITION BY identity_key ORDER BY source_kind, source_order, item_id
                ) AS canonical_duration,
                FIRST_VALUE(pack_candidates) OVER (
                    PARTITION BY identity_key ORDER BY source_kind, source_order, item_id
                ) AS canonical_pack_candidates,
                FIRST_VALUE(analysis_status) OVER (
                    PARTITION BY identity_key ORDER BY source_kind, source_order, item_id
                ) AS canonical_analysis_status,
                FIRST_VALUE(analysis_tags_json) OVER (
                    PARTITION BY identity_key ORDER BY source_kind, source_order, item_id
                ) AS canonical_analysis_tags_json,
                FIRST_VALUE(feature_vector) OVER (
                    PARTITION BY identity_key ORDER BY source_kind, source_order, item_id
                ) AS canonical_feature_vector,
                FIRST_VALUE(feature_space_version) OVER (
                    PARTITION BY identity_key ORDER BY source_kind, source_order, item_id
                ) AS canonical_feature_space_version,
                FIRST_VALUE(feature_schema_json) OVER (
                    PARTITION BY identity_key ORDER BY source_kind, source_order, item_id
                ) AS canonical_feature_schema_json
            FROM candidates
        )
        SELECT
            scan_id, item_id, normalized_path, sample_name, fast_hash, effective_hash,
            pack, category, subcategory, audio_type, confidence, duration, tags,
            pack_candidates, evidence_json, analysis_status, analysis_tags_json,
            duplicate_rank, canonical_path, canonical_hash, canonical_pack,
            canonical_category, canonical_subcategory, canonical_audio_type,
            canonical_confidence, canonical_duration, canonical_pack_candidates,
            canonical_analysis_status, canonical_analysis_tags_json,
            CASE WHEN duplicate_rank > 1 THEN canonical_feature_vector ELSE feature_vector END AS feature_vector,
            CASE WHEN duplicate_rank > 1 THEN canonical_feature_space_version ELSE feature_space_version END AS feature_space_version,
            CASE WHEN duplicate_rank > 1 THEN canonical_feature_schema_json ELSE feature_schema_json END AS feature_schema_json
        FROM ranked WHERE source_kind = 1
        ORDER BY source_order, item_id
        """,
        [session_id, *selected],
    )
    size = max(1, int(batch_size))
    while True:
        rows = cursor.fetchmany(size)
        if not rows:
            return
        yield [dict(row) for row in rows]


def iter_canonical_audio_items(
    conn: sqlite3.Connection,
    scan_id: str,
    *,
    batch_size: int = 2000,
) -> Iterator[list[dict[str, Any]]]:
    cursor = conn.execute(
        """
        WITH ranked AS (
            SELECT item.*,
                   ROW_NUMBER() OVER (
                       PARTITION BY COALESCE(effective_hash, '__item__' || item_id)
                       ORDER BY item_id
                   ) AS hash_rank
            FROM scan_items AS item
            WHERE scan_id = ? AND is_supported_audio = 1
        )
        SELECT * FROM ranked WHERE hash_rank = 1 ORDER BY item_id
        """,
        (scan_id,),
    )
    size = max(1, int(batch_size))
    while True:
        rows = cursor.fetchmany(size)
        if not rows:
            return
        yield [dict(row) for row in rows]


def iter_classification_items(
    conn: sqlite3.Connection,
    scan_id: str,
    *,
    batch_size: int = 1000,
) -> Iterator[list[dict[str, Any]]]:
    cursor = conn.execute(
        """
        SELECT item.*, cache.feature_vector, cache.feature_space_version,
               cache.feature_schema_json, cache.analysis_status AS cached_analysis_status,
               cache.analysis_tags_json AS cached_analysis_tags_json
        FROM scan_items AS item
        LEFT JOIN file_cache AS cache ON cache.hash = item.effective_hash
        WHERE item.scan_id = ? AND item.classification_state = 'pending'
        ORDER BY item.item_id
        """,
        (scan_id,),
    )
    size = max(1, int(batch_size))
    while True:
        rows = cursor.fetchmany(size)
        if not rows:
            return
        yield [dict(row) for row in rows]


def update_analysis_by_hash(
    conn: sqlite3.Connection,
    scan_id: str,
    rows: Iterable[tuple[str, str, str | None, str | None, str | None]],
    *,
    batch_size: int = 1000,
) -> int:
    return _batched_executemany(
        conn,
        """
        UPDATE scan_items SET
            analysis_state = ?, analysis_error_code = ?, analysis_status = ?, analysis_error_text = ?,
            analysis_attempts = analysis_attempts + 1
        WHERE scan_id = ? AND effective_hash = ? AND is_supported_audio = 1
        """,
        (
            (state, error_code, analysis_status, error_text, scan_id, effective_hash)
            for effective_hash, state, error_code, analysis_status, error_text in rows
        ),
        batch_size,
    )


def classified_session_stats(conn: sqlite3.Connection, session_id: str) -> dict[str, Any]:
    rows = conn.execute(
        """
        WITH ranked AS (
            SELECT
                item.category,
                ROW_NUMBER() OVER (
                    PARTITION BY COALESCE(item.effective_hash, '__item__' || item.scan_id || ':' || item.item_id)
                    ORDER BY run.rowid, item.discovery_order, item.item_id
                ) AS duplicate_rank
            FROM scan_items AS item
            JOIN scan_runs AS run ON run.scan_id = item.scan_id
            WHERE run.session_id = ? AND item.classification_state = 'done'
        )
        SELECT
            CASE WHEN duplicate_rank > 1 THEN 'Duplicates' ELSE category END AS summary_category,
            COUNT(*) AS item_count,
            COALESCE(SUM(duplicate_rank > 1), 0) AS duplicate_count
        FROM ranked
        GROUP BY summary_category
        """,
        (session_id,),
    ).fetchall()
    categories = {
        str(category or "Uncategorized"): int(count)
        for category, count, _duplicate_count in rows
    }
    return {
        "total": sum(int(count) for _category, count, _duplicate_count in rows),
        "duplicates": sum(int(count) for _category, _item_count, count in rows),
        "category_counts": categories,
    }


def exclude_classified_session_paths(
    conn: sqlite3.Connection,
    session_id: str,
    roots: Iterable[Path | str],
) -> int:
    changed = 0
    for root in roots:
        normalized = _normalized_path(root).rstrip("/")
        escaped = normalized.replace("!", "!!").replace("%", "!%").replace("_", "!_")
        cursor = conn.execute(
            """
            UPDATE scan_items SET classification_state = 'excluded'
            WHERE scan_id IN (SELECT scan_id FROM scan_runs WHERE session_id = ?)
              AND classification_state = 'done'
              AND (
                    REPLACE(normalized_path, '\\', '/') = ?
                 OR REPLACE(normalized_path, '\\', '/') LIKE ? ESCAPE '!'
              )
            """,
            (session_id, normalized, escaped + "/%"),
        )
        changed += max(0, int(cursor.rowcount or 0))
    return changed


def iter_items(
    conn: sqlite3.Connection,
    scan_id: str,
    *,
    columns: str = "*",
    where_sql: str = "1 = 1",
    params: Sequence[Any] = (),
    batch_size: int = 1000,
) -> Iterator[list[dict[str, Any]]]:
    cursor = conn.execute(
        f"SELECT {columns} FROM scan_items WHERE scan_id = ? AND ({where_sql}) ORDER BY item_id",
        [scan_id, *params],
    )
    size = max(1, int(batch_size))
    while True:
        rows = cursor.fetchmany(size)
        if not rows:
            return
        yield [dict(row) for row in rows]


def iter_directories(
    conn: sqlite3.Connection,
    scan_id: str,
    *,
    columns: str = "*",
    where_sql: str = "1 = 1",
    params: Sequence[Any] = (),
    batch_size: int = 1000,
) -> Iterator[list[dict[str, Any]]]:
    cursor = conn.execute(
        f"SELECT {columns} FROM scan_directories WHERE scan_id = ? AND ({where_sql}) ORDER BY discovery_order",
        [scan_id, *params],
    )
    size = max(1, int(batch_size))
    while True:
        rows = cursor.fetchmany(size)
        if not rows:
            return
        yield [dict(row) for row in rows]


def iter_discovered_nodes(
    conn: sqlite3.Connection,
    scan_id: str,
    *,
    batch_size: int = 1000,
) -> Iterator[list[dict[str, Any]]]:
    cursor = conn.execute(
        """
        SELECT discovery_order, normalized_path, display_name AS name,
               CASE WHEN parent_directory_id IS NULL THEN 'root' ELSE 'directory' END AS node_type,
               is_preserved, NULL AS extension, NULL AS fast_hash, NULL AS effective_hash
        FROM scan_directories WHERE scan_id = ?
        UNION ALL
        SELECT discovery_order, normalized_path, sample_name AS name, 'file' AS node_type,
               is_preserved, extension, fast_hash, effective_hash
        FROM scan_items WHERE scan_id = ?
        ORDER BY discovery_order
        """,
        (scan_id, scan_id),
    )
    size = max(1, int(batch_size))
    while True:
        rows = cursor.fetchmany(size)
        if not rows:
            return
        yield [dict(row) for row in rows]


def fast_hash_collision_groups(conn: sqlite3.Connection, scan_id: str) -> Iterator[dict[str, Any]]:
    cursor = conn.execute(
        """
        SELECT size, fast_hash, COUNT(*) AS item_count
        FROM scan_items
        WHERE scan_id = ? AND fast_hash IS NOT NULL
        GROUP BY size, fast_hash HAVING COUNT(*) > 1
        ORDER BY MIN(item_id)
        """,
        (scan_id,),
    )
    for row in cursor:
        yield dict(row)


def delete_scan_run(conn: sqlite3.Connection, scan_id: str) -> None:
    conn.execute("DELETE FROM scan_runs WHERE scan_id = ?", (scan_id,))


def _phase_column(phase: str) -> str:
    try:
        return _PHASE_COLUMNS[str(phase)]
    except KeyError as exc:
        raise ValueError(f"Unsupported scan phase: {phase}") from exc


def _batched_executemany(
    conn: sqlite3.Connection,
    sql: str,
    rows: Iterable[Sequence[Any]],
    batch_size: int,
) -> int:
    iterator = iter(rows)
    size = max(1, int(batch_size))
    inserted = 0
    while True:
        batch = list(islice(iterator, size))
        if not batch:
            return inserted
        conn.executemany(sql, batch)
        inserted += len(batch)
