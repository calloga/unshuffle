import abc
import dataclasses
import json
import sqlite3
import time
from typing import Any, Optional, Callable, List

from pathlib import Path
from peewee import EXCLUDED, SQL, Case

from unshuffle.core.features import vector_from_blob
from unshuffle.persistence.schema.enums import AnchorProfileState, RefinementCandidateState
from unshuffle.persistence.schema.models import AnchorProfile, CoherenceResult, CoherenceReviewDecision, \
    RefinementCandidate
from unshuffle.persistence.utils.cache_utils import normalize_feature_vector
from unshuffle.persistence.utils.thread_aware_sqlite_database import (
    ConnectionBoundStore,
    ConnectionProvider,
    PeeweeStore,
)

REMOVED_VERIFIED_ANCHOR_SESSION = "__removed_verified_anchors__"


def _normalized_source_path(value: Any) -> str:
    return Path(str(value or "")).as_posix()

class CoherenceStore(abc.ABC):
    @abc.abstractmethod
    def upsert_coherence_results(self, session_id: str, results: list[Any]):
        pass

    @abc.abstractmethod
    def clear_generated_coherence_audit(self, session_id: str) -> None: pass

    @abc.abstractmethod
    def list_coherence_results(self, session_id: str) -> list[dict[str, Any]]:
        pass

    @abc.abstractmethod
    def upsert_refinement_candidates(self, session_id: str, candidates: list[Any]):
        pass

    @abc.abstractmethod
    def list_refinement_candidates(self, session_id: str, state: Optional[str] = None) -> list[dict[str, Any]]:
        pass

    @abc.abstractmethod
    def count_refinement_candidates(self, session_id: str, state: Optional[str] = None) -> int:
        pass

    @abc.abstractmethod
    def set_refinement_candidate_state(self, session_id: str, candidate_ids: list[str],
                                       state: str) -> None:
        pass

    @abc.abstractmethod
    def upsert_coherence_review_decisions(self, session_id: str,
                                          decisions: list[dict[str, Any]]) -> None:
        pass

    @abc.abstractmethod
    def list_coherence_review_decisions(
            self,
            source_paths: list[str] | None = None,
            file_hashes: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        pass

    @abc.abstractmethod
    def upsert_anchor_candidates(self, session_id: str, anchors: list[Any]) -> None:
        pass

    @abc.abstractmethod
    def upsert_anchor_profiles(self, session_id: str, anchors: list[Any]) -> None:
        pass

    @abc.abstractmethod
    def upsert_anchor_profile_rows(self, session_id: str, rows: list[dict[str, Any]]) -> None:
        pass

    @abc.abstractmethod
    def _upsert_anchor_profiles(self, session_id: str, anchors: list[Any], *,
                                update_state: bool) -> None:
        pass

    @abc.abstractmethod
    def list_anchor_candidates(self, session_id: str, state: Optional[str] = None) -> list[dict[str, Any]]:
        pass

    @abc.abstractmethod
    def set_anchor_candidate_state(self, session_id: str, anchor_ids: list[str],
                                   state: str) -> None:
        pass

    @abc.abstractmethod
    def remove_verified_anchor_profiles(self, session_id: str, anchor_ids: list[str]) -> None:
        pass

    @abc.abstractmethod
    def repair_anchor_profile_json(
            self,
            session_id: str,
            anchor_ids: list[str],
            payload_builder,
    ) -> list[str]:
        """Reconstruct profile_json from binary columns for anchors where it is
        NULL or empty.  Returns the anchor_ids that could not be repaired.
        Callers should treat a non-empty return value as a hard failure."""
        pass

    @abc.abstractmethod
    def seed_system_anchors(self, rows: list[dict[str, Any]]) -> None:
        pass

    @abc.abstractmethod
    def ensure_verified_anchors_for_session(self, session_id: str) -> int:
        pass

    @abc.abstractmethod
    def coherence_cache_stats(self, session_id: str) -> dict[str, int]: pass

    @abc.abstractmethod
    def append_coherence_group(
            self,
            session_id: str,
            results: List[Any],
            candidates: List[Any] | None = None,
            anchors: List[Any] | None = None,
    ) -> None: pass


class SqliteCoherenceStore(CoherenceStore, ConnectionBoundStore):
    pass


class PeeweeCoherenceStore(CoherenceStore, PeeweeStore):
    def upsert_anchor_profiles(self, session_id: str, anchors: list[Any]) -> None:
        self._upsert_anchor_profiles(session_id, anchors, update_state=True)

    def _with_write_retry(self, callback: Callable[[], Any]) -> Any:
        for attempt in range(5):
            try:
                with self._db.atomic():
                    return callback()
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() and "busy" not in str(exc).lower():
                    raise
                if attempt == 4:
                    raise
                time.sleep(0.2 * (attempt + 1))
        return None

    def __init__(self, connection_provider: ConnectionProvider):
        self._initialize_db_proxy(connection_provider)

    def clear_generated_coherence_audit(self, session_id: str) -> None:
        self._with_write_retry(lambda:self._clear_generated_coherence_audit(session_id))

    def _clear_generated_coherence_audit(self, session_id):
        self._connection.execute("DELETE FROM coherence_results WHERE session_id = ?", (session_id,))
        self._connection.execute(
            "DELETE FROM refinement_candidates WHERE session_id = ? AND state IN ('pending', 'auto_staged')",
            (session_id,),
        )
        self._connection.execute("DELETE FROM anchor_profiles WHERE session_id = ? AND state = 'candidate'", (session_id,))

    def upsert_coherence_results(self, session_id: str, results: list[Any]):
        CoherenceResult.delete().where(CoherenceResult.session_id == session_id).execute()
        # prepare dicts
        _results = [{
            **dataclasses.asdict(x),
            'session_id': session_id,
            'record_id': str(x.record_id),
            'coherence_score': float(x.coherence_score),
            'is_outlier': 1 if x.is_outlier else 0,
            'nearest_neighbor_summary_json': json.dumps(x.nearest_neighbor_summary or {})
        } for x in results]
        # remove unnecessary field
        _results = [{k: v for k, v in x.items() if k != 'nearest_neighbor_summary'} for x in _results]

        CoherenceResult.insert_many(_results).on_conflict_replace().execute()

    def list_coherence_results(self, session_id: str) -> list[dict[str, Any]]:
        return list(
            CoherenceResult.select()
            .where(CoherenceResult.session_id == session_id)
            .order_by(CoherenceResult.record_id)
            .dicts()
        )

    def upsert_refinement_candidates(self, session_id: str, candidates: list[Any]) -> None:
        RefinementCandidate.delete().where(
            (RefinementCandidate.session_id == session_id)
            & (
                RefinementCandidate.state.in_(
                    [RefinementCandidateState.PENDING, RefinementCandidateState.AUTO_STAGED]
                )
            )
        ).execute()
        if not candidates:
            return
        rows = [
            {
                'session_id': session_id,
                'candidate_id': candidate.candidate_id,
                'record_id': str(candidate.record_id),
                'current_audio_type': getattr(candidate, 'current_audio_type', ''),
                'current_category': candidate.current_category,
                'current_subcategory': candidate.current_subcategory,
                'suggested_audio_type': getattr(candidate, 'suggested_audio_type', ''),
                'suggested_category': candidate.suggested_category,
                'suggested_subcategory': candidate.suggested_subcategory,
                'evidence': candidate.evidence,
                'coherence_status': candidate.coherence_status,
                'confidence_score': float(candidate.confidence_score),
                'state': candidate.state,
            }
            for candidate in candidates
        ]
        (
            RefinementCandidate
            .insert_many(rows)
            .on_conflict(
                conflict_target=[RefinementCandidate.session_id, RefinementCandidate.candidate_id],
                update={
                    RefinementCandidate.record_id: EXCLUDED.record_id,
                    RefinementCandidate.current_audio_type: EXCLUDED.current_audio_type,
                    RefinementCandidate.current_category: EXCLUDED.current_category,
                    RefinementCandidate.current_subcategory: EXCLUDED.current_subcategory,
                    RefinementCandidate.suggested_audio_type: EXCLUDED.suggested_audio_type,
                    RefinementCandidate.suggested_category: EXCLUDED.suggested_category,
                    RefinementCandidate.suggested_subcategory: EXCLUDED.suggested_subcategory,
                    RefinementCandidate.evidence: EXCLUDED.evidence,
                    RefinementCandidate.coherence_status: EXCLUDED.coherence_status,
                    RefinementCandidate.confidence_score: EXCLUDED.confidence_score,
                    RefinementCandidate.state: Case(None, [
                        (
                            RefinementCandidate.state.in_(
                                [RefinementCandidateState.ACCEPTED, RefinementCandidateState.IGNORED]
                            ),
                            RefinementCandidate.state,
                        ),
                    ], EXCLUDED.state),
                    RefinementCandidate.updated_at: SQL('CURRENT_TIMESTAMP'),
                },
            )
            .execute()
        )

    def list_refinement_candidates(self, session_id: str, state: Optional[str] = None) -> list[dict[str, Any]]:
        query = RefinementCandidate.select().where(RefinementCandidate.session_id == session_id)
        if state:
            query = query.where(RefinementCandidate.state == state)
        return list(query.order_by(RefinementCandidate.confidence_score.desc()).dicts())

    def count_refinement_candidates(self, session_id: str, state: Optional[str] = None) -> int:
        query = RefinementCandidate.select().where(RefinementCandidate.session_id == session_id)
        if state:
            query = query.where(RefinementCandidate.state == state)
        return query.count()

    def set_refinement_candidate_state(self, session_id: str, candidate_ids: list[str], state: str) -> None:
        if not candidate_ids:
            return
        RefinementCandidate.update(
            state=state, updated_at=SQL('CURRENT_TIMESTAMP')
        ).where(
            (RefinementCandidate.session_id == session_id)
            & (RefinementCandidate.candidate_id.in_(candidate_ids))
        ).execute()

    def upsert_coherence_review_decisions(self, session_id: str, decisions: list[dict[str, Any]]) -> None:
        rows = []
        for decision in decisions or []:
            source_path = _normalized_source_path(decision.get("source_path"))
            if not source_path:
                continue
            rows.append({
                'source_path': source_path,
                'file_hash': str(decision.get('file_hash') or ''),
                'decision_type': str(decision.get('decision_type') or ''),
                'current_audio_type': str(decision.get('current_audio_type') or ''),
                'current_category': str(decision.get('current_category') or ''),
                'current_subcategory': str(decision.get('current_subcategory') or ''),
                'target_audio_type': str(decision.get('target_audio_type') or ''),
                'target_category': str(decision.get('target_category') or ''),
                'target_subcategory': str(decision.get('target_subcategory') or ''),
                'created_session_id': session_id,
            })
        if not rows:
            return
        (
            CoherenceReviewDecision
            .insert_many(rows)
            .on_conflict(
                conflict_target=[CoherenceReviewDecision.source_path],
                update={
                    CoherenceReviewDecision.file_hash: EXCLUDED.file_hash,
                    CoherenceReviewDecision.decision_type: EXCLUDED.decision_type,
                    CoherenceReviewDecision.current_audio_type: EXCLUDED.current_audio_type,
                    CoherenceReviewDecision.current_category: EXCLUDED.current_category,
                    CoherenceReviewDecision.current_subcategory: EXCLUDED.current_subcategory,
                    CoherenceReviewDecision.target_audio_type: EXCLUDED.target_audio_type,
                    CoherenceReviewDecision.target_category: EXCLUDED.target_category,
                    CoherenceReviewDecision.target_subcategory: EXCLUDED.target_subcategory,
                    CoherenceReviewDecision.created_session_id: EXCLUDED.created_session_id,
                    CoherenceReviewDecision.updated_at: SQL('CURRENT_TIMESTAMP'),
                },
            )
            .execute()
        )

    def list_coherence_review_decisions(
            self,
            *,
            source_paths: list[str] | None = None,
            file_hashes: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        normalized_paths = sorted(
            {_normalized_source_path(path) for path in source_paths or [] if (path or "").strip()})
        hashes = sorted({(item or "").strip() for item in file_hashes or [] if (item or "").strip()})
        rows_by_path: dict[str, dict[str, Any]] = {}

        def fetch_in(field, values: list[str]) -> None:
            for start in range(0, len(values), 800):
                chunk = values[start:start + 800]
                if not chunk:
                    continue
                query = (
                    CoherenceReviewDecision.select()
                    .where(field.in_(chunk))
                    .order_by(CoherenceReviewDecision.updated_at.desc())
                    .dicts()
                )
                for payload in query:
                    rows_by_path.setdefault(_normalized_source_path(payload.get("source_path")), payload)

        fetch_in(CoherenceReviewDecision.source_path, normalized_paths)
        fetch_in(CoherenceReviewDecision.file_hash, hashes)
        return list(rows_by_path.values())

    def upsert_anchor_candidates(self, session_id: str, anchors: list[Any]) -> None:
        AnchorProfile.delete().where(
            (AnchorProfile.session_id == session_id) & (AnchorProfile.state == AnchorProfileState.CANDIDATE)
        ).execute()
        if not anchors:
            return
        self._upsert_anchor_profiles(session_id, anchors, update_state=False)

    def upsert_anchor_profile_rows(self, session_id: str, rows: list[dict[str, Any]]) -> None:
        _rows = [
            {
                'session_id': session_id,
                'anchor_id': row.get('anchor_id'),
                'audio_type': row.get('audio_type'),
                'category': row.get('category'),
                'subcategory': row.get('subcategory'),
                'cluster_id': row.get('cluster_id'),
                'feature_space_version': row.get('feature_space_version'),
                'extractor_version': row.get('extractor_version'),
                'feature_schema_json': row.get('feature_schema_json'),
                'medoid_vector': normalize_feature_vector(row.get('medoid_vector')),
                'cluster_centroid': normalize_feature_vector(row.get('cluster_centroid')),
                'cluster_std': normalize_feature_vector(row.get('cluster_std')),
                'coherence_radius': float(row.get('coherence_radius') or 0.0),
                'n_reference_items': int(row.get('n_reference_items') or 0),
                'state': row.get('state') or AnchorProfileState.CANDIDATE,
                'profile_json': row.get('profile_json'),
            }
            for row in rows
            if row.get('anchor_id')
        ]
        if not _rows:
            return
        (
            AnchorProfile
            .insert_many(_rows)
            .on_conflict(
                conflict_target=[AnchorProfile.session_id, AnchorProfile.anchor_id],
                update={
                    AnchorProfile.audio_type: EXCLUDED.audio_type,
                    AnchorProfile.category: EXCLUDED.category,
                    AnchorProfile.subcategory: EXCLUDED.subcategory,
                    AnchorProfile.cluster_id: EXCLUDED.cluster_id,
                    AnchorProfile.feature_space_version: EXCLUDED.feature_space_version,
                    AnchorProfile.extractor_version: EXCLUDED.extractor_version,
                    AnchorProfile.feature_schema_json: EXCLUDED.feature_schema_json,
                    AnchorProfile.medoid_vector: EXCLUDED.medoid_vector,
                    AnchorProfile.cluster_centroid: EXCLUDED.cluster_centroid,
                    AnchorProfile.cluster_std: EXCLUDED.cluster_std,
                    AnchorProfile.coherence_radius: EXCLUDED.coherence_radius,
                    AnchorProfile.n_reference_items: EXCLUDED.n_reference_items,
                    AnchorProfile.state: EXCLUDED.state,
                    AnchorProfile.profile_json: EXCLUDED.profile_json,
                    AnchorProfile.updated_at: SQL('CURRENT_TIMESTAMP'),
                },
            )
            .execute()
        )

    def _upsert_anchor_profiles(self, session_id: str, anchors: list[Any], *, update_state: bool) -> None:
        if not anchors:
            return
        rows = [
            {
                'session_id': session_id,
                'anchor_id': anchor.anchor_id,
                'audio_type': getattr(anchor, 'audio_type', ''),
                'category': anchor.category,
                'subcategory': anchor.subcategory,
                'cluster_id': anchor.cluster_id,
                'feature_space_version': anchor.feature_space_version,
                'extractor_version': anchor.extractor_version,
                'feature_schema_json': json.dumps(list(anchor.vector_schema)),
                'medoid_vector': normalize_feature_vector(anchor.medoid_vector),
                'cluster_centroid': normalize_feature_vector(anchor.cluster_centroid),
                'cluster_std': normalize_feature_vector(anchor.cluster_std),
                'coherence_radius': float(anchor.coherence_radius),
                'n_reference_items': int(anchor.n_reference_items),
                'state': anchor.state,
                'profile_json': json.dumps(anchor.profile_payload or {}),
            }
            for anchor in anchors
        ]
        update = {
            AnchorProfile.audio_type: EXCLUDED.audio_type,
            AnchorProfile.category: EXCLUDED.category,
            AnchorProfile.subcategory: EXCLUDED.subcategory,
            AnchorProfile.cluster_id: EXCLUDED.cluster_id,
            AnchorProfile.feature_space_version: EXCLUDED.feature_space_version,
            AnchorProfile.extractor_version: EXCLUDED.extractor_version,
            AnchorProfile.feature_schema_json: EXCLUDED.feature_schema_json,
            AnchorProfile.medoid_vector: EXCLUDED.medoid_vector,
            AnchorProfile.cluster_centroid: EXCLUDED.cluster_centroid,
            AnchorProfile.cluster_std: EXCLUDED.cluster_std,
            AnchorProfile.coherence_radius: EXCLUDED.coherence_radius,
            AnchorProfile.n_reference_items: EXCLUDED.n_reference_items,
            AnchorProfile.profile_json: EXCLUDED.profile_json,
            AnchorProfile.updated_at: SQL('CURRENT_TIMESTAMP'),
        }
        if update_state:
            update[AnchorProfile.state] = EXCLUDED.state
        (
            AnchorProfile
            .insert_many(rows)
            .on_conflict(
                conflict_target=[AnchorProfile.session_id, AnchorProfile.anchor_id],
                update=update,
            )
            .execute()
        )

    def list_anchor_candidates(self, session_id: str, state: Optional[str] = None) -> list[dict[str, Any]]:
        query = AnchorProfile.select().where(AnchorProfile.session_id == session_id)
        if state:
            query = query.where(AnchorProfile.state == state)
        return list(
            query.order_by(
                AnchorProfile.audio_type, AnchorProfile.category, AnchorProfile.subcategory
            ).dicts()
        )

    def ensure_verified_anchors_for_session(self, session_id: str) -> int:
        removed_verified_anchor_ids = {
            str(row.anchor_id)
            for row in AnchorProfile.select(AnchorProfile.anchor_id.distinct()).where(
                (AnchorProfile.state == AnchorProfileState.IGNORED)
                & (AnchorProfile.session_id == REMOVED_VERIFIED_ANCHOR_SESSION)
            )
        }
        rows = list(
            AnchorProfile.select().where(
                (AnchorProfile.state.in_([AnchorProfileState.VERIFIED, AnchorProfileState.SYSTEM]))
                & (AnchorProfile.session_id != session_id)
            ).order_by(AnchorProfile.updated_at.desc())
        )
        if not rows:
            return 0
        existing = {
            str(row.anchor_id): str(row.state or "")
            for row in AnchorProfile.select(AnchorProfile.anchor_id, AnchorProfile.state).where(
                AnchorProfile.session_id == session_id
            )
        }
        copied = 0
        seen: set[str] = set()
        insert_rows = []
        for row in rows:
            anchor_id = str(row.anchor_id)
            if (
                    not anchor_id
                    or anchor_id in removed_verified_anchor_ids
                    or existing.get(anchor_id) in ("verified", "system", "ignored")
                    or anchor_id in seen
            ):
                continue
            seen.add(anchor_id)
            copied += 1
            insert_rows.append({
                'session_id': session_id,
                'anchor_id': anchor_id,
                'audio_type': row.audio_type,
                'category': row.category,
                'subcategory': row.subcategory,
                'cluster_id': row.cluster_id,
                'feature_space_version': row.feature_space_version,
                'extractor_version': row.extractor_version,
                'feature_schema_json': row.feature_schema_json,
                'medoid_vector': row.medoid_vector,
                'cluster_centroid': row.cluster_centroid,
                'cluster_std': row.cluster_std,
                'coherence_radius': row.coherence_radius,
                'n_reference_items': row.n_reference_items,
                'state': row.state,
                'profile_json': row.profile_json,
            })
        if insert_rows:
            (
                AnchorProfile
                .insert_many(insert_rows)
                .on_conflict(
                    conflict_target=[AnchorProfile.session_id, AnchorProfile.anchor_id],
                    update={
                        AnchorProfile.audio_type: EXCLUDED.audio_type,
                        AnchorProfile.category: EXCLUDED.category,
                        AnchorProfile.subcategory: EXCLUDED.subcategory,
                        AnchorProfile.cluster_id: EXCLUDED.cluster_id,
                        AnchorProfile.feature_space_version: EXCLUDED.feature_space_version,
                        AnchorProfile.extractor_version: EXCLUDED.extractor_version,
                        AnchorProfile.feature_schema_json: EXCLUDED.feature_schema_json,
                        AnchorProfile.medoid_vector: EXCLUDED.medoid_vector,
                        AnchorProfile.cluster_centroid: EXCLUDED.cluster_centroid,
                        AnchorProfile.cluster_std: EXCLUDED.cluster_std,
                        AnchorProfile.coherence_radius: EXCLUDED.coherence_radius,
                        AnchorProfile.n_reference_items: EXCLUDED.n_reference_items,
                        AnchorProfile.state: EXCLUDED.state,
                        AnchorProfile.profile_json: EXCLUDED.profile_json,
                        AnchorProfile.updated_at: SQL('CURRENT_TIMESTAMP'),
                    },
                    where=(AnchorProfile.state != AnchorProfileState.IGNORED),
                )
                .execute()
            )
        return copied

    def set_anchor_candidate_state(self, session_id: str, anchor_ids: list[str], state: str) -> None:
        if not anchor_ids:
            return
        AnchorProfile.update(
            state=state, updated_at=SQL('CURRENT_TIMESTAMP')
        ).where(
            (AnchorProfile.session_id == session_id) & (AnchorProfile.anchor_id.in_(anchor_ids))
        ).execute()

    def remove_verified_anchor_profiles(self, session_id: str, anchor_ids: list[str]) -> None:
        if not anchor_ids:
            return
        AnchorProfile.update(
            state=AnchorProfileState.IGNORED, updated_at=SQL('CURRENT_TIMESTAMP')
        ).where(
            (AnchorProfile.anchor_id.in_(anchor_ids))
            & (AnchorProfile.session_id != '__system__')
            & (AnchorProfile.state == AnchorProfileState.VERIFIED)
        ).execute()
        AnchorProfile.update(
            state=AnchorProfileState.IGNORED, updated_at=SQL('CURRENT_TIMESTAMP')
        ).where(
            (AnchorProfile.session_id == session_id)
            & (AnchorProfile.anchor_id.in_(anchor_ids))
            & (AnchorProfile.state != AnchorProfileState.SYSTEM)
        ).execute()
        removed_rows = [
            {'session_id': REMOVED_VERIFIED_ANCHOR_SESSION, 'anchor_id': anchor_id, 'state': AnchorProfileState.IGNORED}
            for anchor_id in anchor_ids
        ]
        (
            AnchorProfile
            .insert_many(removed_rows)
            .on_conflict(
                conflict_target=[AnchorProfile.session_id, AnchorProfile.anchor_id],
                update={
                    AnchorProfile.state: EXCLUDED.state,
                    AnchorProfile.updated_at: SQL('CURRENT_TIMESTAMP'),
                },
            )
            .execute()
        )

    def repair_anchor_profile_json(
            self,
            session_id: str,
            anchor_ids: list[str],
            payload_builder,
    ) -> list[str]:
        """Reconstruct profile_json from binary columns for anchors where it is
        NULL or empty.  Returns the anchor_ids that could not be repaired.
        Callers should treat a non-empty return value as a hard failure."""
        if not anchor_ids:
            return []

        rows = (
            AnchorProfile.select(
                AnchorProfile.anchor_id, AnchorProfile.audio_type, AnchorProfile.category,
                AnchorProfile.subcategory, AnchorProfile.cluster_id, AnchorProfile.feature_space_version,
                AnchorProfile.extractor_version, AnchorProfile.feature_schema_json, AnchorProfile.medoid_vector,
                AnchorProfile.cluster_centroid, AnchorProfile.cluster_std, AnchorProfile.coherence_radius,
                AnchorProfile.n_reference_items, AnchorProfile.profile_json,
            )
            .where((AnchorProfile.session_id == session_id) & (AnchorProfile.anchor_id.in_(anchor_ids)))
            .dicts()
        )

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

        for payload_json, anchor_id in to_update:
            AnchorProfile.update(
                profile_json=payload_json, updated_at=SQL('CURRENT_TIMESTAMP')
            ).where(
                (AnchorProfile.session_id == session_id) & (AnchorProfile.anchor_id == anchor_id)
            ).execute()

        return failed

    def seed_system_anchors(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        _rows = [
            {
                'session_id': '__system__',
                'anchor_id': row['anchor_id'],
                'audio_type': row['audio_type'],
                'category': row['category'],
                'subcategory': row['subcategory'],
                'cluster_id': row['cluster_id'],
                'feature_space_version': row['feature_space_version'],
                'extractor_version': row['extractor_version'],
                'feature_schema_json': row['feature_schema_json'],
                'medoid_vector': row['medoid_vector'],
                'cluster_centroid': row['cluster_centroid'],
                'cluster_std': row['cluster_std'],
                'coherence_radius': float(row['coherence_radius'] or 0.0),
                'n_reference_items': int(row['n_reference_items'] or 0),
                'state': AnchorProfileState.SYSTEM,
                'profile_json': row['profile_json'],
            }
            for row in rows
        ]
        (
            AnchorProfile
            .insert_many(_rows)
            .on_conflict(
                conflict_target=[AnchorProfile.session_id, AnchorProfile.anchor_id],
                update={
                    AnchorProfile.audio_type: EXCLUDED.audio_type,
                    AnchorProfile.category: EXCLUDED.category,
                    AnchorProfile.subcategory: EXCLUDED.subcategory,
                    AnchorProfile.cluster_id: EXCLUDED.cluster_id,
                    AnchorProfile.feature_space_version: EXCLUDED.feature_space_version,
                    AnchorProfile.extractor_version: EXCLUDED.extractor_version,
                    AnchorProfile.feature_schema_json: EXCLUDED.feature_schema_json,
                    AnchorProfile.medoid_vector: EXCLUDED.medoid_vector,
                    AnchorProfile.cluster_centroid: EXCLUDED.cluster_centroid,
                    AnchorProfile.cluster_std: EXCLUDED.cluster_std,
                    AnchorProfile.coherence_radius: EXCLUDED.coherence_radius,
                    AnchorProfile.n_reference_items: EXCLUDED.n_reference_items,
                    AnchorProfile.state: AnchorProfileState.SYSTEM,
                    AnchorProfile.profile_json: EXCLUDED.profile_json,
                    AnchorProfile.updated_at: SQL('CURRENT_TIMESTAMP'),
                },
            )
            .execute()
        )

    def coherence_cache_stats(self, session_id: str) -> dict[str, int]:
        row = self._connection.execute(""" SELECT COUNT(*)    AS result_count,
                                            COALESCE(SUM(NOT EXISTS (SELECT 1
                                                                     FROM staging_records AS staging
                                                                     WHERE staging.session_id = result.session_id
                                                                       AND CAST(staging.row_id AS TEXT) = result.record_id)),
                                                     0) AS missing_count
                                     FROM coherence_results AS result
                                     WHERE result.session_id = ? """, (session_id,), ).fetchone()
        return {"result_count": int(row[0] if row is not None else 0),
                "missing_count": int(row[1] if row is not None else 0), }

    def append_coherence_group(
            self,
            session_id: str,
            results: List[Any],
            candidates: List[Any] | None = None,
            anchors: List[Any] | None = None,
    ) -> None:
        def _write() -> None:
            self.append_coherence_results(session_id, results)
            self.append_refinement_candidates(session_id, list(candidates or ()))
            self.append_anchor_candidates(session_id, list(anchors or ()))

        self._with_write_retry(_write)

    def append_coherence_results(self, session_id: str, results: list[Any]) -> None:
        if not results:
            return
        cursor = self._db.cursor()
        cursor.executemany(
            """
            INSERT INTO coherence_results (session_id, record_id, category, subcategory, coherence_status,
                                           coherence_score, cluster_id, is_outlier, review_reason,
                                           suggested_alternate_category, suggested_alternate_subcategory,
                                           nearest_neighbor_summary_json, anchor_fit_status, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(session_id, record_id) DO UPDATE SET category=excluded.category,
                                                             subcategory=excluded.subcategory,
                                                             coherence_status=excluded.coherence_status,
                                                             coherence_score=excluded.coherence_score,
                                                             cluster_id=excluded.cluster_id,
                                                             is_outlier=excluded.is_outlier,
                                                             review_reason=excluded.review_reason,
                                                             suggested_alternate_category=excluded.suggested_alternate_category,
                                                             suggested_alternate_subcategory=excluded.suggested_alternate_subcategory,
                                                             nearest_neighbor_summary_json=excluded.nearest_neighbor_summary_json,
                                                             anchor_fit_status=excluded.anchor_fit_status,
                                                             updated_at=CURRENT_TIMESTAMP
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

    def append_anchor_candidates(self, session_id: str, anchors: list[Any]) -> None:
        if not anchors:
            return
        self._upsert_anchor_profiles(session_id, anchors, update_state=False)

    def append_refinement_candidates(self, session_id: str, candidates: list[Any]) -> None:
        if not candidates:
            return
        cursor = self._db.cursor()
        cursor.executemany.executemany(
            """
            INSERT INTO refinement_candidates (session_id, candidate_id, record_id, current_audio_type,
                                               current_category,
                                               current_subcategory, suggested_audio_type, suggested_category,
                                               suggested_subcategory,
                                               evidence, coherence_status, confidence_score, state, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(session_id, candidate_id) DO UPDATE SET record_id=excluded.record_id,
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
                                                                          WHEN refinement_candidates.state IN ('accepted', 'ignored')
                                                                              THEN refinement_candidates.state
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