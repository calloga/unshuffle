from __future__ import annotations

import csv
import hashlib
import json
import math
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterable, Iterator

from unshuffle.core import PlanRecord, parse_tags, plan_record_from_staging_row, tags_to_search_text
from unshuffle.core.hashing import is_fast_hash
from unshuffle.core.prefixes import common_filename_tokens_for_packs
from unshuffle.logic.tree_organization import (
    TreeOrganizationProfile,
    TreeOrganizationResolver,
    TreeRouteBuilder,
    semantic_profile_for_record_batches,
)
from gui.utils.constants import StagingColumn


DB_TABLE_COLUMNS = {
    StagingColumn.PACK: "pack",
    StagingColumn.FILENAME: "sample_name",
    StagingColumn.CATEGORY: "category",
    StagingColumn.SUBCATEGORY: "subcategory",
    StagingColumn.TAGS: "tags",
    StagingColumn.CONFIDENCE: "confidence",
    StagingColumn.PATH: "source_path",
    StagingColumn.TYPE: "audio_type",
}

LIGHTWEIGHT_RECORD_COLUMNS = (
    "row_id",
    "source_path",
    "sample_name",
    "pack",
    "category",
    "subcategory",
    "audio_type",
    "tags",
    "confidence",
    "duration",
    "hash",
    "fast_hash",
    "pack_candidates",
    "evidence_json",
    "feature_space_version",
    "feature_schema_json",
    "analysis_status",
    "analysis_tags_json",
    "preserved_root",
    "is_preserved",
)

TREE_RECORD_COLUMNS = LIGHTWEIGHT_RECORD_COLUMNS

MAP_RECORD_COLUMNS = (
    "row_id",
    "source_path",
    "audio_type",
    "category",
    "subcategory",
    "feature_vector",
    "evidence_json",
    "is_preserved",
)


class StagingRowsUpdateCanceled(RuntimeError):
    """Raised inside a staging transaction when its worker is canceled."""


@dataclass(frozen=True)
class StagingQuery:
    matched_ids: frozenset[int] | None = None
    audio_types: frozenset[str] | None = None
    show_non_audio_assets: bool = False
    show_duplicates: bool = True
    path_prefixes: tuple[str, ...] = ()
    confidence_min: float = 0.0
    confidence_max: float = 1.0
    column_filters: tuple[tuple[int, tuple[str, ...]], ...] = ()
    similarity_rows: frozenset[int] | None = None


def _json_tags(tags: object) -> str:
    return tags_to_search_text(parse_tags(tags))


def _normalize_path_prefix(path: object) -> str:
    value = str(path or "").replace("\\", "/").rstrip("/").lower()
    return value + "/" if value else ""


def _is_duplicate_shadow_row(row: dict[str, Any]) -> bool:
    try:
        evidence = json.loads(row.get("evidence_json") or "{}")
    except (TypeError, json.JSONDecodeError):
        return False
    shadow = evidence.get("duplicate_shadow") if isinstance(evidence, dict) else None
    return bool(isinstance(shadow, dict) and shadow.get("is_shadow"))


