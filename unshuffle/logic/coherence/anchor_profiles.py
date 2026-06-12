from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from typing import Iterable, Protocol

import numpy as np

from ...audio import SimilarityEngine
from ...core.features import (
    CURRENT_EXTRACTOR_VERSION,
    CURRENT_FEATURE_SPACE_VERSION,
    CURRENT_FEATURE_VECTOR_SIZE,
    CURRENT_VECTOR_SCHEMA,
)
from .models import (
    ANCHOR_CANDIDATE,
    COHERENCE_STATUS_CLUSTERED,
    COHERENCE_STATUS_STABLE,
    AnchorProfile,
    CoherenceRecord,
    CoherenceResult,
)


class _SimilarityDistanceEngine(Protocol):
    def calculate_distance(self, v1: list[float], v2: list[float], d1: float = 0.0, d2: float = 0.0) -> float:
        ...


FORBIDDEN_EXPORT_FIELDS = {
    "audio",
    "filename",
    "filenames",
    "path",
    "paths",
    "pack",
    "vendor",
    "artist",
    "source_file",
    "source_path",
    "folder",
    "folder_structure",
}


def generate_anchor_candidates(
    records: Iterable[CoherenceRecord],
    results: Iterable[CoherenceResult],
    similarity_engine: _SimilarityDistanceEngine | None = None,
) -> list[AnchorProfile]:
    engine = similarity_engine or SimilarityEngine()
    records_by_id = {record.record_id: record for record in records}
    members: dict[str, list[CoherenceRecord]] = defaultdict(list)
    result_by_cluster: dict[str, CoherenceResult] = {}

    for result in results:
        if result.coherence_status not in {COHERENCE_STATUS_STABLE, COHERENCE_STATUS_CLUSTERED}:
            continue
        if result.is_outlier or not result.cluster_id:
            continue
        record = records_by_id.get(result.record_id)
        if record is None or len(record.vector) != CURRENT_FEATURE_VECTOR_SIZE:
            continue
        members[result.cluster_id].append(record)
        result_by_cluster[result.cluster_id] = result

    profiles: list[AnchorProfile] = []
    for cluster_id, cluster_records in members.items():
        if len(cluster_records) < 5:
            continue
        vectors = np.array([record.vector for record in cluster_records], dtype=float)
        if not np.isfinite(vectors).all():
            continue
        centroid = vectors.mean(axis=0)
        std = vectors.std(axis=0)
        medoid_record = min(
            cluster_records,
            key=lambda record: engine.calculate_distance(record.vector, centroid.tolist()),
        )
        distances = [
            engine.calculate_distance(record.vector, medoid_record.vector)
            for record in cluster_records
        ]
        finite_distances = [distance for distance in distances if math.isfinite(distance)]
        if not finite_distances:
            continue
        radius = float(np.percentile(finite_distances, 90))
        result = result_by_cluster[cluster_id]
        audio_type = (medoid_record.audio_type or "").strip()
        payload = build_anchor_payload(
            cluster_id=cluster_id,
            audio_type=audio_type,
            category=result.category,
            subcategory=result.subcategory,
            medoid_vector=medoid_record.vector,
            cluster_centroid=centroid.tolist(),
            cluster_std=std.tolist(),
            coherence_radius=max(radius, 1e-9),
            n_reference_items=len(cluster_records),
        )
        profiles.append(
            AnchorProfile(
                payload["anchor_id"],
                result.category,
                result.subcategory,
                cluster_id,
                CURRENT_FEATURE_SPACE_VERSION,
                CURRENT_EXTRACTOR_VERSION,
                CURRENT_VECTOR_SCHEMA,
                list(medoid_record.vector),
                centroid.tolist(),
                std.tolist(),
                max(radius, 1e-9),
                len(cluster_records),
                ANCHOR_CANDIDATE,
                payload,
                audio_type,
            )
        )
    return profiles


