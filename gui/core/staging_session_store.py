from __future__ import annotations

import csv
import json
import math
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator

from unshuffle.core import PlanRecord, parse_tags, plan_record_from_staging_row, tags_to_search_text
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


@dataclass(frozen=True)
class StagingQuery:
    matched_ids: frozenset[int] | None = None
    audio_types: frozenset[str] | None = None
    show_non_audio_assets: bool = False
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

    def _rows_by_ids(self, row_ids: Iterable[int], columns: str) -> list[dict[str, Any]]:
        ids = [int(row_id) for row_id in row_ids]
        if not ids:
            return []
        placeholders = ", ".join("?" for _ in ids)
        cursor = self.conn.execute(
            f"""
            SELECT {columns}
            FROM staging_records
            WHERE session_id = ? AND row_id IN ({placeholders})
            """,
            [self.session_id, *ids],
        )
        rows = {int(row["row_id"]): dict(row) for row in cursor.fetchall()}
        return [rows[row_id] for row_id in ids if row_id in rows]

    def record_by_row_id(self, row_id: int) -> PlanRecord | None:
        rows = self.rows_by_ids([row_id])
        if not rows:
            return None
        return plan_record_from_staging_row(rows[0], parse_tags)

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

    def update_row(self, row_id: int, fields: dict[str, Any], *, commit: bool = True) -> None:
        if not fields:
            return
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
        db_col = DB_TABLE_COLUMNS.get(StagingColumn(column))
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
        return [str(row[0]) for row in cursor.fetchall()]

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
                f"SELECT * FROM staging_records WHERE session_id = ? AND row_id IN ({placeholders})",
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
                    SELECT *
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
                    SELECT *
                    FROM staging_records
                    WHERE {where}
                    ORDER BY audio_type COLLATE NOCASE, category COLLATE NOCASE, subcategory COLLATE NOCASE, row_id ASC
                    """,
                    params,
                )
                for row in cursor.fetchall():
                    rows.setdefault(int(row["row_id"]), dict(row))
                    if len(rows) >= int(limit):
                        break
        return list(rows.values())[: int(limit)]

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

    def records_for_fields(self, fields: dict[str, str], query: StagingQuery | None = None, limit: int | None = None) -> list[PlanRecord]:
        where, params = self._where_with_fields(fields, query)
        limit_sql = ""
        if limit is not None:
            limit_sql = " LIMIT ?"
            params.append(int(limit))
        cursor = self.conn.execute(
            f"SELECT * FROM staging_records WHERE {where} ORDER BY sample_name COLLATE NOCASE, row_id ASC{limit_sql}",
            params,
        )
        return [plan_record_from_staging_row(dict(row), parse_tags) for row in cursor.fetchall()]

    def _where(self, query: StagingQuery | None) -> tuple[str, list[Any]]:
        clauses = ["session_id = ?"]
        params: list[Any] = [self.session_id]
        if query is None:
            return " AND ".join(clauses), params
        if query.matched_ids is not None:
            ids = sorted(int(row_id) for row_id in query.matched_ids)
            if not ids:
                clauses.append("0")
            else:
                clauses.append(f"row_id IN ({', '.join('?' for _ in ids)})")
                params.extend(ids)
        if query.audio_types is not None:
            values = sorted(str(value) for value in query.audio_types)
            if not values:
                clauses.append("0")
            else:
                clauses.append(f"audio_type IN ({', '.join('?' for _ in values)})")
                params.extend(values)
        if not query.show_non_audio_assets:
            clauses.append("COALESCE(audio_type, '') != 'Non-Audio Assets'")
        for prefix in query.path_prefixes:
            clauses.append("LOWER(REPLACE(source_path, '\\', '/')) LIKE ? ESCAPE '!'")
            params.append(prefix.replace("!", "!!").replace("%", "!%").replace("_", "!_") + "%")
        if query.confidence_min > 0.0:
            clauses.append("(confidence IS NULL OR CAST(confidence AS REAL) >= ?)")
            params.append(query.confidence_min)
        if query.confidence_max < 1.0:
            clauses.append("(confidence IS NULL OR CAST(confidence AS REAL) <= ?)")
            params.append(query.confidence_max)
        for col, allowed in query.column_filters:
            db_col = DB_TABLE_COLUMNS.get(StagingColumn(col))
            if not db_col:
                continue
            values = list(allowed)
            if not values:
                clauses.append("0")
            else:
                clauses.append(f"CAST({db_col} AS TEXT) IN ({', '.join('?' for _ in values)})")
                params.extend(values)
        if query.similarity_rows is not None:
            ids = sorted(int(row_id) for row_id in query.similarity_rows)
            if not ids:
                clauses.append("0")
            else:
                clauses.append(f"row_id IN ({', '.join('?' for _ in ids)})")
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