class StagingSessionStore:
    """DB-backed access layer for active staging session rows."""

    def __init__(self, db: Any, session_id: str):
        self.db = db
        self.session_id = str(session_id or "")
        self._custom_child_count_cache: OrderedDict[tuple[Any, ...], tuple[dict[str, Any], ...]] = OrderedDict()

    @property
    def conn(self):
        return getattr(self.db, "conn", self.db)

    def count(self, query: StagingQuery | None = None) -> int:
        where, params = self._where(query)
        row = self.conn.execute(
            f"SELECT COUNT(*) FROM staging_records WHERE {where}",
            params,
        ).fetchone()
        return int(row[0] if row is not None else 0)

    def filter_suggestion_values(self, *, limit_per_field: int = 500) -> dict[str, set[str]]:
        """Return compact search-completion values without hydrating session records."""
        limit = max(1, int(limit_per_field))
        values: dict[str, set[str]] = {}
        for prefix, column in (
            ("type", "audio_type"),
            ("category", "category"),
            ("subcategory", "subcategory"),
            ("packname", "pack"),
            ("name", "sample_name"),
        ):
            rows = self.conn.execute(
                f"SELECT DISTINCT {column} FROM staging_records "
                f"WHERE session_id = ? AND {column} IS NOT NULL AND {column} != '' "
                f"ORDER BY {column} COLLATE NOCASE LIMIT ?",
                (self.session_id, limit),
            )
            values[prefix] = {str(row[0]).strip() for row in rows if str(row[0] or "").strip()}
        tags: set[str] = set()
        rows = self.conn.execute(
            "SELECT tags FROM staging_records WHERE session_id = ? AND tags IS NOT NULL AND tags != ''",
            (self.session_id,),
        )
        for row in rows:
            tags.update(str(tag).strip() for tag in parse_tags(row[0]) if str(tag).strip())
            if len(tags) >= limit:
                break
        values["tag"] = tags
        return values

    def row_ids(
        self,
        query: StagingQuery | None = None,
        sort_column: int | StagingColumn | None = None,
        *,
        descending: bool = False,
    ) -> list[int]:
        where, params = self._where(query)
        order = self._order_by(sort_column, descending=descending)
        cursor = self.conn.execute(
            f"SELECT row_id FROM staging_records WHERE {where} {order}",
            params,
        )
        return [int(row[0]) for row in cursor.fetchall() if row[0] is not None]

    def rows_by_ids(self, row_ids: Iterable[int]) -> list[dict[str, Any]]:
        return self._rows_by_ids(row_ids, "*")

    def lightweight_rows_by_ids(self, row_ids: Iterable[int]) -> list[dict[str, Any]]:
        return self._rows_by_ids(row_ids, ", ".join(LIGHTWEIGHT_RECORD_COLUMNS))

    def iter_rows_by_ids(
        self,
        row_ids: Iterable[int],
        columns: str,
        *,
        batch_size: int = 500,
    ) -> Iterator[list[dict[str, Any]]]:
        ids = [int(row_id) for row_id in row_ids]
        size = max(1, min(int(batch_size), 900))
        for offset in range(0, len(ids), size):
            chunk = ids[offset : offset + size]
            placeholders = ", ".join("?" for _ in chunk)
            cursor = self.conn.execute(
                f"SELECT {columns} FROM staging_records "
                f"WHERE session_id = ? AND row_id IN ({placeholders})",
                [self.session_id, *chunk],
            )
            by_id = {int(row["row_id"]): dict(row) for row in cursor}
            yield [by_id[row_id] for row_id in chunk if row_id in by_id]

    def acoustic_state_rows(self) -> list[dict[str, Any]]:
        cursor = self.conn.execute(
            """
            SELECT
                row_id,
                source_path,
                hash,
                duration,
                audio_type,
                category,
                subcategory,
                LENGTH(feature_vector) AS vector_len
            FROM staging_records
            WHERE session_id = ?
            ORDER BY row_id ASC, id ASC
            """,
            (self.session_id,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def acoustic_state_digest(self) -> str:
        import hashlib

        digest = hashlib.sha1()
        digest.update(b"acoustic-state-v4\0")
        digest.update(self.session_id.encode("utf-8", errors="surrogatepass"))
        cursor = self.conn.execute(
            """
            SELECT row_id, source_path, hash, duration, audio_type, category, subcategory,
                   LENGTH(feature_vector) AS vector_len
            FROM staging_records
            WHERE session_id = ?
            ORDER BY row_id ASC, id ASC
            """,
            (self.session_id,),
        )
        count = 0
        for row in cursor:
            count += 1
            values = (
                int(row[0] or 0),
                str(row[1] or "").replace("\\", "/"),
                str(row[2] or ""),
                f"{float(row[3] or 0.0):.6f}",
                str(row[4] or ""),
                str(row[5] or ""),
                str(row[6] or ""),
                int(row[7] or 0),
            )
            for value in values:
                digest.update(str(value).encode("utf-8", errors="surrogatepass"))
                digest.update(b"\0")
        digest.update(str(count).encode("ascii"))
        return digest.hexdigest() if count else ""

    def scan_dedupe_index(self) -> dict[str, Any]:
        """Build the append-scan identity index without hydrating PlanRecord DTOs."""
        index: dict[str, Any] = {
            "full_hashes": set(),
            "full_hash_records": {},
            "fast_hash_records": {},
        }
        cursor = self.conn.execute(
            "SELECT source_path, hash, fast_hash, evidence_json, pack, category, subcategory, "
            "audio_type, confidence, duration, pack_candidates, feature_space_version, "
            "feature_schema_json, analysis_status, analysis_tags_json "
            "FROM staging_records WHERE session_id = ?",
            (self.session_id,),
        )
        for row in cursor:
            data = dict(row)
            if _is_duplicate_shadow_row(data):
                continue
            file_hash = str(data.get("hash") or "")
            fast_hash = str(data.get("fast_hash") or "")
            if not fast_hash and is_fast_hash(file_hash):
                fast_hash = file_hash
            try:
                pack_candidates = json.loads(data.get("pack_candidates") or "[]")
            except (TypeError, json.JSONDecodeError):
                pack_candidates = []
            record = SimpleNamespace(
                source_path=Path(str(data.get("source_path") or "")),
                hash=file_hash or None,
                fast_hash=fast_hash or None,
                is_duplicate_shadow=False,
                pack=str(data.get("pack") or ""),
                category=str(data.get("category") or "Uncategorized"),
                subcategory=str(data.get("subcategory") or ""),
                audio_type=str(data.get("audio_type") or "Oneshots"),
                confidence=str(data.get("confidence") or "0"),
                duration=data.get("duration"),
                pack_candidates=pack_candidates,
                feature_space_version=data.get("feature_space_version"),
                feature_schema_json=data.get("feature_schema_json"),
                analysis_status=data.get("analysis_status"),
                analysis_tags_json=data.get("analysis_tags_json"),
            )
            if file_hash and not is_fast_hash(file_hash):
                index["full_hashes"].add(file_hash)
                index["full_hash_records"].setdefault(file_hash, record)
            if fast_hash:
                index["fast_hash_records"].setdefault(fast_hash, []).append(record)
        return index

    def generated_tag_count(self, tag: str) -> int:
        needle = str(tag or "").strip().lower()
        if not needle:
            return 0
        cursor = self.conn.execute(
            """
            SELECT COUNT(*)
            FROM staging_records
            WHERE session_id = ?
              AND LOWER(COALESCE(tags, '')) LIKE ?
            """,
            (self.session_id, f"%{needle}%"),
        )
        return int(cursor.fetchone()[0])

    def tagged_row_ids(self, tag: str) -> set[int]:
        needle = str(tag or "").strip().lower()
        if not needle:
            return set()
        cursor = self.conn.execute(
            "SELECT row_id FROM staging_records "
            "WHERE session_id = ? AND LOWER(COALESCE(tags, '')) LIKE ?",
            (self.session_id, f"%{needle}%"),
        )
        return {int(row[0]) for row in cursor if row[0] is not None}

    def duplicate_shadow_row_ids(self) -> set[int]:
        cursor = self.conn.execute(
            """
            SELECT row_id
            FROM staging_records
            WHERE session_id = ?
              AND COALESCE(
                    CASE WHEN json_valid(evidence_json)
                      THEN json_extract(evidence_json, '$.duplicate_shadow.is_shadow') ELSE 0 END,
                    0
                  ) = 1
            """,
            (self.session_id,),
        )
        return {int(row[0]) for row in cursor if row[0] is not None}

    def has_any_tags(self, tags: Iterable[str]) -> bool:
        needles = [str(tag or "").strip().lower() for tag in tags if str(tag or "").strip()]
        if not needles:
            return False
        clauses = " OR ".join("LOWER(COALESCE(tags, '')) LIKE ?" for _ in needles)
        row = self.conn.execute(
            f"SELECT 1 FROM staging_records WHERE session_id = ? AND ({clauses}) LIMIT 1",
            [self.session_id, *(f"%{needle}%" for needle in needles)],
        ).fetchone()
        return row is not None

    def _rows_by_ids(self, row_ids: Iterable[int], columns: str) -> list[dict[str, Any]]:
        started = time.perf_counter()
        ids = [int(row_id) for row_id in row_ids]
        rows = [row for batch in self.iter_rows_by_ids(ids, columns) for row in batch]
        logging.getLogger("unshuffle").debug(
            "Staging row hydration: requested=%d returned=%d full=%s elapsed=%.3fs",
            len(ids),
            len(rows),
            columns.strip() == "*",
            time.perf_counter() - started,
        )
        return rows

    def record_by_row_id(self, row_id: int) -> PlanRecord | None:
        rows = self.rows_by_ids([row_id])
        if not rows:
            return None
        return plan_record_from_staging_row(rows[0], parse_tags)

    def row_id_for_source_path(self, source_path: Path | str) -> int | None:
        normalized = Path(source_path).as_posix()
        row = self.conn.execute(
            """
            SELECT row_id FROM staging_records
            WHERE session_id = ? AND REPLACE(source_path, '\\', '/') = ?
            LIMIT 1
            """,
            (self.session_id, normalized),
        ).fetchone()
        return int(row[0]) if row is not None else None

    def iter_records(
        self,
        batch_size: int = 1000,
        query: StagingQuery | None = None,
        *,
        include_duplicate_shadows: bool = True,
    ) -> Iterator[list[PlanRecord]]:
        row_ids = self.row_ids(query)
        for offset in range(0, len(row_ids), max(1, int(batch_size))):
            records = [
                plan_record_from_staging_row(row, parse_tags)
                for row in self.rows_by_ids(row_ids[offset:offset + batch_size])
                if include_duplicate_shadows or not _is_duplicate_shadow_row(row)
            ]
            if records:
                yield records

    def count_buildable_records(self) -> int:
        row = self.conn.execute(
            """
            SELECT COUNT(*) FROM staging_records
            WHERE session_id = ?
              AND COALESCE(CASE WHEN json_valid(COALESCE(evidence_json, ''))
                  THEN json_extract(evidence_json, '$.duplicate_shadow.is_shadow') ELSE 0 END, 0) != 1
            """,
            (self.session_id,),
        ).fetchone()
        return int(row[0] if row is not None else 0)

    def common_filename_tokens_by_pack(self) -> dict[str, frozenset[str]]:
        cursor = self.conn.execute(
            """
            SELECT pack, source_path FROM staging_records
            WHERE session_id = ?
              AND COALESCE(is_preserved, 0) = 0
              AND COALESCE(audio_type, '') NOT IN ('Non-Audio Assets', 'Utility')
              AND COALESCE(CASE WHEN json_valid(COALESCE(evidence_json, ''))
                  THEN json_extract(evidence_json, '$.duplicate_shadow.is_shadow') ELSE 0 END, 0) != 1
            ORDER BY row_id ASC, id ASC
            """,
            (self.session_id,),
        )
        return common_filename_tokens_for_packs(
            (str(row[0] or ""), str(row[1] or ""))
            for row in cursor
        )

    def iter_buildable_records(self, batch_size: int = 1000) -> Iterator[list[PlanRecord]]:
        cursor = self.conn.execute(
            """
            SELECT * FROM staging_records
            WHERE session_id = ?
              AND COALESCE(CASE WHEN json_valid(COALESCE(evidence_json, ''))
                  THEN json_extract(evidence_json, '$.duplicate_shadow.is_shadow') ELSE 0 END, 0) != 1
            ORDER BY row_id ASC, id ASC
            """,
            (self.session_id,),
        )
        size = max(1, int(batch_size))
        while True:
            rows = cursor.fetchmany(size)
            if not rows:
                return
            yield [plan_record_from_staging_row(dict(row), parse_tags) for row in rows]

    def iter_tree_records(
        self,
        query: StagingQuery | None = None,
        *,
        batch_size: int = 1000,
    ) -> Iterator[list[PlanRecord]]:
        where, params = self._where(query)
        cursor = self.conn.execute(
            f"SELECT {', '.join(TREE_RECORD_COLUMNS)} FROM staging_records "
            f"WHERE {where} ORDER BY row_id ASC, id ASC",
            params,
        )
        size = max(1, int(batch_size))
        while True:
            rows = cursor.fetchmany(size)
            if not rows:
                break
            yield [plan_record_from_staging_row(dict(row), parse_tags) for row in rows]

    def iter_tree_records_by_ids(
        self,
        row_ids: Iterable[int],
        *,
        batch_size: int = 1000,
    ) -> Iterator[list[PlanRecord]]:
        for rows in self.iter_rows_by_ids(
            row_ids,
            ", ".join(TREE_RECORD_COLUMNS),
            batch_size=batch_size,
        ):
            yield [plan_record_from_staging_row(row, parse_tags) for row in rows]

    def default_tree_group_values(
        self,
        *,
        confidence_floor: float = 0.0,
        confidence_filter_enabled: bool = True,
    ) -> set[tuple[str, str, str]]:
        values: set[tuple[str, str, str]] = set()
        for batch in self.iter_tree_records(None, batch_size=1000):
            for record in batch:
                audio_type = str(record.audio_type or "").strip()
                category = str(record.category or "").strip()
                subcategory = str(record.subcategory or "").strip()
                if confidence_filter_enabled:
                    try:
                        low_confidence = (
                            float(record.confidence) < confidence_floor
                            and not record.is_manual
                            and not record.is_hands_off
                        )
                    except (TypeError, ValueError):
                        low_confidence = False
                    if low_confidence:
                        category = "Uncategorized"
                        subcategory = ""
                if audio_type == "Non-Audio Assets":
                    audio_type = "Utility"
                values.add((audio_type, category, subcategory or "Other"))
        return values

    @staticmethod
    def custom_tree_projection_signature(
        profile: TreeOrganizationProfile,
        levels: list[tuple[str, str]],
        *,
        confidence_floor: float,
        confidence_filter_enabled: bool,
    ) -> str:
        payload = {
            "profile": {
                "root_node_id": profile.root_node_id,
                "nodes": [node.to_dict() for node in profile.nodes],
            },
            "levels": levels,
            "confidence_floor": float(confidence_floor),
            "confidence_filter_enabled": bool(confidence_filter_enabled),
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def ensure_custom_tree_projection(
        self,
        profile: TreeOrganizationProfile,
        levels: list[tuple[str, str]],
        *,
        confidence_floor: float = 0.0,
        confidence_filter_enabled: bool = True,
        progress_callback=None,
        interrupted_check=None,
    ) -> str:
        signature = self.custom_tree_projection_signature(
            profile,
            levels,
            confidence_floor=confidence_floor,
            confidence_filter_enabled=confidence_filter_enabled,
        )
        existing = self.conn.execute(
            """
            SELECT COUNT(DISTINCT row_id) FROM custom_tree_memberships
            WHERE session_id = ? AND profile_id = ? AND projection_signature = ?
            """,
            (self.session_id, profile.id, signature),
        ).fetchone()
        staging_count = self.count(None)
        projected_count = int(existing[0] if existing is not None else 0)
        if projected_count == staging_count:
            return signature

        def check_interrupted() -> None:
            if callable(interrupted_check) and interrupted_check():
                raise StagingRowsUpdateCanceled()

        validation = TreeOrganizationResolver().validate_profile(profile, [])
        if not validation.valid:
            raise ValueError("\n".join(validation.blocking_messages))

        processed = 0
        route_count = staging_count

        def semantic_batches():
            nonlocal processed
            for batch in self.iter_tree_records(None, batch_size=1000):
                check_interrupted()
                yield batch
                processed += len(batch)
                if callable(progress_callback):
                    progress_callback(processed, staging_count + route_count)

        semantic_profile = semantic_profile_for_record_batches(profile, semantic_batches)
        missing_row_ids: list[int] | None = None
        if projected_count:
            missing_row_ids = [
                int(row[0])
                for row in self.conn.execute(
                    """
                    SELECT s.row_id
                    FROM staging_records AS s
                    WHERE s.session_id = ?
                      AND NOT EXISTS (
                          SELECT 1
                          FROM custom_tree_memberships AS m
                          WHERE m.session_id = s.session_id
                            AND m.profile_id = ?
                            AND m.projection_signature = ?
                            AND m.row_id = s.row_id
                      )
                    ORDER BY s.row_id ASC, s.id ASC
                    """,
                    (self.session_id, profile.id, signature),
                )
            ]
            route_count = len(missing_row_ids)

        def route_batches():
            nonlocal processed
            source = (
                self.iter_tree_records_by_ids(missing_row_ids or (), batch_size=1000)
                if missing_row_ids is not None
                else self.iter_tree_records(None, batch_size=1000)
            )
            for batch in source:
                check_interrupted()
                yield batch
                processed += len(batch)
                if callable(progress_callback):
                    progress_callback(processed, staging_count + route_count)

        if callable(progress_callback):
            progress_callback(0, staging_count + route_count)
        builder = TreeRouteBuilder()
        insert_sql = """
            INSERT OR REPLACE INTO custom_tree_memberships (
                session_id, profile_id, projection_signature, route_key,
                parent_route_key, label, node_type, semantic_fields_json,
                source_node_id, source_node_type, read_only, residual, sort_order, depth, row_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        pending: list[tuple[Any, ...]] = []
        with self.conn:
            self._invalidate_custom_tree_cache(profile.id)
            if not projected_count:
                self.conn.execute(
                    "DELETE FROM custom_tree_memberships "
                    "WHERE session_id = ? AND profile_id = ? AND projection_signature = ?",
                    (self.session_id, profile.id, signature),
                )
            for batch in route_batches():
                check_interrupted()
                routes = builder.iter_routes(
                    batch,
                    semantic_profile,
                    levels,
                    presentation_mode=True,
                    confidence_floor=confidence_floor,
                    confidence_filter_enabled=confidence_filter_enabled,
                    resolve_semantics=False,
                )
                for route in routes:
                    row_id = getattr(route.record, "staging_row_id", None)
                    if row_id is None:
                        continue
                    parent_key = ""
                    identity: list[tuple[str, str, str]] = []
                    fields: dict[str, str] = {}
                    for depth, part in enumerate(route.parts, 1):
                        identity.append((part.kind, part.label, part.source_node_id or ""))
                        route_key = json.dumps(identity, separators=(",", ":"))
                        fields.update(part.fields)
                        pending.append(
                            (
                                self.session_id,
                                profile.id,
                                signature,
                                route_key,
                                parent_key,
                                part.label,
                                part.kind,
                                json.dumps(fields, sort_keys=True),
                                part.source_node_id,
                                part.source_node_type,
                                1 if part.read_only else 0,
                                1 if part.residual else 0,
                                int(part.sort_order),
                                depth,
                                int(row_id),
                            )
                        )
                        parent_key = route_key
                        if len(pending) >= 2000:
                            check_interrupted()
                            self.conn.executemany(insert_sql, pending)
                            pending.clear()
            if pending:
                check_interrupted()
                self.conn.executemany(insert_sql, pending)
        return signature

    def preview_custom_tree_node_counts(
        self,
        profile: TreeOrganizationProfile,
        levels: list[tuple[str, str]],
        *,
        confidence_floor: float = 0.0,
        confidence_filter_enabled: bool = True,
    ) -> dict[str, int]:
        """Stream routed counts for the editor without writing a projection."""
        validation = TreeOrganizationResolver().validate_profile(profile, [])
        if not validation.valid:
            raise ValueError("\n".join(validation.blocking_messages))

        record_batches = lambda: self.iter_tree_records(None, batch_size=1000)
        semantic_profile = semantic_profile_for_record_batches(profile, record_batches)
        builder = TreeRouteBuilder()
        counts: dict[str, int] = {}
        total = 0
        for batch in record_batches():
            total += len(batch)
            for route in builder.iter_routes(
                batch,
                semantic_profile,
                levels,
                presentation_mode=True,
                confidence_floor=confidence_floor,
                confidence_filter_enabled=confidence_filter_enabled,
                resolve_semantics=False,
            ):
                node_ids = {
                    str(part.source_node_id)
                    for part in route.parts
                    if part.source_node_id
                }
                for node_id in node_ids:
                    counts[node_id] = counts.get(node_id, 0) + 1
        counts[profile.root_node_id] = total
        return counts

    def clear_custom_tree_projections(
        self,
        profile_id: str | None = None,
        *,
        keep_signature: str = "",
    ) -> None:
        if profile_id and keep_signature:
            self.conn.execute(
                "DELETE FROM custom_tree_memberships "
                "WHERE session_id = ? AND profile_id = ? AND projection_signature != ?",
                (self.session_id, profile_id, keep_signature),
            )
        elif profile_id:
            self.conn.execute(
                "DELETE FROM custom_tree_memberships WHERE session_id = ? AND profile_id = ?",
                (self.session_id, profile_id),
            )
        else:
            self.conn.execute(
                "DELETE FROM custom_tree_memberships WHERE session_id = ?",
                (self.session_id,),
            )
        self._invalidate_custom_tree_cache(profile_id, keep_signature=keep_signature)
        self.conn.commit()

    def custom_tree_child_counts(
        self,
        profile_id: str,
        signature: str,
        parent_route_key: str,
        query: StagingQuery | None = None,
    ) -> list[dict[str, Any]]:
        cache_key = (str(profile_id), str(signature), str(parent_route_key), query)
        cached = self._custom_child_count_cache.get(cache_key)
        if cached is not None:
            self._custom_child_count_cache.move_to_end(cache_key)
            return [dict(row) for row in cached]
        where, params = self._where(query, table_alias="s")
        cursor = self.conn.execute(
            f"""
            SELECT
                m.route_key,
                m.label,
                m.node_type,
                m.semantic_fields_json,
                m.source_node_id,
                m.source_node_type,
                m.read_only,
                m.residual,
                m.sort_order,
                MIN(m.depth) AS depth,
                COUNT(DISTINCT m.row_id) AS count
            FROM custom_tree_memberships AS m INDEXED BY idx_custom_tree_memberships_parent
            JOIN staging_records AS s INDEXED BY idx_staging_records_session_row
              ON s.session_id = m.session_id AND s.row_id = m.row_id
            WHERE m.session_id = ?
              AND m.profile_id = ?
              AND m.projection_signature = ?
              AND m.parent_route_key = ?
              AND {where}
            GROUP BY
                m.route_key, m.label, m.node_type, m.semantic_fields_json,
                m.source_node_id, m.source_node_type, m.read_only, m.residual, m.sort_order
            ORDER BY depth ASC, m.sort_order ASC, m.label COLLATE NOCASE
            """,
            [self.session_id, profile_id, signature, parent_route_key, *params],
        )
        rows = tuple(dict(row) for row in cursor)
        self._custom_child_count_cache[cache_key] = rows
        self._custom_child_count_cache.move_to_end(cache_key)
        while len(self._custom_child_count_cache) > 512:
            self._custom_child_count_cache.popitem(last=False)
        return [dict(row) for row in rows]

    def custom_tree_records(
        self,
        profile_id: str,
        signature: str,
        route_key: str,
        query: StagingQuery | None = None,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[PlanRecord]:
        where, params = self._where(query, table_alias="s")
        limit_sql = ""
        if limit is not None:
            limit_sql = " LIMIT ? OFFSET ?"
            params.extend([max(0, int(limit)), max(0, int(offset))])
        cursor = self.conn.execute(
            f"""
            SELECT s.*
            FROM custom_tree_memberships AS m INDEXED BY idx_custom_tree_memberships_parent
            JOIN staging_records AS s INDEXED BY idx_staging_records_session_row
              ON s.session_id = m.session_id AND s.row_id = m.row_id
            WHERE m.session_id = ?
              AND m.profile_id = ?
              AND m.projection_signature = ?
              AND m.route_key = ?
              AND {where}
            ORDER BY s.sample_name COLLATE NOCASE, s.row_id ASC{limit_sql}
            """,
            [self.session_id, profile_id, signature, route_key, *params],
        )
        return [plan_record_from_staging_row(dict(row), parse_tags) for row in cursor]

    def custom_tree_record_count(
        self,
        profile_id: str,
        signature: str,
        route_key: str,
        query: StagingQuery | None = None,
    ) -> int:
        where, params = self._where(query, table_alias="s")
        row = self.conn.execute(
            f"""
            SELECT COUNT(DISTINCT m.row_id)
            FROM custom_tree_memberships AS m INDEXED BY idx_custom_tree_memberships_parent
            JOIN staging_records AS s INDEXED BY idx_staging_records_session_row
              ON s.session_id = m.session_id AND s.row_id = m.row_id
            WHERE m.session_id = ?
              AND m.profile_id = ?
              AND m.projection_signature = ?
              AND m.route_key = ?
              AND {where}
            """,
            [self.session_id, profile_id, signature, route_key, *params],
        ).fetchone()
        return int(row[0] if row is not None else 0)

    def custom_tree_node_counts(
        self,
        profile_id: str,
        signature: str,
    ) -> dict[str, int]:
        cursor = self.conn.execute(
            """
            SELECT source_node_id, COUNT(DISTINCT row_id) AS count
            FROM custom_tree_memberships
            WHERE session_id = ?
              AND profile_id = ?
              AND projection_signature = ?
              AND COALESCE(source_node_id, '') != ''
            GROUP BY source_node_id
            """,
            (self.session_id, profile_id, signature),
        )
        return {str(row["source_node_id"]): int(row["count"] or 0) for row in cursor}

    def update_row(self, row_id: int, fields: dict[str, Any], *, commit: bool = True) -> None:
        if not fields:
            return
        self._invalidate_custom_tree_cache()
        assignments = []
        values = []
        for key, value in fields.items():
            assignments.append(f"{key} = ?")
            values.append(value)
        self.conn.execute(
            f"UPDATE staging_records SET {', '.join(assignments)} WHERE session_id = ? AND row_id = ?",
            [*values, self.session_id, int(row_id)],
        )
        if commit:
            self.conn.commit()

    def update_rows(
        self,
        updates: Iterable[tuple[int, dict[str, Any]]],
        *,
        batch_size: int = 500,
        progress_callback=None,
        interrupted_check=None,
    ) -> None:
        grouped: dict[tuple[str, ...], list[tuple[Any, ...]]] = {}
        for row_id, fields in updates:
            if not fields:
                continue
            columns = tuple(fields)
            grouped.setdefault(columns, []).append(
                (*[fields[column] for column in columns], self.session_id, int(row_id))
            )
        if not grouped:
            return
        self._invalidate_custom_tree_cache()
        completed = 0
        overall_total = sum(len(values) for values in grouped.values())
        with self.conn:
            for columns, values in grouped.items():
                assignments = ", ".join(f"{column} = ?" for column in columns)
                sql = f"UPDATE staging_records SET {assignments} WHERE session_id = ? AND row_id = ?"
                size = max(1, int(batch_size or 500))
                for offset in range(0, len(values), size):
                    if callable(interrupted_check) and interrupted_check():
                        raise StagingRowsUpdateCanceled()
                    self.conn.executemany(sql, values[offset : offset + size])
                    completed += min(size, len(values) - offset)
                    if callable(progress_callback):
                        progress_callback(completed, overall_total)
                    if callable(interrupted_check) and interrupted_check():
                        raise StagingRowsUpdateCanceled()

    def duplicate_shadow_records_for(
        self,
        canonical_records: Iterable[PlanRecord],
    ) -> list[tuple[PlanRecord, PlanRecord]]:
        """Return in-session shadows linked to the supplied canonical records."""
        canonical_by_path = {
            str(rec.source_path).replace("\\", "/").rstrip("/").lower(): rec
            for rec in canonical_records
            if getattr(rec, "is_duplicate_shadow", False) is not True
        }
        paths = list(canonical_by_path)
        if not paths:
            return []

        matches: list[tuple[PlanRecord, PlanRecord]] = []
        columns = ", ".join(LIGHTWEIGHT_RECORD_COLUMNS)
        fast_hashes = sorted({
            str(getattr(rec, "fast_hash", None) or "")
            for rec in canonical_by_path.values()
            if getattr(rec, "fast_hash", None)
        })
        rows: dict[int, dict[str, Any]] = {}
        for offset in range(0, len(fast_hashes), 400):
            chunk = fast_hashes[offset : offset + 400]
            placeholders = ", ".join("?" for _ in chunk)
            cursor = self.conn.execute(
                f"""
                SELECT {columns}
                FROM staging_records INDEXED BY idx_staging_records_fast_hash
                WHERE session_id = ?
                  AND fast_hash IN ({placeholders})
                  AND COALESCE(CASE WHEN json_valid(COALESCE(evidence_json, ''))
                      THEN json_extract(evidence_json, '$.duplicate_shadow.is_shadow') ELSE 0 END, 0) = 1
                ORDER BY row_id ASC, id ASC
                """,
                [self.session_id, *chunk],
            )
            rows.update((int(row["row_id"]), dict(row)) for row in cursor)

        fallback_paths = [
            path for path, rec in canonical_by_path.items()
            if not getattr(rec, "fast_hash", None)
        ]
        for offset in range(0, len(fallback_paths), 400):
            chunk = fallback_paths[offset : offset + 400]
            placeholders = ", ".join("?" for _ in chunk)
            cursor = self.conn.execute(
                f"""
                SELECT {columns}
                FROM staging_records
                WHERE session_id = ?
                  AND COALESCE(CASE WHEN json_valid(COALESCE(evidence_json, ''))
                      THEN json_extract(evidence_json, '$.duplicate_shadow.is_shadow') ELSE 0 END, 0) = 1
                  AND LOWER(REPLACE(json_extract(
                      evidence_json, '$.duplicate_shadow.duplicate_of_path'
                  ), '\\', '/')) IN ({placeholders})
                ORDER BY row_id ASC, id ASC
                """,
                [self.session_id, *chunk],
            )
            rows.update((int(row["row_id"]), dict(row)) for row in cursor)

        for data in rows.values():
            shadow = plan_record_from_staging_row(data, parse_tags)
            key = str(shadow.duplicate_of_path or "").replace("\\", "/").rstrip("/").lower()
            canonical = canonical_by_path.get(key)
            if canonical is not None:
                matches.append((canonical, shadow))
        return matches

    def has_custom_tree_projection(self, profile_id: str, signature: str) -> bool:
        row = self.conn.execute(
            """
            SELECT COUNT(DISTINCT row_id)
            FROM custom_tree_memberships
            WHERE session_id = ? AND profile_id = ? AND projection_signature = ?
            """,
            (self.session_id, str(profile_id), str(signature)),
        ).fetchone()
        return int(row[0] if row is not None else 0) == self.count(None)

    def _invalidate_custom_tree_cache(
        self,
        profile_id: str | None = None,
        *,
        keep_signature: str = "",
    ) -> None:
        if not profile_id:
            self._custom_child_count_cache.clear()
            return
        profile_key = str(profile_id)
        retained_signature = str(keep_signature or "")
        for cache_key in list(self._custom_child_count_cache):
            cached_profile = str(cache_key[0])
            cached_signature = str(cache_key[1])
            if cached_profile != profile_key:
                continue
            if retained_signature and cached_signature == retained_signature:
                continue
            self._custom_child_count_cache.pop(cache_key, None)

    def update_record(self, row_id: int, record: PlanRecord, *, commit: bool = True) -> None:
        evidence = dict(getattr(record, "evidence", {}) or {})
        if getattr(record, "is_duplicate_shadow", False) is True:
            evidence["duplicate_shadow"] = {
                "is_shadow": True,
                "duplicate_of_hash": getattr(record, "duplicate_of_hash", None),
                "duplicate_of_path": str(getattr(record, "duplicate_of_path", "")) if getattr(record, "duplicate_of_path", None) else None,
            }
        else:
            evidence.pop("duplicate_shadow", None)
        self.update_row(
            row_id,
            {
                "sample_name": record.source_path.name,
                "source_path": str(record.source_path),
                "pack": record.pack,
                "category": record.category,
                "subcategory": record.subcategory or "",
                "audio_type": record.audio_type,
                "tags": _json_tags(record.tags),
                "confidence": record.confidence,
                "duration": record.duration,
                "hash": record.hash or "",
                "fast_hash": getattr(record, "fast_hash", None),
                "pack_candidates": json.dumps(getattr(record, "pack_candidates", []) or []),
                "evidence_json": json.dumps(evidence, default=str),
                "feature_vector": getattr(record, "feature_vector", None) or getattr(record, "acoustic_vector", None),
                "feature_space_version": getattr(record, "feature_space_version", None),
                "feature_schema_json": getattr(record, "feature_schema_json", None),
                "analysis_status": getattr(record, "analysis_status", None),
                "analysis_tags_json": getattr(record, "analysis_tags_json", None),
                "preserved_root": str(record.preserved_root) if record.preserved_root else None,
                "is_preserved": 1 if record.is_preserved else 0,
            },
            commit=commit,
        )

    def delete_source_root(self, root: Path) -> int:
        self._invalidate_custom_tree_cache()
        root_text = Path(root).resolve().as_posix().rstrip("/")
        pattern = root_text.replace("!", "!!").replace("%", "!%").replace("_", "!_") + "/%"
        cursor = self.conn.execute(
            """
            DELETE FROM staging_records
            WHERE session_id = ?
              AND (
                REPLACE(source_path, '\\', '/') = ?
                OR REPLACE(source_path, '\\', '/') LIKE ? ESCAPE '!'
              )
            """,
            (self.session_id, root_text, pattern),
        )
        return int(getattr(cursor, "rowcount", 0) or 0)

    def promote_duplicate_shadows_after_root_removal(self, root: Path) -> int:
        root_prefix = Path(root).resolve().as_posix().rstrip("/").lower()
        rows = self.conn.execute(
            "SELECT row_id, source_path, tags, hash, fast_hash, evidence_json FROM staging_records WHERE session_id = ? ORDER BY row_id ASC, id ASC",
            (self.session_id,),
        ).fetchall()
        promoted = 0
        shadow_groups: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            data = dict(row)
            try:
                evidence = json.loads(data.get("evidence_json") or "{}")
            except (TypeError, json.JSONDecodeError):
                continue
            shadow = evidence.get("duplicate_shadow") if isinstance(evidence, dict) else None
            if not isinstance(shadow, dict) or not shadow.get("is_shadow"):
                continue
            duplicate_of_path = str(shadow.get("duplicate_of_path") or "").replace("\\", "/").lower()
            if not (duplicate_of_path == root_prefix or duplicate_of_path.startswith(root_prefix + "/")):
                continue
            key = str(shadow.get("duplicate_of_hash") or data.get("hash") or data.get("fast_hash") or data.get("row_id"))
            shadow_groups.setdefault(key, []).append(data)

        for key, group in shadow_groups.items():
            if not group:
                continue
            chosen = group[0]
            try:
                evidence = json.loads(chosen.get("evidence_json") or "{}")
            except (TypeError, json.JSONDecodeError):
                evidence = {}
            evidence.pop("duplicate_shadow", None)
            tags = parse_tags(chosen.get("tags") or "")
            tags = [tag for tag in tags if str(tag).strip().lower() != "duplicate"]
            self.update_row(
                int(chosen["row_id"]),
                {
                    "tags": tags_to_search_text(tags),
                    "evidence_json": json.dumps(evidence, default=str),
                },
            )
            promoted += 1
            promoted_path = chosen.get("source_path")
            for other in group[1:]:
                try:
                    other_evidence = json.loads(other.get("evidence_json") or "{}")
                except (TypeError, json.JSONDecodeError):
                    other_evidence = {}
                other_evidence["duplicate_shadow"] = {
                    "is_shadow": True,
                    "duplicate_of_hash": key,
                    "duplicate_of_path": promoted_path,
                }
                self.update_row(int(other["row_id"]), {"evidence_json": json.dumps(other_evidence, default=str)})
        return promoted

    def export_csv(self, path: Path, query: StagingQuery | None = None) -> Path:
        with open(path, "w", newline="", encoding="utf-8") as file_handle:
            writer = csv.DictWriter(
                file_handle,
                fieldnames=["source_directory", "source_filename", "pack", "category", "subcategory", "audio_type", "tags"],
            )
            writer.writeheader()
            for batch in self.iter_records(batch_size=1500, query=query):
                for rec in batch:
                    writer.writerow(
                        {
                            "source_directory": str(rec.source_path.parent),
                            "source_filename": rec.source_path.name,
                            "pack": rec.pack,
                            "category": rec.category,
                            "subcategory": rec.subcategory or "",
                            "audio_type": rec.audio_type,
                            "tags": tags_to_search_text(rec.tags),
                        }
                    )
        return path

    def distinct_values(self, column: int | StagingColumn, limit: int = 5000) -> list[str]:
        staging_column = StagingColumn(column)
        db_col = DB_TABLE_COLUMNS.get(staging_column)
        if not db_col:
            return []
        cursor = self.conn.execute(
            f"""
            SELECT DISTINCT {db_col}
            FROM staging_records
            WHERE session_id = ? AND {db_col} IS NOT NULL AND TRIM(CAST({db_col} AS TEXT)) != ''
            ORDER BY {db_col} COLLATE NOCASE
            LIMIT ?
            """,
            (self.session_id, int(limit)),
        )
        values = [str(row[0]) for row in cursor.fetchall()]
        if staging_column == StagingColumn.TAGS:
            return sorted(
                {
                    str(tag).strip()
                    for raw_value in values
                    for tag in parse_tags(raw_value)
                    if str(tag).strip()
                },
                key=str.casefold,
            )[: int(limit)]
        return values

    def map_candidate_rows(self, *, audio_type: str = "", category: str = "", limit: int = 10000, priority_row_ids: Iterable[int] = ()) -> list[dict[str, Any]]:
        """Return a deterministic, category-balanced map sample.

        Taking the first ``limit`` rows in taxonomy order made whole categories
        disappear from capped maps.  The map needs broad coverage first; dense
        categories can contribute the remaining capacity afterwards.
        """
        priority = [int(row_id) for row_id in priority_row_ids]
        clauses = ["session_id = ?", "feature_vector IS NOT NULL", "COALESCE(is_preserved, 0) = 0"]
        params: list[Any] = [self.session_id]
        if audio_type:
            clauses.append("audio_type = ?")
            params.append(audio_type)
        if category:
            clauses.append("category = ?")
            params.append(category)
        where = " AND ".join(clauses)
        rows: dict[int, dict[str, Any]] = {}
        if priority:
            placeholders = ", ".join("?" for _ in priority)
            cursor = self.conn.execute(
                f"SELECT {', '.join(MAP_RECORD_COLUMNS)} FROM staging_records "
                f"WHERE session_id = ? AND row_id IN ({placeholders})",
                [self.session_id, *priority],
            )
            rows.update({int(row["row_id"]): dict(row) for row in cursor.fetchall()})
        remaining = max(0, int(limit) - len(rows))
        if remaining:
            groups_cursor = self.conn.execute(
                f"""
                SELECT audio_type, category, COUNT(*) AS count
                FROM staging_records
                WHERE {where}
                GROUP BY audio_type, category
                ORDER BY audio_type COLLATE NOCASE, category COLLATE NOCASE
                """,
                params,
            )
            groups = [dict(row) for row in groups_cursor.fetchall()]
            if not groups:
                return list(rows.values())[: int(limit)]

            base_quota, extra = divmod(remaining, len(groups))
            capacities = [min(int(group["count"] or 0), base_quota + (1 if index < extra else 0)) for index, group in enumerate(groups)]
            assigned = sum(capacities)
            while assigned < remaining:
                progressed = False
                for index, group in enumerate(groups):
                    if assigned >= remaining:
                        break
                    if capacities[index] >= int(group["count"] or 0):
                        continue
                    capacities[index] += 1
                    assigned += 1
                    progressed = True
                if not progressed:
                    break

            for group, quota in zip(groups, capacities):
                if quota <= 0:
                    continue
                group_clauses = [where, "audio_type = ?", "category = ?"]
                cursor = self.conn.execute(
                    f"""
                    SELECT {', '.join(MAP_RECORD_COLUMNS)}
                    FROM staging_records
                    WHERE {' AND '.join(group_clauses)}
                    ORDER BY subcategory COLLATE NOCASE, row_id ASC
                    LIMIT ?
                    """,
                    [*params, str(group.get("audio_type") or ""), str(group.get("category") or ""), quota],
                )
                for row in cursor.fetchall():
                    rows.setdefault(int(row["row_id"]), dict(row))
            if len(rows) < int(limit):
                cursor = self.conn.execute(
                    f"""
                    SELECT {', '.join(MAP_RECORD_COLUMNS)}
                    FROM staging_records
                    WHERE {where}
                    ORDER BY audio_type COLLATE NOCASE, category COLLATE NOCASE, subcategory COLLATE NOCASE, row_id ASC
                    """,
                    params,
                )
                for row in cursor:
                    rows.setdefault(int(row["row_id"]), dict(row))
                    if len(rows) >= int(limit):
                        break
        return list(rows.values())[: int(limit)]

    def coherence_clusters_for_row_ids(self, row_ids: Iterable[int]) -> list[dict[str, Any]]:
        values = sorted({int(row_id) for row_id in row_ids})
        results: list[dict[str, Any]] = []
        for start in range(0, len(values), 800):
            chunk = values[start:start + 800]
            if not chunk:
                continue
            placeholders = ", ".join("?" for _ in chunk)
            cursor = self.conn.execute(
                f"""
                SELECT record_id, cluster_id
                FROM coherence_results
                WHERE session_id = ? AND record_id IN ({placeholders})
                ORDER BY record_id
                """,
                [self.session_id, *[str(value) for value in chunk]],
            )
            results.extend(dict(row) for row in cursor)
        return results

    def group_counts(self, fields: list[str], query: StagingQuery | None = None) -> list[dict[str, Any]]:
        allowed = {"audio_type", "category", "subcategory", "pack"}
        selected = [field for field in fields if field in allowed]
        if not selected:
            return [{"count": self.count(query)}]
        where, params = self._where(query)
        select = ", ".join(selected)
        group = ", ".join(selected)
        cursor = self.conn.execute(
            f"""
            SELECT {select}, COUNT(*) AS count
            FROM staging_records
            WHERE {where}
            GROUP BY {group}
            ORDER BY {group} COLLATE NOCASE
            """,
            params,
        )
        return [dict(row) for row in cursor.fetchall()]

    def child_group_counts(self, parent_fields: dict[str, str], child_field: str, query: StagingQuery | None = None) -> list[dict[str, Any]]:
        if child_field not in {"audio_type", "category", "subcategory", "pack"}:
            return []
        where, params = self._where_with_fields(parent_fields, query)
        cursor = self.conn.execute(
            f"""
            SELECT {child_field} AS value, COUNT(*) AS count
            FROM staging_records
            WHERE {where}
            GROUP BY {child_field}
            ORDER BY {child_field} COLLATE NOCASE
            """,
            params,
        )
        return [dict(row) for row in cursor.fetchall()]

    def count_for_fields(self, fields: dict[str, str], query: StagingQuery | None = None) -> int:
        where, params = self._where_with_fields(fields, query)
        row = self.conn.execute(
            f"SELECT COUNT(*) FROM staging_records WHERE {where}",
            params,
        ).fetchone()
        return int(row[0] if row is not None else 0)

    def records_for_fields(
        self,
        fields: dict[str, str],
        query: StagingQuery | None = None,
        limit: int | None = None,
        *,
        offset: int = 0,
    ) -> list[PlanRecord]:
        where, params = self._where_with_fields(fields, query)
        limit_sql = ""
        if limit is not None:
            limit_sql = " LIMIT ? OFFSET ?"
            params.extend([max(0, int(limit)), max(0, int(offset))])
        cursor = self.conn.execute(
            f"SELECT * FROM staging_records WHERE {where} ORDER BY sample_name COLLATE NOCASE, row_id ASC{limit_sql}",
            params,
        )
        return [plan_record_from_staging_row(dict(row), parse_tags) for row in cursor.fetchall()]

    def _where(
        self,
        query: StagingQuery | None,
        *,
        table_alias: str = "",
    ) -> tuple[str, list[Any]]:
        def column(name: str) -> str:
            return f"{table_alias}.{name}" if table_alias else name

        clauses = [f"{column('session_id')} = ?"]
        params: list[Any] = [self.session_id]
        if query is None:
            return " AND ".join(clauses), params
        if query.matched_ids is not None:
            ids = sorted(int(row_id) for row_id in query.matched_ids)
            if not ids:
                clauses.append("0")
            else:
                clauses.append(f"{column('row_id')} IN ({', '.join('?' for _ in ids)})")
                params.extend(ids)
        if query.audio_types is not None:
            values = sorted(str(value) for value in query.audio_types)
            if not values:
                clauses.append("0")
            else:
                clauses.append(f"{column('audio_type')} IN ({', '.join('?' for _ in values)})")
                params.extend(values)
        if not query.show_non_audio_assets:
            clauses.append(f"COALESCE({column('audio_type')}, '') != 'Non-Audio Assets'")
        if not query.show_duplicates:
            clauses.append(
                f"INSTR(' ' || LOWER(COALESCE({column('tags')}, '')) || ' ', ' duplicate ') = 0"
            )
        for prefix in query.path_prefixes:
            clauses.append(f"LOWER(REPLACE({column('source_path')}, '\\', '/')) LIKE ? ESCAPE '!'")
            params.append(prefix.replace("!", "!!").replace("%", "!%").replace("_", "!_") + "%")
        if query.confidence_min > 0.0:
            clauses.append(f"({column('confidence')} IS NULL OR CAST({column('confidence')} AS REAL) >= ?)")
            params.append(query.confidence_min)
        if query.confidence_max < 1.0:
            clauses.append(f"({column('confidence')} IS NULL OR CAST({column('confidence')} AS REAL) <= ?)")
            params.append(query.confidence_max)
        for col, allowed in query.column_filters:
            db_col = DB_TABLE_COLUMNS.get(StagingColumn(col))
            if not db_col:
                continue
            values = list(allowed)
            if not values:
                clauses.append("0")
            else:
                clauses.append(f"CAST({column(db_col)} AS TEXT) IN ({', '.join('?' for _ in values)})")
                params.extend(values)
        if query.similarity_rows is not None:
            ids = sorted(int(row_id) for row_id in query.similarity_rows)
            if not ids:
                clauses.append("0")
            else:
                clauses.append(f"{column('row_id')} IN ({', '.join('?' for _ in ids)})")
                params.extend(ids)
        return " AND ".join(clauses), params

    def _where_with_fields(self, fields: dict[str, str], query: StagingQuery | None = None) -> tuple[str, list[Any]]:
        where, params = self._where(query)
        clauses = [where]
        for field, value in (fields or {}).items():
            if field not in {"audio_type", "category", "subcategory", "pack"}:
                continue
            if field == "audio_type" and str(value or "") == "Utility":
                value = "Non-Audio Assets"
            clauses.append(f"COALESCE({field}, '') = ?")
            params.append(str(value or ""))
        return " AND ".join(clauses), params

    def _order_by(self, sort_column: int | StagingColumn | None, *, descending: bool = False) -> str:
        try:
            column = StagingColumn(sort_column) if sort_column is not None else StagingColumn.PACK
        except (TypeError, ValueError):
            column = StagingColumn.PACK
        db_col = DB_TABLE_COLUMNS.get(column, "pack")
        direction = "DESC" if descending else "ASC"
        if column == StagingColumn.CONFIDENCE:
            return f"ORDER BY CAST(confidence AS REAL) {direction}, sample_name COLLATE NOCASE ASC, row_id ASC"
        if column == StagingColumn.FILENAME:
            return f"ORDER BY sample_name COLLATE NOCASE {direction}, row_id ASC"
        return f"ORDER BY {db_col} COLLATE NOCASE {direction}, sample_name COLLATE NOCASE ASC, row_id ASC"


class DbRecordSequence:
    """Lazy compatibility surface preserving the old all-records model contract."""

    def __init__(self, store: StagingSessionStore, model: Any):
        self.store = store
        self.model = model

    def __len__(self) -> int:
        return self.store.count(None)

    def __getitem__(self, index: int) -> PlanRecord:
        row_ids = self.store.row_ids(None)
        if isinstance(index, slice):
            return [
                record
                for row_id in row_ids[index]
                if (record := self.store.record_by_row_id(row_id)) is not None
            ]  # type: ignore[return-value]
        row_id = row_ids[index]
        record = self.store.record_by_row_id(row_id)
        if record is None:
            raise IndexError(index)
        return record

    def __iter__(self) -> Iterator[PlanRecord]:
        for batch in self.store.iter_records(batch_size=1500, query=None):
            yield from batch

    def common_filename_tokens_by_pack(self) -> dict[str, frozenset[str]]:
        return self.store.common_filename_tokens_by_pack()


class BuildableDbRecordSequence:
    """Reiterable bounded DTO stream for build execution."""

    def __init__(self, store: StagingSessionStore, *, include_duplicate_shadows: bool = False):
        self.store = store
        self.include_duplicate_shadows = bool(include_duplicate_shadows)

    def __len__(self) -> int:
        if self.include_duplicate_shadows:
            return self.store.count(None)
        return self.store.count_buildable_records()

    def __iter__(self) -> Iterator[PlanRecord]:
        if self.include_duplicate_shadows:
            for batch in self.store.iter_records(batch_size=1000, query=None):
                yield from batch
            return
        for batch in self.store.iter_buildable_records(batch_size=1000):
            yield from batch

    def common_filename_tokens_by_pack(self) -> dict[str, frozenset[str]]:
        return self.store.common_filename_tokens_by_pack()


class LruRecordCache:
    def __init__(self, max_size: int = 5000):
        self.max_size = max(1, int(max_size))
        self._cache: OrderedDict[int, PlanRecord] = OrderedDict()

    def get(self, key: int) -> PlanRecord | None:
        value = self._cache.get(key)
        if value is not None:
            self._cache.move_to_end(key)
        return value

    def put_many(self, values: dict[int, PlanRecord]) -> None:
        for key, value in values.items():
            self._cache[key] = value
            self._cache.move_to_end(key)
        while len(self._cache) > self.max_size:
            self._cache.popitem(last=False)

    def clear(self) -> None:
        self._cache.clear()