def build_anchor_payload(
    *,
    cluster_id: str,
    audio_type: str = "",
    category: str,
    subcategory: str,
    medoid_vector: list[float],
    cluster_centroid: list[float],
    cluster_std: list[float],
    coherence_radius: float,
    n_reference_items: int,
) -> dict:
    anchor_id = stable_anchor_id(
        audio_type=audio_type,
        category=category,
        subcategory=subcategory,
        medoid_vector=medoid_vector,
        cluster_centroid=cluster_centroid,
    )
    return {
        "profile_type": "cluster_anchor",
        "anchor_id": anchor_id,
        "cluster_id": cluster_id,
        "audio_type": audio_type,
        "category": category,
        "subcategory": subcategory,
        "features": {
            "feature_space_version": CURRENT_FEATURE_SPACE_VERSION,
            "extractor_version": CURRENT_EXTRACTOR_VERSION,
            "normalization_version": "unshuffle-norm-v1",
            "vector_schema": list(CURRENT_VECTOR_SCHEMA),
            "medoid_vector": _rounded(medoid_vector),
            "cluster_centroid": _rounded(cluster_centroid),
            "cluster_std": _rounded(cluster_std),
            "coherence_radius": coherence_radius,
        },
        "evidence": {
            "n_reference_items": n_reference_items,
            "source": "local_curated",
            "created_from": "laplacian_cluster_pass",
        },
        "privacy": {
            "contains_audio": False,
            "contains_filenames": False,
            "contains_paths": False,
            "contains_vendor_names": False,
            "contains_artist_names": False,
            "contains_folder_structure": False,
        },
    }


def stable_anchor_id(
    *,
    audio_type: str = "",
    category: str,
    subcategory: str,
    medoid_vector: list[float],
    cluster_centroid: list[float],
) -> str:
    raw = (
        CURRENT_FEATURE_SPACE_VERSION
        + audio_type
        + category
        + subcategory
        + json.dumps(_rounded(medoid_vector), separators=(",", ":"))
        + json.dumps(_rounded(cluster_centroid), separators=(",", ":"))
    )
    return "anchor_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def validate_anchor_payload(payload: dict, known_categories: set[str] | None = None) -> tuple[bool, str]:
    if not isinstance(payload, dict):
        return False, "payload is not an object"
    lowered = {str(key).lower() for key in _walk_keys(payload)}
    suspicious = sorted(key for key in lowered if key in FORBIDDEN_EXPORT_FIELDS)
    if suspicious:
        return False, f"contains private metadata field(s): {', '.join(suspicious[:5])}"

    if not str(payload.get("anchor_id") or "").strip():
        return False, "missing anchor_id"
    category = str(payload.get("category") or "")
    if known_categories is not None and category not in known_categories:
        return False, "unknown category"
    features = payload.get("features") or {}
    if not isinstance(features, dict):
        return False, "features is not an object"
    if features.get("feature_space_version") != CURRENT_FEATURE_SPACE_VERSION:
        return False, "feature space mismatch"
    if features.get("extractor_version") != CURRENT_EXTRACTOR_VERSION:
        return False, "extractor version mismatch"
    vector_schema = features.get("vector_schema")
    if list(vector_schema or []) != list(CURRENT_VECTOR_SCHEMA):
        return False, "vector schema mismatch"
    vector = features.get("medoid_vector")
    centroid = features.get("cluster_centroid")
    cluster_std = features.get("cluster_std")
    if not _valid_vector(vector) or not _valid_vector(centroid) or not _valid_vector(cluster_std):
        return False, "invalid vector length or values"
    radius = features.get("coherence_radius")
    try:
        if radius is None:
            raise TypeError
        if float(radius) <= 0:
            return False, "coherence radius must be positive"
    except (TypeError, ValueError):
        return False, "invalid coherence radius"
    evidence = payload.get("evidence") or {}
    if not isinstance(evidence, dict):
        return False, "evidence is not an object"
    n_reference = evidence.get("n_reference_items")
    try:
        if n_reference is None:
            raise TypeError
        if int(n_reference) < 5:
            return False, "not enough reference items"
    except (TypeError, ValueError):
        return False, "invalid reference count"
    privacy = payload.get("privacy") or {}
    required_privacy = [
        "contains_audio",
        "contains_filenames",
        "contains_paths",
        "contains_vendor_names",
        "contains_artist_names",
        "contains_folder_structure",
    ]
    for key in required_privacy:
        if key not in privacy:
            return False, f"missing privacy flag: {key}"
        if bool(privacy[key]):
            return False, f"privacy flag is unsafe: {key}"
    return True, ""


def _rounded(values: list[float]) -> list[float]:
    return [round(value, 6) for value in values]


def _valid_vector(value) -> bool:
    if not isinstance(value, list) or len(value) != CURRENT_FEATURE_VECTOR_SIZE:
        return False
    try:
        return all(math.isfinite(float(item)) for item in value)
    except (TypeError, ValueError):
        return False


def _walk_keys(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from _walk_keys(child)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_keys(item)
