import json
import logging
import math
from pathlib import Path
from typing import Any, Dict, List

from ..core.assets import asset_path
from ..core.features import (
    CURRENT_EXTRACTOR_VERSION,
    CURRENT_FEATURE_SPACE_VERSION,
    CURRENT_FEATURE_VECTOR_SIZE,
    CURRENT_VECTOR_SCHEMA,
    feature_blob_from_vector,
)

logger = logging.getLogger(__name__)


def get_system_anchors_file_path() -> Path:
    """Returns the absolute path to the bundled system anchors JSON file."""
    return asset_path("data", "anchors", "system_anchors.json")


def load_system_anchors() -> List[Dict[str, Any]]:
    """Loads system anchor profiles from the bundled JSON and prepares them for seeding."""
    file_path = get_system_anchors_file_path()
    if not file_path.exists():
        logger.warning("System anchors file not found at: %s", file_path)
        return []

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            raw_anchors = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.error("Failed to load system anchors from %s: %s", file_path, e)
        return []

    if not isinstance(raw_anchors, list):
        logger.warning("System anchors JSON is not a list")
        return []

    prepared = []
    for payload in raw_anchors:
        if not isinstance(payload, dict):
            continue
        valid, reason = _validate_system_anchor_payload(payload)
        if not valid:
            logger.warning("Skipping invalid system anchor payload: %s", reason)
            continue

        anchor_id = payload.get("anchor_id")
        if not anchor_id:
            continue

        features = payload.get("features") or {}
        medoid_vector = features.get("medoid_vector")
        cluster_centroid = features.get("cluster_centroid")
        cluster_std = features.get("cluster_std")

        medoid_blob = feature_blob_from_vector(medoid_vector) if medoid_vector else None
        centroid_blob = feature_blob_from_vector(cluster_centroid) if cluster_centroid else None
        std_blob = feature_blob_from_vector(cluster_std) if cluster_std else None
        if medoid_blob is None or centroid_blob is None or std_blob is None:
            logger.warning("Skipping system anchor with invalid vectors: %s", anchor_id)
            continue

        evidence = payload.get("evidence") or {}
        vector_schema = features.get("vector_schema")
        feature_schema_json = json.dumps(vector_schema) if vector_schema else None

        row = {
            "anchor_id": anchor_id,
            "audio_type": payload.get("audio_type"),
            "category": payload.get("category"),
            "subcategory": payload.get("subcategory"),
            "cluster_id": payload.get("cluster_id"),
            "feature_space_version": features.get("feature_space_version"),
            "extractor_version": features.get("extractor_version"),
            "feature_schema_json": feature_schema_json,
            "medoid_vector": medoid_blob,
            "cluster_centroid": centroid_blob,
            "cluster_std": std_blob,
            "coherence_radius": features.get("coherence_radius"),
            "n_reference_items": evidence.get("n_reference_items"),
            "state": "system",
            "profile_json": json.dumps(payload),
        }
        prepared.append(row)

    return prepared


def _validate_system_anchor_payload(payload: dict) -> tuple[bool, str]:
    if not str(payload.get("anchor_id") or "").strip():
        return False, "missing anchor_id"
    features = payload.get("features")
    if not isinstance(features, dict):
        return False, "features is not an object"
    if features.get("feature_space_version") != CURRENT_FEATURE_SPACE_VERSION:
        return False, "feature space mismatch"
    if features.get("extractor_version") != CURRENT_EXTRACTOR_VERSION:
        return False, "extractor version mismatch"
    if list(features.get("vector_schema") or []) != list(CURRENT_VECTOR_SCHEMA):
        return False, "vector schema mismatch"
    for key in ("medoid_vector", "cluster_centroid", "cluster_std"):
        if not _valid_vector(features.get(key)):
            return False, f"invalid {key}"
    return True, ""


def _valid_vector(value) -> bool:
    if not isinstance(value, list) or len(value) != CURRENT_FEATURE_VECTOR_SIZE:
        return False
    try:
        return all(math.isfinite(float(item)) for item in value)
    except (TypeError, ValueError):
        return False
