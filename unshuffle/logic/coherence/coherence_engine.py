from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import Any, Iterable, Protocol

import numpy as np

from ...audio import SimilarityEngine
from ...core.features import (
    DEFAULT_DISTANCE_WEIGHTS,
    CURRENT_FEATURE_VECTOR_SIZE,
    IDX_ACTIVE_DURATION,
    IDX_BRIGHTNESS,
    IDX_CHROMA_START,
    IDX_DECAY,
    IDX_FFT_REGISTER,
    IDX_PERCUSSIVITY,
    IDX_ZCR,
    SILENCE_THRESHOLD,
    normalize_distance_vector,
)
from .models import (
    COHERENCE_STATUS_CLUSTERED,
    COHERENCE_STATUS_LOW,
    COHERENCE_STATUS_MISCATEGORIZATION,
    COHERENCE_STATUS_STABLE,
    COHERENCE_STATUS_UNDERREPRESENTED,
    CoherenceRecord,
    CoherenceResult,
    RefinementCandidate,
    REFINEMENT_AUTO_STAGED,
    REFINEMENT_PENDING,
)
from .formatting import (
    _anchor_matches_group,
    _bucket_label,
    _profile_float,
    _profile_vector,
    _slug,
)
from .density import clamp_density_ratio, outlier_iqr_multiplier
from .refinement_candidates import refinement_candidates_for_engine


def _safe_anchor_radius(value) -> float:
    try:
        radius = float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return radius if math.isfinite(radius) else 0.0


class _SimilarityDistanceEngine(Protocol):
    def calculate_distance(self, v1: list[float], v2: list[float], d1: float = 0.0, d2: float = 0.0) -> float:
        ...


_GroupContext = dict[str, dict[str, float | str]]

