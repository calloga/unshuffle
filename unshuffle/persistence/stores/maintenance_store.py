import abc
import logging
import os
import sqlite3
from pathlib import Path
from typing import Any

from unshuffle.persistence.schema.enums import AnchorProfileState
from unshuffle.persistence.schema.models import (
    AnchorProfile,
    CoherenceResult,
    Record,
    RefinementCandidate,
    Session,
    SessionSource,
    StagingRecord,
)
from unshuffle.persistence.utils.thread_aware_sqlite_database import (
    ConnectionBoundStore,
    ConnectionProvider,
    PeeweeStore,
)

EPHEMERAL_TABLES = (
    "staging_records",
    "staging_fts",
    "coherence_results",
    "refinement_candidates",
)
REMOVED_VERIFIED_ANCHOR_SESSION = "__removed_verified_anchors__"


def _normalized_path_value(path: Path | str | None) -> str:
    if path is None:
        return ""
    try:
        value = str(Path(path).resolve())
    except OSError:
        value = str(Path(path))
    return os.path.normcase(os.path.normpath(value))


def _placeholders(values) -> str:
    return ", ".join("?" for _ in values)


class MaintenanceStore(abc.ABC):
    @abc.abstractmethod
    def _all_session_ids(self) -> set[str]:
        pass

    @abc.abstractmethod
    def _session_ids_for_target(self, target_root: Path | str | None) -> set[str]:
        pass

    @abc.abstractmethod
    def _ephemeral_session_ids(self) -> set[str]:
        pass

    @abc.abstractmethod
    def _count_ephemeral_rows(self, prune_sessions: set[str]) -> dict[str, int]:
        pass

    @abc.abstractmethod
    def _delete_ephemeral_rows(self, prune_sessions: set[str]) -> None:
        pass

    @abc.abstractmethod
    def _delete_orphaned_sessions(self, prune_sessions: set[str]) -> int:
        pass

    @abc.abstractmethod
    def newest_restorable_staging_session(self, target_root: Path | str | None = None) -> str:
        pass

    @abc.abstractmethod
    def database_size_stats(self) -> dict[str, int]:
        pass

    def prune_ephemeral_state(
            self,
            keep_session_ids: set[str] | list[str] | tuple[str, ...] | None = None,
            target_root: Path | str | None = None,
            *,
            use_restorable_fallback: bool = True,
    ) -> dict[str, Any]:
        keep = {str(item).strip() for item in (keep_session_ids or set()) if str(item or "").strip()}
        if not keep and target_root is not None and use_restorable_fallback:
            fallback = self.newest_restorable_staging_session(target_root)
            if fallback:
                keep.add(fallback)

        known_sessions = self._all_session_ids()
        scoped_sessions = self._session_ids_for_target(target_root)
        ephemeral_sessions = self._ephemeral_session_ids()
        orphan_sessions = ephemeral_sessions - known_sessions
        prune_sessions = ((ephemeral_sessions & scoped_sessions) | orphan_sessions) - keep

        stats: dict[str, Any] = {
            "kept_sessions": sorted(keep),
            "pruned_sessions": sorted(prune_sessions),
            "deleted": {},
            "pending_sessions_deleted": 0,
        }
        if not prune_sessions:
            return stats

        stats["deleted"] = self._count_ephemeral_rows(prune_sessions)
        self._delete_ephemeral_rows(prune_sessions)
        stats["pending_sessions_deleted"] = self._delete_orphaned_sessions(prune_sessions)
        return stats

    def compact_if_worthwhile(
            self,
            *,
            min_reclaim_mb: int = 512,
            min_reclaim_ratio: float = 0.25,
    ) -> dict[str, Any]:
        before = self.database_size_stats()
        database_bytes = int(before.get("database_bytes") or 0)
        reclaimable_bytes = int(before.get("reclaimable_bytes") or 0)
        threshold_bytes = int(min_reclaim_mb) * 1024 * 1024
        reclaim_ratio = (reclaimable_bytes / database_bytes) if database_bytes else 0.0
        result: dict[str, Any] = {
            "ran": False,
            "skipped": False,
            "reason": "",
            "before": before,
            "after": before,
        }
        if reclaimable_bytes < threshold_bytes or reclaim_ratio < float(min_reclaim_ratio):
            result["skipped"] = True
            result["reason"] = "below_threshold"
            return result

        try:
            self._connection.execute("VACUUM")
        except sqlite3.OperationalError as exc:
            if "locked" in str(exc).lower() or "busy" in str(exc).lower():
                result["skipped"] = True
                result["reason"] = "database_busy"
                return result
            raise
        except sqlite3.DatabaseError as exc:
            logging.debug("Database compaction skipped: %s", exc)
            result["skipped"] = True
            result["reason"] = "database_error"
            return result

        result["ran"] = True
        result["after"] = self.database_size_stats()
        return result

    def force_compact(self) -> dict[str, Any]:
        before = self.database_size_stats()
        self._connection.execute("VACUUM")
        after = self.database_size_stats()
        return {"ran": True, "before": before, "after": after}


