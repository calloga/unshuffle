import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from unshuffle.persistence import (
    connection,
)
from unshuffle.persistence.storages import storage_taxonomy, storage_lifecycle, storage_coherence, storage_learning, \
    storage_sessions, storage_maintenance
from unshuffle.persistence.stores.cache_store import SqliteCacheStore, PeeweeCacheStore
from unshuffle.persistence.utils import cache_utils
from unshuffle.persistence.utils.cache_utils import normalize_cache_rows, cache_row


class UnshuffleDB:
    """
    SQLite backend for Unshuffle metadata.
    """

    SCHEMA_VERSION = 9

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._connections: Set[sqlite3.Connection] = set()
        self._thread_state = threading.local()
        self._connection_lock = threading.RLock()
        self._write_lock = threading.RLock()
        self._closed = False
        self._initialize_schema()
        self._cache_store = PeeweeCacheStore(self.conn, self.db_path)
        if os.environ.get("UNSHUFFLE_DB_FOREIGN_KEY_CHECK", "0") == "1":
            self._log_foreign_key_integrity()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False

    def _create_connection(self) -> sqlite3.Connection:
        return connection.create_connection(self.db_path)

    def _get_connection(self) -> sqlite3.Connection:
        if self._closed:
            raise RuntimeError(f"Database handle for {self.db_path} is closed")
        with self._connection_lock:
            conn = getattr(self._thread_state, "conn", None)
            if conn is None:
                conn = self._create_connection()
                self._thread_state.conn = conn
                self._connections.add(conn)
            return conn

    @property
    def conn(self) -> sqlite3.Connection:
        return self._get_connection()

    @contextmanager
    def _write_transaction(self):
        with self._write_lock:
            with self.conn:
                yield

    @contextmanager
    def write_transaction(self):
        with self._write_transaction():
            yield

    def foreign_key_violations(self) -> List[Tuple[str, int, str, int]]:
        return connection.foreign_key_violations(self.conn, self.db_path)

    def _log_foreign_key_integrity(self):
        storage_lifecycle.log_foreign_key_integrity(self)

    def _initialize_schema(self):
        storage_lifecycle.initialize_schema(self, self.SCHEMA_VERSION)

    def _get_schema_version(self) -> int:
        row = self.conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
        return int(row[0]) if row else 0

    def get_schema_version(self) -> int:
        return self._get_schema_version()

    def get_all_hashes(self) -> Dict[str, str]:
        return self._cache_store.get_all_hashes()

    def has_hash_in_library(self, file_hash: str) -> bool:
        return self._cache_store.has_hash_in_library(file_hash)

    def get_committed_hashes(self) -> Set[str]:
        return self._cache_store.get_committed_hashes()

    def get_cached_hash(self, path: Path, size: int, mtime: float) -> Optional[str]:
        return self._cache_store.get_cached_hash(path, size, mtime)

    def get_cached_hashes(self, file_stats: List[tuple[Path, int, float]]) -> Dict[str, str]:
        return self._cache_store.get_cached_hashes(file_stats)

    def update_cache(
        self,
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
    ):
        row = cache_row(
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
        with self.write_transaction():
            self._cache_store.upsert_cache_rows([row])

    def get_feature_vector(self, file_hash: str) -> Optional[bytes]:
        return self._cache_store.get_feature_vector(file_hash)

    def get_feature_vectors_bulk(self, file_hashes: List[str]) -> Dict[str, bytes]:
        return self._cache_store.get_feature_vectors_bulk(file_hashes)

    def get_acoustic_vector(self, file_hash: str) -> Optional[bytes]:
        return self._cache_store.get_feature_vector(file_hash)

    def get_cached_path_by_hash(self, file_hash: str) -> Optional[str]:
        return self._cache_store.get_cached_path_by_hash(file_hash)

    def update_cache_bulk(self, hash_list: List[tuple]):
        if not hash_list:
            return
        normalized = normalize_cache_rows(hash_list)
        with self.write_transaction():
            self._cache_store.upsert_cache_rows(normalized)

    def remove_from_cache_by_paths(self, path_list: List[str]):
        if not path_list:
            return
        with self.write_transaction():
            self.conn.executemany("DELETE FROM file_cache WHERE last_path = ?", [(p,) for p in path_list])

    def clear_cache(self):
        self._cache_store.clear_cache()

    def register_session(self, session_id: str, source: Path, target: Path, mode: str, is_flat: bool = False):
        storage_sessions.register_session(self, session_id, source, target, mode, is_flat)

    def set_session_sources(self, session_id: str, sources: List[Path]):
        storage_sessions.set_session_sources(self, session_id, sources)

    def set_session_metadata(self, session_id: str, key: str, value_json: str):
        storage_sessions.set_session_metadata(self, session_id, key, value_json)

    def get_session_metadata(self, session_id: str, key: str) -> str | None:
        return storage_sessions.get_session_metadata(self, session_id, key)

    def remove_session_source(self, session_id: str, source_path: str):
        storage_sessions.remove_session_source(self, session_id, source_path)

    def get_session_sources(self, session_id: str) -> List[str]:
        return storage_sessions.get_session_sources(self, session_id)

    def add_records_bulk(self, session_id: str, records_list: List[Dict[str, Any]]):
        storage_sessions.add_records_bulk(self, session_id, records_list)

    def get_session_records(self, session_id: str) -> List[Dict]:
        return storage_sessions.get_session_records(self, session_id)

    def get_recent_sessions(
        self,
        limit: int = 10,
        only_executed: bool = False,
        target_root: Path | str | None = None,
    ) -> List[Dict]:
        return storage_sessions.get_recent_sessions(self, limit, only_executed, target_root)

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        return storage_sessions.get_session(self, session_id)

    def mark_session_undone(self, session_id: str):
        storage_sessions.mark_session_undone(self, session_id)

    def delete_session(self, session_id: str):
        storage_sessions.delete_session(self, session_id)

    def clear_staging(self, session_id: Optional[str] = None):
        storage_sessions.clear_staging(self, session_id)

    def prune_ephemeral_state(
        self,
        keep_session_ids: Set[str] | List[str] | Tuple[str, ...] | None = None,
        target_root: Path | str | None = None,
        *,
        use_restorable_fallback: bool = True,
    ) -> Dict[str, Any]:
        return storage_maintenance.prune_ephemeral_state(
            self,
            keep_session_ids,
            target_root,
            use_restorable_fallback=use_restorable_fallback,
        )

    def newest_restorable_staging_session(self, target_root: Path | str | None = None) -> str:
        return storage_maintenance.newest_restorable_staging_session(self, target_root)

    def database_size_stats(self) -> Dict[str, int]:
        return storage_maintenance.database_size_stats(self)

    def compact_if_worthwhile(
        self,
        *,
        min_reclaim_mb: int = 512,
        min_reclaim_ratio: float = 0.25,
    ) -> Dict[str, Any]:
        return storage_maintenance.compact_if_worthwhile(
            self,
            min_reclaim_mb=min_reclaim_mb,
            min_reclaim_ratio=min_reclaim_ratio,
        )

    def force_compact(self) -> Dict[str, Any]:
        return storage_maintenance.force_compact(self)

    def remove_staging_by_source(self, session_id: str, source_path: str):
        storage_sessions.remove_staging_by_source(self, session_id, source_path)

    def add_staging_records_bulk(self, session_id: str, records: List[Tuple]):
        storage_sessions.add_staging_records_bulk(self, session_id, records)

    def get_staging_records(self, session_id: str) -> List[Dict]:
        return storage_sessions.get_staging_records(self, session_id)

    def upsert_coherence_results(self, session_id: str, results: List[Any]):
        storage_coherence.upsert_coherence_results(self, session_id, results)

    def list_coherence_results(self, session_id: str) -> List[Dict[str, Any]]:
        return storage_coherence.list_coherence_results(self, session_id)

    def upsert_refinement_candidates(self, session_id: str, candidates: List[Any]):
        storage_coherence.upsert_refinement_candidates(self, session_id, candidates)

    def list_refinement_candidates(self, session_id: str, state: Optional[str] = None) -> List[Dict[str, Any]]:
        return storage_coherence.list_refinement_candidates(self, session_id, state)

    def count_refinement_candidates(self, session_id: str, state: Optional[str] = None) -> int:
        return storage_coherence.count_refinement_candidates(self, session_id, state)

    def set_refinement_candidate_state(self, session_id: str, candidate_ids: List[str], state: str):
        storage_coherence.set_refinement_candidate_state(self, session_id, candidate_ids, state)

    def upsert_coherence_review_decisions(self, session_id: str, decisions: List[Dict[str, Any]]):
        storage_coherence.upsert_coherence_review_decisions(self, session_id, decisions)

    def list_coherence_review_decisions(
        self,
        source_paths: List[str] | None = None,
        file_hashes: List[str] | None = None,
    ) -> List[Dict[str, Any]]:
        return storage_coherence.list_coherence_review_decisions(self, source_paths, file_hashes)

    def upsert_anchor_candidates(self, session_id: str, anchors: List[Any]):
        storage_coherence.upsert_anchor_candidates(self, session_id, anchors)

    def upsert_coherence_audit(self, session_id: str, results: List[Any], candidates: List[Any], anchors: List[Any]):
        storage_coherence.upsert_coherence_audit(self, session_id, results, candidates, anchors)

    def upsert_anchor_profiles(self, session_id: str, anchors: List[Any]):
        storage_coherence.upsert_anchor_profiles(self, session_id, anchors)

    def upsert_anchor_profile_rows(self, session_id: str, rows: List[Dict[str, Any]]):
        storage_coherence.upsert_anchor_profile_rows(self, session_id, rows)

    def list_anchor_candidates(self, session_id: str, state: Optional[str] = None) -> List[Dict[str, Any]]:
        return storage_coherence.list_anchor_candidates(self, session_id, state)

    def ensure_verified_anchors_for_session(self, session_id: str) -> int:
        return storage_coherence.ensure_verified_anchors_for_session(self, session_id)

    def set_anchor_candidate_state(self, session_id: str, anchor_ids: List[str], state: str):
        storage_coherence.set_anchor_candidate_state(self, session_id, anchor_ids, state)

    def remove_verified_anchor_profiles(self, session_id: str, anchor_ids: List[str]):
        storage_coherence.remove_verified_anchor_profiles(self, session_id, anchor_ids)

    def repair_anchor_profile_json(self, session_id: str, anchor_ids: List[str], payload_builder) -> List[str]:
        """Rebuild profile_json from binary columns for anchors missing it.
        Returns anchor_ids that could not be repaired (caller should treat as failure)."""
        return storage_coherence.repair_anchor_profile_json(self, session_id, anchor_ids, payload_builder)

    def seed_system_anchors(self, rows: List[Dict[str, Any]]):
        storage_coherence.seed_system_anchors(self, rows)

    def _normalize_acoustic_vector(self, value) -> Optional[bytes]:
        return cache_utils.normalize_feature_vector(value)

    def update_staging_record(self, session_id: str, row_id: int, data: Dict[str, str]):
        storage_sessions.update_staging_record(self, session_id, row_id, data)

    def search_staging(self, session_id: str, query_text: str) -> List[int] | Set[int]:
        return storage_learning.search_staging(self, session_id, query_text)

    def search_similar_records(
        self,
        session_id: str,
        target_id: int,
        limit: int = 50,
        candidate_ids: Set[int] | None = None,
    ) -> List[int]:
        return storage_learning.search_similar_records(
            self,
            session_id,
            target_id,
            limit,
            candidate_ids=candidate_ids,
        )

    def update_token_adjustment(self, token: str, category: str, delta: float):
        storage_learning.update_token_adjustment(self, token, category, delta)

    def update_token_adjustments_bulk(self, adjustment_list: List[tuple]):
        storage_learning.update_token_adjustments_bulk(self, adjustment_list)

    def update_token_adjustments_from_events(self, event_list: List[tuple]) -> int:
        return storage_learning.update_token_adjustments_from_events(self, event_list)

    def get_token_adjustments(self) -> Dict[str, Dict[str, float]]:
        return storage_learning.get_token_adjustments(self)

    def prune_unweighted_token_adjustments(self) -> int:
        return storage_learning.prune_unweighted_token_adjustments(self)

    def delete_token_adjustments(self, adjustment_keys: List[tuple]) -> int:
        return storage_learning.delete_token_adjustments(self, adjustment_keys)

    def clear_all_history(self):
        storage_sessions.clear_all_history(self)

    def clear_history_for_target(self, target_root: Path | str):
        storage_sessions.clear_history_for_target(self, target_root)

    def reset_adjustments(self):
        storage_taxonomy.reset_adjustments(self)

    def seed_aliases_bulk(self, alias_list: List[tuple]):
        storage_taxonomy.seed_aliases_bulk(self, alias_list)

    def get_aliases(self) -> Dict[str, Tuple[str, float]]:
        return storage_taxonomy.get_aliases(self)

    def get_aliases_with_source(self) -> Dict[str, Tuple[str, float, str]]:
        return storage_taxonomy.get_aliases_with_source(self)

    def get_aliases_by_source(self, source: str) -> Dict[str, Tuple[str, float]]:
        return storage_taxonomy.get_aliases_by_source(self, source)

    def seed_config_list(self, list_type: str, values: List[str], clear: bool = False):
        storage_taxonomy.seed_config_list(self, list_type, values, clear)

    def seed_suppression_rules(self, rules: Dict[str, List[str]]):
        storage_taxonomy.seed_suppression_rules(self, rules)

    def seed_sub_taxonomy(self, mapping: Dict[str, Dict[str, str]]):
        storage_taxonomy.seed_sub_taxonomy(self, mapping)

    def get_config_list(self, list_type: str) -> List[str]:
        return storage_taxonomy.get_config_list(self, list_type)

    def get_suppression_rules(self) -> Dict[str, List[str]]:
        return storage_taxonomy.get_suppression_rules(self)

    def get_sub_taxonomy(self) -> Dict[str, Dict[str, str]]:
        return storage_taxonomy.get_sub_taxonomy(self)

    def add_exclusion(self, path: str):
        storage_taxonomy.add_exclusion(self, path)

    def get_exclusions(self) -> List[str]:
        return storage_taxonomy.get_exclusions(self)

    def is_excluded(self, path: str) -> bool:
        return storage_taxonomy.is_excluded(self, path)

    def close(self):
        storage_lifecycle.close(self)
