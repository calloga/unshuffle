import sqlite3
import time
from typing import Any, Callable, Dict, List, Optional

from unshuffle.persistence.stores import coherence_store


def _with_write_retry(db, callback: Callable[[], Any]) -> Any:
    for attempt in range(5):
        try:
            with db._write_transaction():
                return callback()
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() and "busy" not in str(exc).lower():
                raise
            if attempt == 4:
                raise
            time.sleep(0.2 * (attempt + 1))
    return None


def upsert_coherence_results(db, session_id: str, results: List[Any]) -> None:
    def _write() -> None:
        coherence_store.upsert_coherence_results(db.conn, session_id, results)
    _with_write_retry(db, _write)


def list_coherence_results(db, session_id: str) -> List[Dict[str, Any]]:
    return coherence_store.list_coherence_results(db.conn, session_id)


def upsert_refinement_candidates(db, session_id: str, candidates: List[Any]) -> None:
    def _write() -> None:
        coherence_store.upsert_refinement_candidates(db.conn, session_id, candidates)
    _with_write_retry(db, _write)


def list_refinement_candidates(db, session_id: str, state: Optional[str] = None) -> List[Dict[str, Any]]:
    return coherence_store.list_refinement_candidates(db.conn, session_id, state)


def count_refinement_candidates(db, session_id: str, state: Optional[str] = None) -> int:
    return coherence_store.count_refinement_candidates(db.conn, session_id, state)


def set_refinement_candidate_state(db, session_id: str, candidate_ids: List[str], state: str) -> None:
    if not candidate_ids:
        return
    with db._write_transaction():
        coherence_store.set_refinement_candidate_state(db.conn, session_id, candidate_ids, state)


def upsert_coherence_review_decisions(db, session_id: str, decisions: List[Dict[str, Any]]) -> None:
    if not decisions:
        return
    with db._write_transaction():
        coherence_store.upsert_coherence_review_decisions(db.conn, session_id, decisions)


def list_coherence_review_decisions(
    db,
    source_paths: List[str] | None = None,
    file_hashes: List[str] | None = None,
) -> List[Dict[str, Any]]:
    return coherence_store.list_coherence_review_decisions(
        db.conn,
        source_paths=source_paths,
        file_hashes=file_hashes,
    )


def upsert_anchor_candidates(db, session_id: str, anchors: List[Any]) -> None:
    def _write() -> None:
        coherence_store.upsert_anchor_candidates(db.conn, session_id, anchors)
    _with_write_retry(db, _write)


def upsert_coherence_audit(db, session_id: str, results: List[Any], candidates: List[Any], anchors: List[Any]) -> None:
    def _write() -> None:
        coherence_store.upsert_coherence_results(db.conn, session_id, results)
        coherence_store.upsert_refinement_candidates(db.conn, session_id, candidates)
        coherence_store.upsert_anchor_candidates(db.conn, session_id, anchors)
    _with_write_retry(db, _write)


def upsert_anchor_profiles(db, session_id: str, anchors: List[Any]) -> None:
    with db._write_transaction():
        coherence_store.upsert_anchor_profiles(db.conn, session_id, anchors)


def upsert_anchor_profile_rows(db, session_id: str, rows: List[Dict[str, Any]]) -> None:
    with db._write_transaction():
        coherence_store.upsert_anchor_profile_rows(db.conn, session_id, rows)


def list_anchor_candidates(db, session_id: str, state: Optional[str] = None) -> List[Dict[str, Any]]:
    return coherence_store.list_anchor_candidates(db.conn, session_id, state)


def ensure_verified_anchors_for_session(db, session_id: str) -> int:
    with db._write_transaction():
        return coherence_store.ensure_verified_anchors_for_session(db.conn, session_id)


def set_anchor_candidate_state(db, session_id: str, anchor_ids: List[str], state: str) -> None:
    if not anchor_ids:
        return
    with db._write_transaction():
        coherence_store.set_anchor_candidate_state(db.conn, session_id, anchor_ids, state)


def remove_verified_anchor_profiles(db, session_id: str, anchor_ids: List[str]) -> None:
    if not anchor_ids:
        return
    with db._write_transaction():
        coherence_store.remove_verified_anchor_profiles(db.conn, session_id, anchor_ids)


def repair_anchor_profile_json(db, session_id: str, anchor_ids: List[str], payload_builder) -> List[str]:
    if not anchor_ids:
        return []
    with db._write_transaction():
        return coherence_store.repair_anchor_profile_json(db.conn, session_id, anchor_ids, payload_builder)


def seed_system_anchors(db, rows: List[Dict[str, Any]]) -> None:
    if not rows:
        return
    with db._write_transaction():
        coherence_store.seed_system_anchors(db.conn, rows)