class SqliteMaintenanceStore(MaintenanceStore, ConnectionBoundStore):
    def _all_session_ids(self) -> set[str]:
        rows = self._connection.execute("SELECT session_id FROM sessions WHERE session_id IS NOT NULL").fetchall()
        return {str(row["session_id"]) for row in rows if str(row["session_id"] or "").strip()}

    def _session_ids_for_target(self, target_root: Path | str | None) -> set[str]:
        if target_root is None:
            return self._all_session_ids()
        target_value = _normalized_path_value(target_root)
        rows = self._connection.execute(
            "SELECT session_id, target_root FROM sessions WHERE session_id IS NOT NULL"
        ).fetchall()
        return {
            str(row["session_id"])
            for row in rows
            if str(row["session_id"] or "").strip()
            and _normalized_path_value(row["target_root"]) == target_value
        }

    def _ephemeral_session_ids(self) -> set[str]:
        session_ids: set[str] = set()
        for table in EPHEMERAL_TABLES:
            rows = self._connection.execute(
                f"SELECT DISTINCT session_id FROM {table} WHERE session_id IS NOT NULL"
            ).fetchall()
            session_ids.update(str(row["session_id"]) for row in rows if str(row["session_id"] or "").strip())
        rows = self._connection.execute(
            """
            SELECT DISTINCT session_id
            FROM anchor_profiles
            WHERE session_id IS NOT NULL
              AND state NOT IN ('verified', 'system')
              AND session_id NOT IN ('__system__', ?)
            """,
            (REMOVED_VERIFIED_ANCHOR_SESSION,),
        ).fetchall()
        session_ids.update(str(row["session_id"]) for row in rows if str(row["session_id"] or "").strip())
        return session_ids

    def newest_restorable_staging_session(self, target_root: Path | str | None = None) -> str:
        target_value = _normalized_path_value(target_root) if target_root is not None else None
        rows = self._connection.execute(
            """
            SELECT s.session_id, s.target_root
            FROM sessions s
            WHERE s.session_id IS NOT NULL
              AND EXISTS (
                  SELECT 1 FROM staging_records sr WHERE sr.session_id = s.session_id
              )
            ORDER BY s.timestamp DESC
            """
        ).fetchall()
        for row in rows:
            session_id = str(row["session_id"] or "").strip()
            if not session_id:
                continue
            if target_value is None or _normalized_path_value(row["target_root"]) == target_value:
                return session_id
        return ""

    def _count_for_sessions(self, table: str, session_ids: set[str], extra_where: str = "") -> int:
        if not session_ids:
            return 0
        placeholders = _placeholders(session_ids)
        row = self._connection.execute(
            f"SELECT COUNT(*) FROM {table} WHERE session_id IN ({placeholders}) {extra_where}",
            tuple(session_ids),
        ).fetchone()
        return int(row[0] or 0)

    def _delete_for_sessions(self, table: str, session_ids: set[str], extra_where: str = "") -> None:
        if not session_ids:
            return
        placeholders = _placeholders(session_ids)
        self._connection.execute(
            f"DELETE FROM {table} WHERE session_id IN ({placeholders}) {extra_where}",
            tuple(session_ids),
        )

    def _count_ephemeral_rows(self, prune_sessions: set[str]) -> dict[str, int]:
        deleted: dict[str, int] = {}
        for table in EPHEMERAL_TABLES:
            deleted[table] = self._count_for_sessions(table, prune_sessions)
        deleted["anchor_profiles"] = self._count_for_sessions(
            "anchor_profiles",
            prune_sessions,
            f"AND state NOT IN ('verified', 'system') AND session_id NOT IN "
            f"('__system__', '{REMOVED_VERIFIED_ANCHOR_SESSION}')",
        )
        return deleted

    def _delete_ephemeral_rows(self, prune_sessions: set[str]) -> None:
        for table in EPHEMERAL_TABLES:
            self._delete_for_sessions(table, prune_sessions)
        self._delete_for_sessions(
            "anchor_profiles",
            prune_sessions,
            f"AND state NOT IN ('verified', 'system') AND session_id NOT IN "
            f"('__system__', '{REMOVED_VERIFIED_ANCHOR_SESSION}')",
        )

    def _delete_orphaned_sessions(self, prune_sessions: set[str]) -> int:
        session_delete_rows = self._connection.execute(
            f"""
            SELECT s.session_id
            FROM sessions s
            WHERE s.session_id IN ({_placeholders(prune_sessions)})
              AND NOT EXISTS (SELECT 1 FROM records r WHERE r.session_id = s.session_id)
            """,
            tuple(prune_sessions),
        ).fetchall()
        deletable_sessions = {
            str(row["session_id"]) for row in session_delete_rows if str(row["session_id"] or "").strip()
        }
        if deletable_sessions:
            self._connection.executemany(
                "DELETE FROM session_sources WHERE session_id = ?",
                [(session_id,) for session_id in deletable_sessions],
            )
            self._connection.executemany(
                "DELETE FROM sessions WHERE session_id = ?",
                [(session_id,) for session_id in deletable_sessions],
            )
        return len(deletable_sessions)

    def database_size_stats(self) -> dict[str, int]:
        page_size = int(self._connection.execute("PRAGMA page_size").fetchone()[0] or 0)
        page_count = int(self._connection.execute("PRAGMA page_count").fetchone()[0] or 0)
        freelist_count = int(self._connection.execute("PRAGMA freelist_count").fetchone()[0] or 0)
        return {
            "page_size": page_size,
            "page_count": page_count,
            "freelist_count": freelist_count,
            "database_bytes": page_size * page_count,
            "reclaimable_bytes": page_size * freelist_count,
        }