NORMAL_IMPROVEMENT_THRESHOLD = 0.15
STRONG_IMPROVEMENT_THRESHOLD = 0.35
STRONG_TOKEN_CONFIDENCE_FLOOR_RATIO = 0.45
STRONG_WINNER_MARGIN_RATIO = 1.45
ANCHOR_CLOSE_RADIUS_MULTIPLIER = 1.25
DENSITY_EPSILON = 1e-6
DENSITY_RATIO_MIN = 0.25
DENSITY_RATIO_MAX = 4.0
OUTLIER_IQR_BASE_MULTIPLIER = 1.5
OUTLIER_IQR_MIN_MULTIPLIER = 0.9
OUTLIER_IQR_MAX_MULTIPLIER = 2.4
CLUSTER_ADJACENCY_RATIO_CLOSE = 0.75
CLUSTER_ADJACENCY_TRAVEL_PENALTY = 0.65
class CoherenceEngine:
    """Pure acoustic graph audit over already-classified records."""

    def __init__(
        self,
        similarity_engine: _SimilarityDistanceEngine | None = None,
        verified_anchors: Iterable[dict] | None = None,
    ):
        self.similarity_engine = similarity_engine or SimilarityEngine()
        self.verified_anchors = list(verified_anchors or [])
        self._vector_input_cache: dict[tuple[tuple[str, int, int], ...], dict[str, np.ndarray]] = {}
        self._global_spatial_index_cache: dict[int, Any] = {}
        self._anchor_match_cache: dict[tuple[str, str, str], list[dict]] = {}

    def audit(self, records: Iterable[CoherenceRecord]) -> tuple[list[CoherenceResult], list[RefinementCandidate]]:
        records = list(records)
        by_group: dict[tuple[str, str, str], list[CoherenceRecord]] = defaultdict(list)
        for record in records:
            by_group[(record.audio_type, record.category, record.subcategory)].append(record)

        results: list[CoherenceResult] = []
        group_context: _GroupContext = {}
        cluster_profiles: list[dict[str, Any]] = []
        for group_key, group_records in by_group.items():
            group_results, medoid_distances, group_profiles = self._audit_group(group_key, group_records)
            results.extend(group_results)
            group_context.update(medoid_distances)
            cluster_profiles.extend(group_profiles)

        results = self._with_cluster_adjacency_summaries(results, cluster_profiles)
        candidates = self._refinement_candidates(records, results, group_context, cluster_profiles)
        if candidates:
            candidate_by_record = {candidate.record_id: candidate for candidate in candidates}
            updated: list[CoherenceResult] = []
            for result in results:
                candidate = candidate_by_record.get(result.record_id)
                if candidate is None:
                    updated.append(result)
                    continue
                updated.append(
                    CoherenceResult(
                        record_id=result.record_id,
                        category=result.category,
                        subcategory=result.subcategory,
                        coherence_status=COHERENCE_STATUS_MISCATEGORIZATION,
                        coherence_score=result.coherence_score,
                        cluster_id=result.cluster_id,
                        is_outlier=True,
                        review_reason=candidate.evidence,
                        suggested_alternate_category=candidate.suggested_category,
                        suggested_alternate_subcategory=candidate.suggested_subcategory,
                        nearest_neighbor_summary=result.nearest_neighbor_summary,
                        anchor_fit_status=result.anchor_fit_status,
                    )
                )
            results = updated
        return results, candidates

    def _audit_group(
        self,
        group_key: tuple[str, str, str],
        records: list[CoherenceRecord],
    ) -> tuple[list[CoherenceResult], _GroupContext, list[dict[str, Any]]]:
        audio_type, category, subcategory = group_key
        n = len(records)
        if n < 3:
            anchors = self._matching_anchors(audio_type, category, subcategory)
            return (
                [self._underrepresented_result(record, category, subcategory, anchors) for record in records],
                {},
                [],
            )

        distances = self._pairwise_distances(records)
        k = min(10, max(3, int(math.sqrt(n))))
        k = min(k, n - 1)
        if hasattr(distances, "nearest"):
            nearest = distances.nearest
        else:
            nearest = np.argsort(distances, axis=1)[:, 1 : k + 1]
        W = self._knn_similarity(distances, nearest)
        clusters = self._cluster_labels(W, n) if n >= 8 else np.zeros(n, dtype=int)
        cluster_medoids = self._cluster_medoid_indexes(clusters, distances)
        density_ratios = self._cluster_density_ratios(clusters, distances, cluster_medoids)
        cluster_profiles = self._cluster_profiles(group_key, records, distances, clusters, cluster_medoids)
        outliers = self._outlier_mask(records, distances, nearest, clusters, W, cluster_medoids, density_ratios)
        cluster_count = len(set(int(label) for label in clusters))
        statuses = []
        medoid_context: _GroupContext = {}
        for idx, record in enumerate(records):
            label = int(clusters[idx])
            cluster_id = f"{_slug(audio_type)}_{_slug(category)}_{_slug(subcategory)}_{label:03d}"
            medoid_distance = float(distances[idx, cluster_medoids[label]])
            mean_neighbor = float(np.mean(distances[idx, nearest[idx]])) if len(nearest[idx]) else 0.0
            coherence_score = max(0.0, 1.0 / (1.0 + mean_neighbor + medoid_distance))
            medoid_context[record.record_id] = {
                "assigned_medoid_distance": medoid_distance,
                "mean_neighbor_distance": mean_neighbor,
                "cluster_density_ratio": density_ratios.get(label, 1.0),
                "cluster_id": cluster_id,
            }
            if outliers[idx]:
                anchor_fit = self._anchor_fit(record, audio_type, category, subcategory)
                if anchor_fit["close"]:
                    status = COHERENCE_STATUS_STABLE
                    reason = None
                    anchor_fit_status = "close"
                    is_outlier = False
                else:
                    status = COHERENCE_STATUS_LOW
                    reason = f"Low acoustic coherence inside {category}/{subcategory}"
                    anchor_fit_status = str(anchor_fit["status"])
                    is_outlier = True
            elif cluster_count > 1:
                status = COHERENCE_STATUS_CLUSTERED
                reason = f"Coherent cluster {label + 1} inside {category}/{subcategory}"
                anchor_fit_status = None
                is_outlier = False
            else:
                status = COHERENCE_STATUS_STABLE
                reason = None
                anchor_fit_status = None
                is_outlier = False
            statuses.append(
                CoherenceResult(
                    record_id=record.record_id,
                    category=category,
                    subcategory=subcategory,
                    coherence_status=status,
                    coherence_score=round(coherence_score, 6),
                    cluster_id=cluster_id,
                    is_outlier=is_outlier,
                    review_reason=reason,
                    nearest_neighbor_summary={
                        "mean_neighbor_distance": round(mean_neighbor, 6),
                        "distance_to_cluster_medoid": round(medoid_distance, 6),
                        "cluster_density_ratio": round(density_ratios.get(label, 1.0), 6),
                    },
                    anchor_fit_status=anchor_fit_status,
                )
            )
        return statuses, medoid_context, cluster_profiles

    def _underrepresented_result(
        self,
        record: CoherenceRecord,
        category: str,
        subcategory: str,
        anchors: list[dict],
    ) -> CoherenceResult:
        if not anchors:
            return CoherenceResult(
                record_id=record.record_id,
                category=category,
                subcategory=subcategory,
                coherence_status=COHERENCE_STATUS_UNDERREPRESENTED,
                coherence_score=0.0,
                review_reason="Not enough files and no verified anchor available",
                anchor_fit_status="no_anchor",
            )
        best_distance = float("inf")
        best_radius = 0.0
        for anchor in anchors:
            vector = anchor.get("medoid_vector")
            if not vector:
                continue
            distance = self.similarity_engine.calculate_distance(record.vector, vector)
            if math.isfinite(distance) and distance < best_distance:
                best_distance = distance
                best_radius = _safe_anchor_radius(anchor.get("coherence_radius"))
        if best_radius > 0 and best_distance <= best_radius * ANCHOR_CLOSE_RADIUS_MULTIPLIER:
            return CoherenceResult(
                record_id=record.record_id,
                category=category,
                subcategory=subcategory,
                coherence_status=COHERENCE_STATUS_STABLE,
                coherence_score=round(1.0 / (1.0 + best_distance), 6),
                anchor_fit_status="close",
            )
        return CoherenceResult(
            record_id=record.record_id,
            category=category,
            subcategory=subcategory,
            coherence_status=COHERENCE_STATUS_LOW,
            coherence_score=0.0,
            review_reason="Not enough files and distant from verified anchor",
            anchor_fit_status="distant",
        )

    def _matching_anchors(self, audio_type: str, category: str, subcategory: str) -> list[dict]:
        key = (audio_type or "", category or "", subcategory or "")
        cached = self._anchor_match_cache.get(key)
        if cached is not None:
            return cached
        anchors = [
            anchor for anchor in self.verified_anchors
            if _anchor_matches_group(anchor, audio_type, category, subcategory)
        ]
        self._anchor_match_cache[key] = anchors
        return anchors

    def _pairwise_distances(self, records: list[CoherenceRecord]) -> np.ndarray | Any:
        n = len(records)
        k = min(10, max(3, int(math.sqrt(n))))
        k = min(k, n - 1)
        if n >= 3000:
            try:
                from .spatial_index import SparsePairwiseDistances
                return SparsePairwiseDistances(records, self, k)
            except ModuleNotFoundError:
                pass
            
        vectorized = self._pairwise_distances_vectorized(records)
        if vectorized is not None:
            return vectorized
        distances = np.zeros((n, n), dtype=float)
        for i in range(n):
            for j in range(i + 1, n):
                distance = self.similarity_engine.calculate_distance(records[i].vector, records[j].vector)
                if not math.isfinite(distance):
                    distance = 1e9
                distances[i, j] = distance
                distances[j, i] = distance
        return distances

    def _pairwise_distances_vectorized(self, records: list[CoherenceRecord]) -> np.ndarray | None:
        if any(len(record.vector) != CURRENT_FEATURE_VECTOR_SIZE for record in records):
            return None
        weights = getattr(self.similarity_engine, "weights", None)
        if weights is None:
            return None
        required = set(DEFAULT_DISTANCE_WEIGHTS)
        if any(key not in weights for key in required):
            return None
        inputs = self._vectorized_inputs(records)
        if inputs is None:
            return None
        vectors = inputs["vectors"]

        n = vectors.shape[0]
        distances = np.empty((n, n), dtype=np.float32)
        chunk_size = max(128, min(512, int(96_000_000 / max(n, 1))))
        chroma = inputs["chroma"]
        chroma_norm = inputs["chroma_norm"]
        tonalness = inputs["tonalness"]
        silent_feature = inputs["silent_feature"].astype(bool, copy=False)
        fully_silent = inputs["fully_silent"].astype(bool, copy=False)

        for start in range(0, n, chunk_size):
            stop = min(n, start + chunk_size)
            left = vectors[start:stop]
            block = (
                float(weights["brightness"]) * np.abs(left[:, [IDX_BRIGHTNESS]] - vectors[:, IDX_BRIGHTNESS])
                + float(weights["percussivity"]) * np.abs(left[:, [IDX_PERCUSSIVITY]] - vectors[:, IDX_PERCUSSIVITY])
                + float(weights["fft_register"]) * np.abs(left[:, [IDX_FFT_REGISTER]] - vectors[:, IDX_FFT_REGISTER])
                + float(weights["zcr"]) * np.abs(left[:, [IDX_ZCR]] - vectors[:, IDX_ZCR])
                + float(weights["decay"]) * np.abs(left[:, [IDX_DECAY]] - vectors[:, IDX_DECAY])
                + float(weights["tonalness"]) * np.abs(tonalness[start:stop, None] - tonalness[None, :])
            )

            dot = chroma[start:stop] @ chroma.T
            denom = chroma_norm[start:stop, None] * chroma_norm[None, :]
            cosine = np.ones_like(block, dtype=np.float32)
            valid = denom >= 1e-9
            cosine[valid] = 1.0 - np.clip(dot[valid] / denom[valid], -1.0, 1.0)
            both_empty = (chroma_norm[start:stop, None] < 1e-9) & (chroma_norm[None, :] < 1e-9)
            cosine[both_empty] = 0.0
            avg_percussivity = (left[:, [IDX_PERCUSSIVITY]] + vectors[:, IDX_PERCUSSIVITY]) / 2.0
            avg_tonalness = (tonalness[start:stop, None] + tonalness[None, :]) / 2.0
            chroma_weight = float(weights["chroma"]) * avg_tonalness * (1.0 - avg_percussivity)
            block += chroma_weight * cosine

            durations = vectors[:, IDX_ACTIVE_DURATION]
            left_duration = left[:, [IDX_ACTIVE_DURATION]]
            duration_valid = (left_duration > 0) & (durations[None, :] > 0)
            max_duration = np.maximum(left_duration, durations[None, :])
            length_penalty = np.zeros_like(block, dtype=np.float32)
            length_penalty[duration_valid] = np.minimum(
                np.abs(left_duration - durations[None, :])[duration_valid] / max_duration[duration_valid],
                1.0,
            )
            block += float(weights["active_duration"]) * length_penalty

            any_silent = silent_feature[start:stop, None] | silent_feature[None, :]
            same_silence = fully_silent[start:stop, None] == fully_silent[None, :]
            block = np.where(any_silent, np.where(same_silence, 0.0, 2.0), block)
            distances[start:stop] = block

        np.fill_diagonal(distances, 0.0)
        return distances.astype(float, copy=False)

    def _distances_from_vectorized(self, left: list[float], records: list[CoherenceRecord]) -> np.ndarray | None:
        if len(left) != CURRENT_FEATURE_VECTOR_SIZE or any(len(record.vector) != CURRENT_FEATURE_VECTOR_SIZE for record in records):
            return None
        weights = getattr(self.similarity_engine, "weights", None)
        if weights is None:
            return None
        if any(key not in weights for key in DEFAULT_DISTANCE_WEIGHTS):
            return None
        try:
            left_vec = np.asarray(normalize_distance_vector(left), dtype=np.float32)
        except (TypeError, ValueError):
            return None
        inputs = self._vectorized_inputs(records)
        if inputs is None:
            return None
        vectors = inputs["vectors"]
        if left_vec.ndim != 1 or left_vec.shape[0] != CURRENT_FEATURE_VECTOR_SIZE or not np.isfinite(left_vec).all():
            return None

        left_chroma = left_vec[IDX_CHROMA_START : IDX_CHROMA_START + 12]
        chroma = inputs["chroma"]
        left_norm = np.linalg.norm(left_chroma)
        chroma_norm = inputs["chroma_norm"]
        tonalness = inputs["tonalness"]
        left_tonalness = max(0.0, 1.0 - float(left_chroma.mean()))
        distances = (
            float(weights["brightness"]) * np.abs(left_vec[IDX_BRIGHTNESS] - vectors[:, IDX_BRIGHTNESS])
            + float(weights["percussivity"]) * np.abs(left_vec[IDX_PERCUSSIVITY] - vectors[:, IDX_PERCUSSIVITY])
            + float(weights["fft_register"]) * np.abs(left_vec[IDX_FFT_REGISTER] - vectors[:, IDX_FFT_REGISTER])
            + float(weights["zcr"]) * np.abs(left_vec[IDX_ZCR] - vectors[:, IDX_ZCR])
            + float(weights["decay"]) * np.abs(left_vec[IDX_DECAY] - vectors[:, IDX_DECAY])
            + float(weights["tonalness"]) * np.abs(left_tonalness - tonalness)
        )
        denom = left_norm * chroma_norm
        cosine = np.ones(vectors.shape[0], dtype=np.float32)
        valid = denom >= 1e-9
        if valid.any():
            cosine[valid] = 1.0 - np.clip((chroma[valid] @ left_chroma) / denom[valid], -1.0, 1.0)
        cosine[(left_norm < 1e-9) & (chroma_norm < 1e-9)] = 0.0
        avg_percussivity = (left_vec[IDX_PERCUSSIVITY] + vectors[:, IDX_PERCUSSIVITY]) / 2.0
        avg_tonalness = (left_tonalness + tonalness) / 2.0
        distances += float(weights["chroma"]) * avg_tonalness * (1.0 - avg_percussivity) * cosine

        left_duration = left_vec[IDX_ACTIVE_DURATION]
        durations = vectors[:, IDX_ACTIVE_DURATION]
        duration_valid = (left_duration > 0) & (durations > 0)
        if duration_valid.any():
            max_duration = np.maximum(left_duration, durations[duration_valid])
            penalty = np.minimum(np.abs(left_duration - durations[duration_valid]) / max_duration, 1.0)
            distances[duration_valid] += float(weights["active_duration"]) * penalty

        left_silent_feature = float(left_vec[:IDX_CHROMA_START].sum()) < SILENCE_THRESHOLD
        silent_feature = inputs["silent_feature"].astype(bool, copy=False)
        any_silent = left_silent_feature | silent_feature
        if np.any(any_silent):
            left_fully_silent = float(left_vec.sum()) < SILENCE_THRESHOLD
            fully_silent = inputs["fully_silent"].astype(bool, copy=False)
            distances = np.where(any_silent, np.where(left_fully_silent == fully_silent, 0.0, 2.0), distances)
        return distances.astype(float, copy=False)

    def _vectorized_inputs(self, records: list[CoherenceRecord]) -> dict[str, np.ndarray] | None:
        if any(len(record.vector) != CURRENT_FEATURE_VECTOR_SIZE for record in records):
            return None
        key = tuple((record.record_id, id(record.vector), len(record.vector)) for record in records)
        cached = self._vector_input_cache.get(key)
        if cached is not None:
            return cached
        try:
            vectors = np.asarray([normalize_distance_vector(record.vector) for record in records], dtype=np.float32)
        except (TypeError, ValueError):
            return None
        if vectors.ndim != 2 or vectors.shape[1] != CURRENT_FEATURE_VECTOR_SIZE or not np.isfinite(vectors).all():
            return None
        chroma = vectors[:, IDX_CHROMA_START : IDX_CHROMA_START + 12]
        payload = {
            "vectors": vectors,
            "chroma": chroma,
            "chroma_norm": np.linalg.norm(chroma, axis=1),
            "tonalness": np.maximum(0.0, 1.0 - chroma.mean(axis=1)),
            "silent_feature": vectors[:, :IDX_CHROMA_START].sum(axis=1) < SILENCE_THRESHOLD,
            "fully_silent": vectors.sum(axis=1) < SILENCE_THRESHOLD,
        }
        self._vector_input_cache[key] = payload
        return payload

    def _knn_similarity(self, distances: np.ndarray, nearest: np.ndarray) -> np.ndarray:
        n = distances.shape[0]
        W = np.zeros((n, n), dtype=float)
        kth = np.array([
            max(float(distances[i, nearest[i][-1]]) if len(nearest[i]) else 0.0, 1e-9)
            for i in range(n)
        ])
        for i in range(n):
            for j in nearest[i]:
                denom = max(1e-9, kth[i] * kth[j])
                W[i, j] = math.exp(-((float(distances[i, j]) ** 2) / denom))
        return np.maximum(W, W.T)

    def _cluster_labels(self, W: np.ndarray, n: int) -> np.ndarray:
        degree = W.sum(axis=1)
        safe_degree = np.where(degree > 1e-12, degree, 1.0)
        inv_sqrt = np.diag(1.0 / np.sqrt(safe_degree))
        L = np.eye(n) - inv_sqrt @ W @ inv_sqrt
        values, vectors = np.linalg.eigh(L)
        order = np.argsort(values)
        values = values[order]
        vectors = vectors[:, order]

        max_clusters = min(6, max(1, n // 8))
        if max_clusters < 2:
            return np.zeros(n, dtype=int)

        small_count = int(np.sum(values[:max_clusters] < 0.08))
        gaps = np.diff(values[: max_clusters + 1]) if len(values) > max_clusters else np.diff(values)
        gap_choice = int(np.argmax(gaps) + 1) if len(gaps) else 1
        k = max(1, min(max_clusters, max(small_count, gap_choice)))
        if k < 2 or (len(gaps) and float(np.max(gaps)) < 0.04 and small_count < 2):
            return np.zeros(n, dtype=int)

        labels = self._kmeans(vectors[:, :k], k)
        counts = Counter(int(label) for label in labels)
        if any(count < 3 for count in counts.values()):
            return np.zeros(n, dtype=int)
        return labels

    def _kmeans(self, data: np.ndarray, k: int, iterations: int = 50) -> np.ndarray:
        n = data.shape[0]
        if k <= 1 or n <= k:
            return np.zeros(n, dtype=int)
        norms = np.linalg.norm(data, axis=1)
        first = int(np.argmax(norms))
        centroids = [data[first]]
        while len(centroids) < k:
            existing = np.vstack(centroids)
            dists = np.min(((data[:, None, :] - existing[None, :, :]) ** 2).sum(axis=2), axis=1)
            centroids.append(data[int(np.argmax(dists))])
        centers = np.vstack(centroids)
        labels = np.zeros(n, dtype=int)
        for _ in range(iterations):
            distances = ((data[:, None, :] - centers[None, :, :]) ** 2).sum(axis=2)
            new_labels = np.argmin(distances, axis=1)
            if np.array_equal(new_labels, labels):
                break
            labels = new_labels
            for idx in range(k):
                members = data[labels == idx]
                if len(members):
                    centers[idx] = members.mean(axis=0)
        return labels

    def _outlier_mask(
        self,
        records: list[CoherenceRecord],
        distances: np.ndarray,
        nearest: np.ndarray,
        clusters: np.ndarray,
        W: np.ndarray,
        cluster_medoids: dict[int, int] | None = None,
        density_ratios: dict[int, float] | None = None,
    ) -> np.ndarray:
        n = len(records)
        cluster_medoids = cluster_medoids or self._cluster_medoid_indexes(clusters, distances)
        density_ratios = density_ratios or self._cluster_density_ratios(clusters, distances, cluster_medoids)
        local_degree = W.sum(axis=1)
        mean_distance = np.array([
            np.mean(distances[i, nearest[i]]) if len(nearest[i]) else 0.0
            for i in range(n)
        ])
        medoid_distance = np.array([
            distances[i, cluster_medoids[int(clusters[i])]]
            for i in range(n)
        ])
        low_degree = self._low_outliers(local_degree, clusters, density_ratios)
        high_mean = self._high_outliers(mean_distance, clusters, density_ratios)
        high_medoid = self._high_outliers(medoid_distance, clusters, density_ratios)
        return low_degree | high_mean | high_medoid

    def _cluster_density_ratios(
        self,
        clusters: np.ndarray,
        distances: np.ndarray,
        cluster_medoids: dict[int, int],
    ) -> dict[int, float]:
        densities: dict[int, float] = {}
        for label in set(int(item) for item in clusters):
            member_indexes = np.where(clusters == label)[0]
            support = max(0, len(member_indexes) - 1)
            if support <= 0:
                densities[label] = 0.0
                continue
            medoid_idx = cluster_medoids[label]
            radius = float(np.percentile(distances[member_indexes, medoid_idx], 90))
            densities[label] = support / max(radius, DENSITY_EPSILON)
        positive = [value for value in densities.values() if value > 0 and math.isfinite(value)]
        baseline = float(np.median(positive)) if positive else 0.0
        if baseline <= 0:
            return {label: 1.0 for label in densities}
        return {
            label: _clamp_density_ratio(value / baseline) if value > 0 and math.isfinite(value) else 1.0
            for label, value in densities.items()
        }

    def _cluster_medoid_indexes(self, clusters: np.ndarray, distances: np.ndarray) -> dict[int, int]:
        medoids: dict[int, int] = {}
        for label in set(int(item) for item in clusters):
            member_indexes = np.where(clusters == label)[0]
            if len(member_indexes) <= 1:
                medoids[label] = int(member_indexes[0])
                continue
            sub = distances[np.ix_(member_indexes, member_indexes)]
            medoid_local = int(np.argmin(sub.sum(axis=1)))
            medoids[label] = int(member_indexes[medoid_local])
        return medoids

    def _cluster_profiles(
        self,
        group_key: tuple[str, str, str],
        records: list[CoherenceRecord],
        distances: np.ndarray,
        clusters: np.ndarray,
        cluster_medoids: dict[int, int],
    ) -> list[dict[str, Any]]:
        audio_type, category, subcategory = group_key
        profiles: list[dict[str, object]] = []
        for label in set(int(item) for item in clusters):
            member_indexes = np.where(clusters == label)[0]
            medoid_idx = cluster_medoids[label]
            if len(member_indexes) <= 1:
                radius = 0.0
            else:
                radius = float(np.percentile(distances[member_indexes, medoid_idx], 90))
            profiles.append(
                {
                    "cluster_id": f"{_slug(audio_type)}_{_slug(category)}_{_slug(subcategory)}_{label:03d}",
                    "audio_type": audio_type,
                    "category": category,
                    "subcategory": subcategory,
                    "medoid_vector": records[medoid_idx].vector,
                    "radius": radius,
                    "member_count": len(member_indexes),
                }
            )
        return profiles

    def _with_cluster_adjacency_summaries(
        self,
        results: list[CoherenceResult],
        cluster_profiles: list[dict[str, Any]],
    ) -> list[CoherenceResult]:
        if len(cluster_profiles) < 2:
            return results
        profile_by_id = {str(profile.get("cluster_id") or ""): profile for profile in cluster_profiles}
        adjacency_by_cluster = {
            str(profile.get("cluster_id") or ""): self._nearest_adjacent_cluster(profile, cluster_profiles)
            for profile in cluster_profiles
        }
        updated: list[CoherenceResult] = []
        for result in results:
            adjacency = adjacency_by_cluster.get(result.cluster_id or "")
            if not adjacency:
                updated.append(result)
                continue
            summary = dict(result.nearest_neighbor_summary or {})
            summary["nearest_adjacent_cluster"] = adjacency
            updated.append(
                CoherenceResult(
                    record_id=result.record_id,
                    category=result.category,
                    subcategory=result.subcategory,
                    coherence_status=result.coherence_status,
                    coherence_score=result.coherence_score,
                    cluster_id=result.cluster_id,
                    is_outlier=result.is_outlier,
                    review_reason=result.review_reason,
                    suggested_alternate_category=result.suggested_alternate_category,
                    suggested_alternate_subcategory=result.suggested_alternate_subcategory,
                    nearest_neighbor_summary=summary,
                    anchor_fit_status=result.anchor_fit_status,
                )
            )
        return updated

    def _nearest_adjacent_cluster(
        self,
        profile: dict[str, object],
        cluster_profiles: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        best: dict[str, object] | None = None
        best_distance = float("inf")
        source_id = str(profile.get("cluster_id") or "")
        for candidate in cluster_profiles:
            if str(candidate.get("cluster_id") or "") == source_id:
                continue
            distance = self._profile_distance(profile, candidate)
            if math.isfinite(distance) and distance < best_distance:
                best_distance = distance
                best = candidate
        if best is None:
            return None
        ratio = self._cluster_adjacency_ratio(profile, best, best_distance)
        return {
            "audio_type": str(best.get("audio_type") or ""),
            "category": str(best.get("category") or ""),
            "subcategory": str(best.get("subcategory") or ""),
            "distance": round(best_distance, 6),
            "adjacency_ratio": round(ratio, 6),
            "is_close": ratio <= CLUSTER_ADJACENCY_RATIO_CLOSE,
        }

    def _profile_distance(self, left: dict[str, Any], right: dict[str, Any]) -> float:
        left_vector = _profile_vector(left.get("medoid_vector"))
        right_vector = _profile_vector(right.get("medoid_vector"))
        if not left_vector or not right_vector:
            return float("inf")
        distance = self.similarity_engine.calculate_distance(left_vector, right_vector)
        return distance if math.isfinite(distance) else float("inf")

    def _cluster_adjacency_ratio(
        self,
        left: dict[str, Any],
        right: dict[str, Any],
        distance: float | None = None,
    ) -> float:
        if distance is None:
            distance = self._profile_distance(left, right)
        radius_sum = max(
            _profile_float(left.get("radius")) + _profile_float(right.get("radius")),
            DENSITY_EPSILON,
        )
        return distance / radius_sum

    def _distance_to_cluster_medoid(self, idx: int, member_indexes: np.ndarray, distances: np.ndarray) -> float:
        if len(member_indexes) <= 1:
            return 0.0
        sub = distances[np.ix_(member_indexes, member_indexes)]
        medoid_local = int(np.argmin(sub.sum(axis=1)))
        medoid_idx = int(member_indexes[medoid_local])
        return float(distances[idx, medoid_idx])

    def _low_outliers(
        self,
        values: np.ndarray,
        clusters: np.ndarray | None = None,
        density_ratios: dict[int, float] | None = None,
    ) -> np.ndarray:
        if float(np.max(values) - np.min(values)) < 0.25:
            return np.zeros(values.shape, dtype=bool)
        q1, q3 = np.percentile(values, [25, 75])
        iqr = max(float(q3 - q1), 1e-9)
        if clusters is None or not density_ratios:
            return values < (q1 - OUTLIER_IQR_BASE_MULTIPLIER * iqr)
        thresholds = np.array([
            q1 - _outlier_iqr_multiplier(density_ratios.get(int(label), 1.0)) * iqr
            for label in clusters
        ])
        return values < thresholds

    def _high_outliers(
        self,
        values: np.ndarray,
        clusters: np.ndarray | None = None,
        density_ratios: dict[int, float] | None = None,
    ) -> np.ndarray:
        if float(np.max(values) - np.min(values)) < 0.05:
            return np.zeros(values.shape, dtype=bool)
        q1, q3 = np.percentile(values, [25, 75])
        iqr = max(float(q3 - q1), 1e-9)
        if clusters is None or not density_ratios:
            return values > (q3 + OUTLIER_IQR_BASE_MULTIPLIER * iqr)
        thresholds = np.array([
            q3 + _outlier_iqr_multiplier(density_ratios.get(int(label), 1.0)) * iqr
            for label in clusters
        ])
        return values > thresholds

    def _refinement_candidates(
        self,
        records: list[CoherenceRecord],
        results: list[CoherenceResult],
        group_context: _GroupContext,
        cluster_profiles: list[dict[str, Any]] | None = None,
    ) -> list[RefinementCandidate]:
        return refinement_candidates_for_engine(
            self,
            records,
            results,
            group_context,
            cluster_profiles,
            normal_improvement_threshold=NORMAL_IMPROVEMENT_THRESHOLD,
            strong_improvement_threshold=STRONG_IMPROVEMENT_THRESHOLD,
            cluster_adjacency_travel_penalty=CLUSTER_ADJACENCY_TRAVEL_PENALTY,
        )

    def _target_cluster_adjacency(
        self,
        record: CoherenceRecord,
        current_profile: dict[str, object] | None,
        cluster_profiles: list[dict[str, Any]],
        target_group: tuple[str, str, str],
    ) -> dict[str, Any] | None:
        if current_profile is None:
            return None
        target_profiles = [
            profile for profile in cluster_profiles
            if (
                str(profile.get("audio_type") or ""),
                str(profile.get("category") or ""),
                str(profile.get("subcategory") or ""),
            ) == target_group
        ]
        if not target_profiles:
            return None
        target_profile = min(
            target_profiles,
            key=lambda profile: self.similarity_engine.calculate_distance(
                record.vector,
                _profile_vector(profile.get("medoid_vector")) or [],
            ),
        )
        distance = self._profile_distance(current_profile, target_profile)
        if not math.isfinite(distance):
            return None
        ratio = self._cluster_adjacency_ratio(current_profile, target_profile, distance)
        return {
            "audio_type": str(target_profile.get("audio_type") or ""),
            "category": str(target_profile.get("category") or ""),
            "subcategory": str(target_profile.get("subcategory") or ""),
            "distance": round(distance, 6),
            "adjacency_ratio": round(ratio, 6),
            "is_close": ratio <= CLUSTER_ADJACENCY_RATIO_CLOSE,
        }

    def _second_best_improvement(
        self,
        ranked_groups: list[tuple[tuple[str, ...], int, float]],
        assigned_distance: float,
        winner: tuple[str, ...],
    ) -> float:
        for group_key, _count, mean_distance in ranked_groups:
            if group_key == winner or not math.isfinite(mean_distance):
                continue
            return max(0.0, min(1.0, (assigned_distance - mean_distance) / max(assigned_distance, 1e-9)))
        return 0.0

    def _refinement_state(
        self,
        *,
        record: CoherenceRecord,
        winner_score: float,
        second_score: float,
        improvement_ratio: float,
        force_pending: bool = False,
    ) -> str:
        if record.category == "Uncategorized":
            return REFINEMENT_AUTO_STAGED
        if force_pending:
            return REFINEMENT_PENDING
        token_confidence = record.classification_confidence
        if token_confidence is None:
            return REFINEMENT_PENDING
        strong_confidence = winner_score >= token_confidence * STRONG_TOKEN_CONFIDENCE_FLOOR_RATIO
        strong_margin = winner_score >= max(second_score * STRONG_WINNER_MARGIN_RATIO, 1e-9)
        strong_improvement = improvement_ratio >= STRONG_IMPROVEMENT_THRESHOLD
        return REFINEMENT_AUTO_STAGED if strong_confidence and strong_margin and strong_improvement else REFINEMENT_PENDING

    def _anchor_fit(self, record: CoherenceRecord, audio_type: str, category: str, subcategory: str) -> dict[str, object]:
        anchors = self._matching_anchors(audio_type, category, subcategory)
        if not anchors:
            return {"close": False, "status": "no_anchor", "distance": float("inf"), "radius": 0.0}
        best_distance = float("inf")
        best_radius = 0.0
        for anchor in anchors:
            vector = anchor.get("medoid_vector")
            if not vector:
                continue
            distance = self.similarity_engine.calculate_distance(record.vector, vector)
            if math.isfinite(distance) and distance < best_distance:
                best_distance = distance
                best_radius = _safe_anchor_radius(anchor.get("coherence_radius"))
        close = best_radius > 0 and best_distance <= best_radius * ANCHOR_CLOSE_RADIUS_MULTIPLIER
        return {
            "close": close,
            "status": "close" if close else "distant",
            "distance": best_distance,
            "radius": best_radius,
        }

    def _global_neighbors(
        self,
        record: CoherenceRecord,
        records: list[CoherenceRecord],
        limit: int,
    ) -> list[tuple[CoherenceRecord, float]]:
        vectorized = self._global_neighbors_vectorized(record, records, limit)
        if vectorized is not None:
            return vectorized
        distances = []
        for candidate in records:
            if candidate.record_id == record.record_id:
                continue
            distance = self.similarity_engine.calculate_distance(record.vector, candidate.vector)
            if math.isfinite(distance):
                distances.append((candidate, distance))
        distances.sort(key=lambda item: item[1])
        return distances[:limit]
    def _get_global_spatial_index(self, records: list[CoherenceRecord]) -> Any | None:
        key = id(records)
        if key in self._global_spatial_index_cache:
            return self._global_spatial_index_cache[key]
            
        inputs = self._vectorized_inputs(records)
        if inputs is None:
            return None
        vectors = inputs["vectors"]
        
        try:
            from .spatial_index import SpatialIndex
            spatial_index = SpatialIndex(vectors)
        except ModuleNotFoundError:
            return None
        self._global_spatial_index_cache[key] = spatial_index
        return spatial_index

    def _global_neighbors_vectorized(
        self,
        record: CoherenceRecord,
        records: list[CoherenceRecord],
        limit: int,
    ) -> list[tuple[CoherenceRecord, float]] | None:
        weights = getattr(self.similarity_engine, "weights", None)
        if weights is None:
            return None
            
        n = len(records)
        if n >= 3000:
            spatial_index = self._get_global_spatial_index(records)
            if spatial_index is not None:
                inputs = self._vectorized_inputs(records)
                if inputs is None:
                    return None
                vectors = inputs["vectors"]
                
                try:
                    query_idx = next(i for i, r in enumerate(records) if r.record_id == record.record_id)
                except StopIteration:
                    return None
                    
                M = min(max(100, limit * 2), n)
                labels, _ = spatial_index.query(vectors[query_idx], k=M)
                candidate_indices = labels[0]
                
                cand_records = [records[c] for c in candidate_indices]
                exact_dists = self._distances_from_vectorized(record.vector, cand_records)
                if exact_dists is None:
                    return None
                    
                paired = []
                for c_idx, dist in zip(candidate_indices, exact_dists):
                    cand = records[c_idx]
                    if cand.record_id == record.record_id:
                        continue
                    if math.isfinite(dist):
                        paired.append((cand, float(dist)))
                paired.sort(key=lambda x: x[1])
                return paired[:limit]
            
        distances = self._distances_from_vectorized(record.vector, records)
        if distances is None:
            return None
        ordered = np.argsort(distances)
        result: list[tuple[CoherenceRecord, float]] = []
        for idx in ordered:
            candidate = records[int(idx)]
            if candidate.record_id == record.record_id:
                continue
            distance = float(distances[int(idx)])
            if math.isfinite(distance):
                result.append((candidate, distance))
            if len(result) >= limit:
                break
        return result


def _clamp_density_ratio(value: float) -> float:
    return clamp_density_ratio(value, min_ratio=DENSITY_RATIO_MIN, max_ratio=DENSITY_RATIO_MAX)


def _outlier_iqr_multiplier(density_ratio: float) -> float:
    return outlier_iqr_multiplier(
        density_ratio,
        base_multiplier=OUTLIER_IQR_BASE_MULTIPLIER,
        min_multiplier=OUTLIER_IQR_MIN_MULTIPLIER,
        max_multiplier=OUTLIER_IQR_MAX_MULTIPLIER,
        min_density_ratio=DENSITY_RATIO_MIN,
        max_density_ratio=DENSITY_RATIO_MAX,
    )
