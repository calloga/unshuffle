import json
import sqlite3
from typing import Any, Optional
from pathlib import Path

from unshuffle.persistence.utils.cache_utils import normalize_feature_vector
from unshuffle.core.features import vector_from_blob


REMOVED_VERIFIED_ANCHOR_SESSION = "__removed_verified_anchors__"


def _normalized_source_path(value: Any) -> str:
    return Path(str(value or "")).as_posix()


def upsert_coherence_results(conn: sqlite3.Connection, session_id: str, results: list[Any]) -> None:
    conn.execute("DELETE FROM coherence_results WHERE session_id = ?", (session_id,))
    conn.executemany(
        """
        INSERT INTO coherence_results (
            session_id, record_id, category, subcategory, coherence_status,
            coherence_score, cluster_id, is_outlier, review_reason,
            suggested_alternate_category, suggested_alternate_subcategory,
            nearest_neighbor_summary_json, anchor_fit_status, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        [
            (
                session_id,
                str(result.record_id),
                result.category,
                result.subcategory,
                result.coherence_status,
                float(result.coherence_score),
                result.cluster_id,
                1 if result.is_outlier else 0,
                result.review_reason,
                result.suggested_alternate_category,
                result.suggested_alternate_subcategory,
                json.dumps(result.nearest_neighbor_summary or {}),
                result.anchor_fit_status,
            )
            for result in results
        ],
    )


def list_coherence_results(conn: sqlite3.Connection, session_id: str) -> list[dict[str, Any]]:
    cursor = conn.execute(
        "SELECT * FROM coherence_results WHERE session_id = ? ORDER BY record_id",
        (session_id,),
    )
    return [dict(row) for row in cursor.fetchall()]


def upsert_refinement_candidates(conn: sqlite3.Connection, session_id: str, candidates: list[Any]) -> None:
    conn.execute(
        "DELETE FROM refinement_candidates WHERE session_id = ? AND state IN ('pending', 'auto_staged')",
        (session_id,),
    )
    if not candidates:
        return
    conn.executemany(
        """
        INSERT INTO refinement_candidates (
            session_id, candidate_id, record_id, current_audio_type, current_category,
            current_subcategory, suggested_audio_type, suggested_category, suggested_subcategory,
            evidence, coherence_status, confidence_score, state, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(session_id, candidate_id) DO UPDATE SET
            record_id=excluded.record_id,
            current_audio_type=excluded.current_audio_type,
            current_category=excluded.current_category,
            current_subcategory=excluded.current_subcategory,
            suggested_audio_type=excluded.suggested_audio_type,
            suggested_category=excluded.suggested_category,
            suggested_subcategory=excluded.suggested_subcategory,
            evidence=excluded.evidence,
            coherence_status=excluded.coherence_status,
            confidence_score=excluded.confidence_score,
            state=CASE
                WHEN refinement_candidates.state IN ('accepted', 'ignored') THEN refinement_candidates.state
                ELSE excluded.state
            END,
            updated_at=CURRENT_TIMESTAMP
        """,
        [
            (
                session_id,
                candidate.candidate_id,
                str(candidate.record_id),
                getattr(candidate, "current_audio_type", ""),
                candidate.current_category,
                candidate.current_subcategory,
                getattr(candidate, "suggested_audio_type", ""),
                candidate.suggested_category,
                candidate.suggested_subcategory,
                candidate.evidence,
                candidate.coherence_status,
                float(candidate.confidence_score),
                candidate.state,
            )
            for candidate in candidates
        ],
    )


def list_refinement_candidates(conn: sqlite3.Connection, session_id: str, state: Optional[str] = None) -> list[dict[str, Any]]:
    if state:
        cursor = conn.execute(
            "SELECT * FROM refinement_candidates WHERE session_id = ? AND state = ? ORDER BY confidence_score DESC",
            (session_id, state),
        )
    else:
        cursor = conn.execute(
            "SELECT * FROM refinement_candidates WHERE session_id = ? ORDER BY confidence_score DESC",
            (session_id,),
        )
    return [dict(row) for row in cursor.fetchall()]


def count_refinement_candidates(conn: sqlite3.Connection, session_id: str, state: Optional[str] = None) -> int:
    if state:
        cursor = conn.execute(
            "SELECT COUNT(*) FROM refinement_candidates WHERE session_id = ? AND state = ?",
            (session_id, state),
        )
    else:
        cursor = conn.execute(
            "SELECT COUNT(*) FROM refinement_candidates WHERE session_id = ?",
            (session_id,),
        )
    return int(cursor.fetchone()[0])


def set_refinement_candidate_state(conn: sqlite3.Connection, session_id: str, candidate_ids: list[str], state: str) -> None:
    conn.executemany(
        """
        UPDATE refinement_candidates
        SET state = ?, updated_at = CURRENT_TIMESTAMP
        WHERE session_id = ? AND candidate_id = ?
        """,
        [(state, session_id, candidate_id) for candidate_id in candidate_ids],
    )


def upsert_coherence_review_decisions(conn: sqlite3.Connection, session_id: str, decisions: list[dict[str, Any]]) -> None:
    rows = []
    for decision in decisions or []:
        source_path = _normalized_source_path(decision.get("source_path"))
        if not source_path:
            continue
        rows.append(
            (
                source_path,
                str(decision.get("file_hash") or ""),
                str(decision.get("decision_type") or ""),
                str(decision.get("current_audio_type") or ""),
                str(decision.get("current_category") or ""),
                str(decision.get("current_subcategory") or ""),
                str(decision.get("target_audio_type") or ""),
                str(decision.get("target_category") or ""),
                str(decision.get("target_subcategory") or ""),
                session_id,
            )
        )
    if not rows:
        return
    conn.executemany(
        """
        INSERT INTO coherence_review_decisions (
            source_path, file_hash, decision_type,
            current_audio_type, current_category, current_subcategory,
            target_audio_type, target_category, target_subcategory,
            created_session_id, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(source_path) DO UPDATE SET
            file_hash=excluded.file_hash,
            decision_type=excluded.decision_type,
            current_audio_type=excluded.current_audio_type,
            current_category=excluded.current_category,
            current_subcategory=excluded.current_subcategory,
            target_audio_type=excluded.target_audio_type,
            target_category=excluded.target_category,
            target_subcategory=excluded.target_subcategory,
            created_session_id=excluded.created_session_id,
            updated_at=CURRENT_TIMESTAMP
        """,
        rows,
    )


def list_coherence_review_decisions(
    conn: sqlite3.Connection,
    *,
    source_paths: list[str] | None = None,
    file_hashes: list[str] | None = None,
) -> list[dict[str, Any]]:
    normalized_paths = sorted({_normalized_source_path(path) for path in source_paths or [] if (path or "").strip()})
    hashes = sorted({(item or "").strip() for item in file_hashes or [] if (item or "").strip()})
    rows_by_path: dict[str, dict[str, Any]] = {}

    def fetch_in(column: str, values: list[str]) -> None:
        for start in range(0, len(values), 800):
            chunk = values[start:start + 800]
            if not chunk:
                continue
            placeholders = ", ".join("?" for _ in chunk)
            cursor = conn.execute(
                f"""
                SELECT *
                FROM coherence_review_decisions
                WHERE {column} IN ({placeholders})
                ORDER BY updated_at DESC
                """,
                chunk,
            )
            for row in cursor.fetchall():
                payload = dict(row)
                rows_by_path.setdefault(_normalized_source_path(payload.get("source_path")), payload)

    fetch_in("source_path", normalized_paths)
    fetch_in("file_hash", hashes)
    return list(rows_by_path.values())


def upsert_anchor_candidates(conn: sqlite3.Connection, session_id: str, anchors: list[Any]) -> None:
    conn.execute("DELETE FROM anchor_profiles WHERE session_id = ? AND state = 'candidate'", (session_id,))
    if not anchors:
        return
    _upsert_anchor_profiles(conn, session_id, anchors, update_state=False)


def upsert_anchor_profiles(conn: sqlite3.Connection, session_id: str, anchors: list[Any]) -> None:
    _upsert_anchor_profiles(conn, session_id, anchors, update_state=True)


def upsert_anchor_profile_rows(conn: sqlite3.Connection, session_id: str, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    conn.executemany(
        """
        INSERT INTO anchor_profiles (
            session_id, anchor_id, audio_type, category, subcategory, cluster_id,
            feature_space_version, extractor_version, feature_schema_json,
            medoid_vector, cluster_centroid, cluster_std, coherence_radius,
            n_reference_items, state, profile_json, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(session_id, anchor_id) DO UPDATE SET
            audio_type=excluded.audio_type,
            category=excluded.category,
            subcategory=excluded.subcategory,
            cluster_id=excluded.cluster_id,
            feature_space_version=excluded.feature_space_version,
            extractor_version=excluded.extractor_version,
            feature_schema_json=excluded.feature_schema_json,
            medoid_vector=excluded.medoid_vector,
            cluster_centroid=excluded.cluster_centroid,
            cluster_std=excluded.cluster_std,
            coherence_radius=excluded.coherence_radius,
            n_reference_items=excluded.n_reference_items,
            state=excluded.state,
            profile_json=excluded.profile_json,
            updated_at=CURRENT_TIMESTAMP
        """,
        [
            (
                session_id,
                row.get("anchor_id"),
                row.get("audio_type"),
                row.get("category"),
                row.get("subcategory"),
                row.get("cluster_id"),
                row.get("feature_space_version"),
                row.get("extractor_version"),
                row.get("feature_schema_json"),
                normalize_feature_vector(row.get("medoid_vector")),
                normalize_feature_vector(row.get("cluster_centroid")),
                normalize_feature_vector(row.get("cluster_std")),
                float(row.get("coherence_radius") or 0.0),
                int(row.get("n_reference_items") or 0),
                row.get("state") or "candidate",
                row.get("profile_json"),
            )
            for row in rows
            if row.get("anchor_id")
        ],
    )


def _upsert_anchor_profiles(conn: sqlite3.Connection, session_id: str, anchors: list[Any], *, update_state: bool) -> None:
    if not anchors:
        return
    state_update = "state=excluded.state," if update_state else ""
    conn.executemany(
        f"""
        INSERT INTO anchor_profiles (
            session_id, anchor_id, audio_type, category, subcategory, cluster_id,
            feature_space_version, extractor_version, feature_schema_json,
            medoid_vector, cluster_centroid, cluster_std, coherence_radius,
            n_reference_items, state, profile_json, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(session_id, anchor_id) DO UPDATE SET
            audio_type=excluded.audio_type,
            category=excluded.category,
            subcategory=excluded.subcategory,
            cluster_id=excluded.cluster_id,
            feature_space_version=excluded.feature_space_version,
            extractor_version=excluded.extractor_version,
            feature_schema_json=excluded.feature_schema_json,
            medoid_vector=excluded.medoid_vector,
            cluster_centroid=excluded.cluster_centroid,
            cluster_std=excluded.cluster_std,
            coherence_radius=excluded.coherence_radius,
            n_reference_items=excluded.n_reference_items,
            {state_update}
            profile_json=excluded.profile_json,
            updated_at=CURRENT_TIMESTAMP
        """,
        [
            (
                session_id,
                anchor.anchor_id,
                getattr(anchor, "audio_type", ""),
                anchor.category,
                anchor.subcategory,
                anchor.cluster_id,
                anchor.feature_space_version,
                anchor.extractor_version,
                json.dumps(list(anchor.vector_schema)),
                normalize_feature_vector(anchor.medoid_vector),
                normalize_feature_vector(anchor.cluster_centroid),
                normalize_feature_vector(anchor.cluster_std),
                float(anchor.coherence_radius),
                int(anchor.n_reference_items),
                anchor.state,
                json.dumps(anchor.profile_payload or {}),
            )
            for anchor in anchors
        ],
    )


def list_anchor_candidates(conn: sqlite3.Connection, session_id: str, state: Optional[str] = None) -> list[dict[str, Any]]:
    if state:
        cursor = conn.execute(
            "SELECT * FROM anchor_profiles WHERE session_id = ? AND state = ? ORDER BY audio_type, category, subcategory",
            (session_id, state),
        )
    else:
        cursor = conn.execute(
            "SELECT * FROM anchor_profiles WHERE session_id = ? ORDER BY audio_type, category, subcategory",
            (session_id,),
        )
    return [dict(row) for row in cursor.fetchall()]


def ensure_verified_anchors_for_session(conn: sqlite3.Connection, session_id: str) -> int:
    removed_verified_anchor_ids = {
        str(row["anchor_id"])
        for row in conn.execute(
            """
            SELECT DISTINCT anchor_id
            FROM anchor_profiles
            WHERE state = 'ignored'
              AND session_id = ?
            """,
            (REMOVED_VERIFIED_ANCHOR_SESSION,),
        ).fetchall()
    }
    rows = conn.execute(
        """
        SELECT *
        FROM anchor_profiles
        WHERE state IN ('verified', 'system')
          AND session_id != ?
        ORDER BY updated_at DESC
        """,
        (session_id,),
    ).fetchall()
    if not rows:
        return 0
    existing = {
        str(row["anchor_id"]): str(row["state"] or "")
        for row in conn.execute(
            "SELECT anchor_id, state FROM anchor_profiles WHERE session_id = ?",
            (session_id,),
        ).fetchall()
    }
    copied = 0
    seen: set[str] = set()
    insert_rows = []
    for row in rows:
        anchor_id = str(row["anchor_id"])
        if (
            not anchor_id
            or anchor_id in removed_verified_anchor_ids
            or existing.get(anchor_id) in ("verified", "system", "ignored")
            or anchor_id in seen
        ):
            continue
        seen.add(anchor_id)
        copied += 1
        insert_rows.append(
            (
                session_id,
                anchor_id,
                row["audio_type"],
                row["category"],
                row["subcategory"],
                row["cluster_id"],
                row["feature_space_version"],
                row["extractor_version"],
                row["feature_schema_json"],
                row["medoid_vector"],
                row["cluster_centroid"],
                row["cluster_std"],
                row["coherence_radius"],
                row["n_reference_items"],
                row["state"],
                row["profile_json"],
            )
        )
    if insert_rows:
        conn.executemany(
            """
            INSERT INTO anchor_profiles (
                session_id, anchor_id, audio_type, category, subcategory, cluster_id,
                feature_space_version, extractor_version, feature_schema_json,
                medoid_vector, cluster_centroid, cluster_std, coherence_radius,
                n_reference_items, state, profile_json, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(session_id, anchor_id) DO UPDATE SET
                audio_type=excluded.audio_type,
                category=excluded.category,
                subcategory=excluded.subcategory,
                cluster_id=excluded.cluster_id,
                feature_space_version=excluded.feature_space_version,
                extractor_version=excluded.extractor_version,
                feature_schema_json=excluded.feature_schema_json,
                medoid_vector=excluded.medoid_vector,
                cluster_centroid=excluded.cluster_centroid,
                cluster_std=excluded.cluster_std,
                coherence_radius=excluded.coherence_radius,
                n_reference_items=excluded.n_reference_items,
                state=excluded.state,
                profile_json=excluded.profile_json,
                updated_at=CURRENT_TIMESTAMP
            WHERE anchor_profiles.state != 'ignored'
            """,
            insert_rows,
        )
    return copied


def set_anchor_candidate_state(conn: sqlite3.Connection, session_id: str, anchor_ids: list[str], state: str) -> None:
    conn.executemany(
        """
        UPDATE anchor_profiles
        SET state = ?, updated_at = CURRENT_TIMESTAMP
        WHERE session_id = ? AND anchor_id = ?
        """,
        [(state, session_id, anchor_id) for anchor_id in anchor_ids],
    )


def remove_verified_anchor_profiles(conn: sqlite3.Connection, session_id: str, anchor_ids: list[str]) -> None:
    if not anchor_ids:
        return
    conn.executemany(
        """
        UPDATE anchor_profiles
        SET state = 'ignored', updated_at = CURRENT_TIMESTAMP
        WHERE anchor_id = ?
          AND session_id != '__system__'
          AND state = 'verified'
        """,
        [(anchor_id,) for anchor_id in anchor_ids],
    )
    conn.executemany(
        """
        UPDATE anchor_profiles
        SET state = 'ignored', updated_at = CURRENT_TIMESTAMP
        WHERE session_id = ?
          AND anchor_id = ?
          AND state != 'system'
        """,
        [(session_id, anchor_id) for anchor_id in anchor_ids],
    )
    conn.executemany(
        """
        INSERT INTO anchor_profiles (session_id, anchor_id, state, updated_at)
        VALUES (?, ?, 'ignored', CURRENT_TIMESTAMP)
        ON CONFLICT(session_id, anchor_id) DO UPDATE SET
            state='ignored',
            updated_at=CURRENT_TIMESTAMP
        """,
        [(REMOVED_VERIFIED_ANCHOR_SESSION, anchor_id) for anchor_id in anchor_ids],
    )


def repair_anchor_profile_json(
    conn: sqlite3.Connection,
    session_id: str,
    anchor_ids: list[str],
    payload_builder,
) -> list[str]:
    """Reconstruct profile_json from binary columns for anchors where it is
    NULL or empty.  Returns the anchor_ids that could not be repaired.
    Callers should treat a non-empty return value as a hard failure."""
    if not anchor_ids:
        return []

    placeholders = ", ".join("?" for _ in anchor_ids)
    rows = conn.execute(
        f"""
        SELECT anchor_id, audio_type, category, subcategory, cluster_id,
               feature_space_version, extractor_version, feature_schema_json,
               medoid_vector, cluster_centroid, cluster_std,
               coherence_radius, n_reference_items, profile_json
        FROM anchor_profiles
        WHERE session_id = ? AND anchor_id IN ({placeholders})
        """,
        (session_id, *anchor_ids),
    ).fetchall()

    failed: list[str] = []
    to_update: list[tuple[str, str]] = []  

    for row in rows:
        anchor_id = str(row["anchor_id"] or "")

        existing = row["profile_json"]
        if existing:
            try:
                parsed = json.loads(existing)
                if isinstance(parsed, dict) and parsed:
                    continue
            except (json.JSONDecodeError, TypeError):
                pass

   
        medoid = vector_from_blob(row["medoid_vector"])
        centroid = vector_from_blob(row["cluster_centroid"])
        cluster_std = vector_from_blob(row["cluster_std"])

        if medoid is None or centroid is None:
            failed.append(anchor_id)
            continue

        if cluster_std is None or len(cluster_std) != len(medoid):
            cluster_std = [0.0] * len(medoid)

        schema_json = row["feature_schema_json"]
        if not schema_json:
            failed.append(anchor_id)
            continue
        try:
            vector_schema = json.loads(schema_json)
            if not isinstance(vector_schema, list) or not vector_schema:
                raise ValueError("empty schema")
        except (json.JSONDecodeError, ValueError):
            failed.append(anchor_id)
            continue

        coherence_radius = row["coherence_radius"]
        n_reference_items = row["n_reference_items"]
        if coherence_radius is None or n_reference_items is None:
            failed.append(anchor_id)
            continue

        payload = payload_builder(
            cluster_id=str(row["cluster_id"] or anchor_id),
            audio_type=str(row["audio_type"] or ""),
            category=str(row["category"] or ""),
            subcategory=str(row["subcategory"] or ""),
            medoid_vector=medoid,
            cluster_centroid=centroid,
            cluster_std=cluster_std,
            coherence_radius=float(coherence_radius),
            n_reference_items=int(n_reference_items),
        )
        to_update.append((json.dumps(payload), anchor_id))

    if to_update:
        conn.executemany(
            """
            UPDATE anchor_profiles
            SET profile_json = ?, updated_at = CURRENT_TIMESTAMP
            WHERE session_id = ? AND anchor_id = ?
            """,
            [(payload_json, session_id, aid) for payload_json, aid in to_update],
        )

    return failed


def seed_system_anchors(conn: sqlite3.Connection, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    conn.executemany(
        """
        INSERT INTO anchor_profiles (
            session_id, anchor_id, audio_type, category, subcategory, cluster_id,
            feature_space_version, extractor_version, feature_schema_json,
            medoid_vector, cluster_centroid, cluster_std, coherence_radius,
            n_reference_items, state, profile_json, updated_at
        )
        VALUES ('__system__', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'system', ?, CURRENT_TIMESTAMP)
        ON CONFLICT(session_id, anchor_id) DO UPDATE SET
            audio_type=excluded.audio_type,
            category=excluded.category,
            subcategory=excluded.subcategory,
            cluster_id=excluded.cluster_id,
            feature_space_version=excluded.feature_space_version,
            extractor_version=excluded.extractor_version,
            feature_schema_json=excluded.feature_schema_json,
            medoid_vector=excluded.medoid_vector,
            cluster_centroid=excluded.cluster_centroid,
            cluster_std=excluded.cluster_std,
            coherence_radius=excluded.coherence_radius,
            n_reference_items=excluded.n_reference_items,
            state='system',
            profile_json=excluded.profile_json,
            updated_at=CURRENT_TIMESTAMP
        """,
        [
            (
                row["anchor_id"],
                row["audio_type"],
                row["category"],
                row["subcategory"],
                row["cluster_id"],
                row["feature_space_version"],
                row["extractor_version"],
                row["feature_schema_json"],
                row["medoid_vector"],
                row["cluster_centroid"],
                row["cluster_std"],
                float(row["coherence_radius"] or 0.0),
                int(row["n_reference_items"] or 0),
                row["profile_json"],
            )
            for row in rows
        ]
    )