class PeeweeMaintenanceStore(MaintenanceStore, PeeweeStore):
    _EPHEMERAL_MODELS = {
        "staging_records": StagingRecord,
        "coherence_results": CoherenceResult,
        "refinement_candidates": RefinementCandidate,
    }

    def __init__(self, connection_provider: ConnectionProvider):
        self._initialize_db_proxy(connection_provider)

    @staticmethod
    def _anchor_prune_condition(prune_list: list[str]):
        return (
            AnchorProfile.session_id.in_(prune_list)
            & AnchorProfile.state.not_in([AnchorProfileState.VERIFIED, AnchorProfileState.SYSTEM])
            & AnchorProfile.session_id.not_in(["__system__", REMOVED_VERIFIED_ANCHOR_SESSION])
        )

    def _all_session_ids(self) -> set[str]:
        self._bind_db_proxy()
        return {
            str(row.session_id)
            for row in Session.select(Session.session_id).where(Session.session_id.is_null(False))
            if str(row.session_id or "").strip()
        }

    def _session_ids_for_target(self, target_root: Path | str | None) -> set[str]:
        if target_root is None:
            return self._all_session_ids()
        self._bind_db_proxy()
        target_value = _normalized_path_value(target_root)
        return {
            str(row.session_id)
            for row in Session.select(Session.session_id, Session.target_root).where(
                Session.session_id.is_null(False)
            )
            if str(row.session_id or "").strip() and _normalized_path_value(row.target_root) == target_value
        }

    def _ephemeral_session_ids(self) -> set[str]:
        self._bind_db_proxy()
        session_ids: set[str] = set()
        for model in self._EPHEMERAL_MODELS.values():
            rows = model.select(model.session_id.distinct()).where(model.session_id.is_null(False))
            session_ids.update(str(row.session_id) for row in rows if str(row.session_id or "").strip())
        fts_rows = self._db.execute_sql(
            "SELECT DISTINCT session_id FROM staging_fts WHERE session_id IS NOT NULL"
        ).fetchall()
        session_ids.update(str(row[0]) for row in fts_rows if str(row[0] or "").strip())
        anchor_rows = AnchorProfile.select(AnchorProfile.session_id.distinct()).where(
            AnchorProfile.session_id.is_null(False)
            & AnchorProfile.state.not_in([AnchorProfileState.VERIFIED, AnchorProfileState.SYSTEM])
            & AnchorProfile.session_id.not_in(["__system__", REMOVED_VERIFIED_ANCHOR_SESSION])
        )
        session_ids.update(str(row.session_id) for row in anchor_rows if str(row.session_id or "").strip())
        return session_ids

    def newest_restorable_staging_session(self, target_root: Path | str | None = None) -> str:
        self._bind_db_proxy()
        target_value = _normalized_path_value(target_root) if target_root is not None else None
        staged_session_ids = {
            str(row.session_id)
            for row in StagingRecord.select(StagingRecord.session_id.distinct())
            if str(row.session_id or "").strip()
        }
        if not staged_session_ids:
            return ""
        query = (
            Session.select(Session.session_id, Session.target_root)
            .where(Session.session_id.in_(list(staged_session_ids)))
            .order_by(Session.timestamp.desc())
        )
        for row in query:
            session_id = str(row.session_id or "").strip()
            if not session_id:
                continue
            if target_value is None or _normalized_path_value(row.target_root) == target_value:
                return session_id
        return ""

    def _count_ephemeral_rows(self, prune_sessions: set[str]) -> dict[str, int]:
        self._bind_db_proxy()
        prune_list = list(prune_sessions)
        deleted = {
            table: model.select().where(model.session_id.in_(prune_list)).count()
            for table, model in self._EPHEMERAL_MODELS.items()
        }
        fts_row = self._db.execute_sql(
            f"SELECT COUNT(*) FROM staging_fts WHERE session_id IN ({_placeholders(prune_sessions)})",
            prune_list,
        ).fetchone()
        deleted["staging_fts"] = int(fts_row[0] or 0)
        deleted["anchor_profiles"] = AnchorProfile.select().where(
            self._anchor_prune_condition(prune_list)
        ).count()
        return deleted

    def _delete_ephemeral_rows(self, prune_sessions: set[str]) -> None:
        self._bind_db_proxy()
        prune_list = list(prune_sessions)
        for model in self._EPHEMERAL_MODELS.values():
            model.delete().where(model.session_id.in_(prune_list)).execute()
        self._db.execute_sql(
            f"DELETE FROM staging_fts WHERE session_id IN ({_placeholders(prune_sessions)})",
            prune_list,
        )
        AnchorProfile.delete().where(self._anchor_prune_condition(prune_list)).execute()

    def _delete_orphaned_sessions(self, prune_sessions: set[str]) -> int:
        self._bind_db_proxy()
        prune_list = list(prune_sessions)
        sessions_with_records = Record.select(Record.session_id.distinct())
        deletable_sessions = {
            str(row.session_id)
            for row in Session.select(Session.session_id).where(
                Session.session_id.in_(prune_list) & Session.session_id.not_in(sessions_with_records)
            )
            if str(row.session_id or "").strip()
        }
        if deletable_sessions:
            deletable_list = list(deletable_sessions)
            SessionSource.delete().where(SessionSource.session_id.in_(deletable_list)).execute()
            Session.delete().where(Session.session_id.in_(deletable_list)).execute()
        return len(deletable_sessions)

    def database_size_stats(self) -> dict[str, int]:
        self._bind_db_proxy()
        page_size = int(self._db.pragma("page_size") or 0)
        page_count = int(self._db.pragma("page_count") or 0)
        freelist_count = int(self._db.pragma("freelist_count") or 0)
        return {
            "page_size": page_size,
            "page_count": page_count,
            "freelist_count": freelist_count,
            "database_bytes": page_size * page_count,
            "reclaimable_bytes": page_size * freelist_count,
        }
