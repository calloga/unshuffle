from __future__ import annotations

import csv
from pathlib import Path

from unshuffle.logic.coherence.models import ANCHOR_VERIFIED, AnchorProfile


def read_additions_csv(path: Path) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    with open(path, "r", newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            alias = str(row.get("alias", "") or "").strip().lower()
            category = str(row.get("category", "") or "").strip()
            if alias and category:
                rows.append((alias, category))
    return rows


def write_additions_csv(path: Path, rows) -> int:
    count = 0
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["alias", "category", "source"])
        writer.writeheader()
        for alias, category, source in rows:
            writer.writerow({"alias": alias, "category": category, "source": source})
            count += 1
    return count


def anchor_profile_from_payload(payload: dict, state: str = ANCHOR_VERIFIED) -> AnchorProfile | None:
    features = payload.get("features") or {}
    evidence = payload.get("evidence") or {}
    medoid = features.get("medoid_vector")
    centroid = features.get("cluster_centroid")
    cluster_std = features.get("cluster_std")
    if not isinstance(medoid, list) or not isinstance(centroid, list):
        return None
    if not isinstance(cluster_std, list) or len(cluster_std) != len(medoid):
        cluster_std = [0.0] * len(medoid)
    try:
        radius = features.get("coherence_radius")
        reference_count = evidence.get("n_reference_items")
        if radius is None or reference_count is None:
            return None
        return AnchorProfile(
            anchor_id=str(payload.get("anchor_id") or ""),
            audio_type=str(payload.get("audio_type") or ""),
            category=str(payload.get("category") or ""),
            subcategory=str(payload.get("subcategory") or ""),
            cluster_id=str(payload.get("cluster_id") or payload.get("anchor_id") or ""),
            feature_space_version=str(features.get("feature_space_version") or ""),
            extractor_version=str(features.get("extractor_version") or ""),
            vector_schema=tuple(str(item) for item in (features.get("vector_schema") or [])),
            medoid_vector=[float(item) for item in medoid],
            cluster_centroid=[float(item) for item in centroid],
            cluster_std=[float(item) for item in cluster_std],
            coherence_radius=float(radius),
            n_reference_items=int(reference_count),
            state=state,
            profile_payload=payload,
        )
    except (TypeError, ValueError):
        return None
