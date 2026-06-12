from __future__ import annotations

import logging
from collections import defaultdict
from pathlib import Path
from statistics import median

from unshuffle.audio import SimilarityEngine
from unshuffle.core.features import vector_from_blob


ANCHOR_TOO_BROAD_RATIO = 1.75


def add_anchor_consistency(rows: list[dict]) -> list[dict]:
    radii_by_group: dict[tuple[str, str], list[float]] = defaultdict(list)
    all_radii: list[float] = []
    for row in rows:
        try:
            radius = float(row.get("coherence_radius") or 0.0)
        except (TypeError, ValueError):
            continue
        if radius <= 0:
            continue
        audio_type = str(row.get("audio_type") or "").strip()
        category = str(row.get("category") or "").strip()
        if category:
            radii_by_group[(audio_type, category)].append(radius)
        all_radii.append(radius)

    global_baseline = median(all_radii) if len(all_radii) >= 2 else 0.0
    enriched = []
    densities_by_group: dict[tuple[str, str], list[float]] = defaultdict(list)
    all_densities: list[float] = []
    for payload in rows:
        row = dict(payload)
        try:
            radius = float(row.get("coherence_radius") or 0.0)
        except (TypeError, ValueError):
            radius = 0.0
        audio_type = str(row.get("audio_type") or "").strip()
        category = str(row.get("category") or "").strip()
        group_radii = radii_by_group.get((audio_type, category), [])
        if len(group_radii) >= 2:
            baseline = median(group_radii)
            scope = "type/category" if audio_type else "category"
        else:
            baseline = global_baseline
            scope = "library"
        if radius > 0 and baseline > 0:
            ratio = radius / baseline
            if ratio <= 0.75:
                label = "Strong"
            elif ratio <= 1.25:
                label = "Medium"
            elif ratio >= ANCHOR_TOO_BROAD_RATIO:
                label = "Too broad"
            else:
                label = "Low"
            row["consistency_ratio"] = ratio
            row["consistency_text"] = label
            row["consistency_baseline_scope"] = scope
            neighbors = anchor_neighbors(row)
            density = neighbors / max(ratio, 0.01)
            row["density_score"] = density
            if density > 0:
                densities_by_group[(audio_type, category)].append(density)
                all_densities.append(density)
        else:
            row["consistency_ratio"] = None
            row["consistency_text"] = "Pending"
            row["consistency_baseline_scope"] = ""
            row["density_score"] = None
        enriched.append(row)

    global_density_baseline = median(all_densities) if len(all_densities) >= 2 else 0.0
    final_rows = []
    for payload in enriched:
        row = dict(payload)
        density = row.get("density_score")
        audio_type = str(row.get("audio_type") or "").strip()
        category = str(row.get("category") or "").strip()
        group_densities = densities_by_group.get((audio_type, category), [])
        density_baseline = median(group_densities) if len(group_densities) >= 2 else global_density_baseline
        if isinstance(density, (int, float)) and density > 0 and density_baseline > 0:
            density_ratio = float(density) / density_baseline
            if density_ratio >= 1.25:
                density_label = "Dense"
            elif density_ratio >= 0.75:
                density_label = "Typical"
            else:
                density_label = "Sparse"
            if row.get("consistency_text") == "Too broad":
                row["anchor_quality"] = "too_broad"
                row["anchor_quality_text"] = "Too broad"
            else:
                row["anchor_quality"] = "candidate"
                row["anchor_quality_text"] = ""
            row["density_ratio"] = density_ratio
            row["density_text"] = f"{density_label} {density_ratio:.2f}x"
        else:
            row["density_ratio"] = None
            row["density_text"] = "Pending"
            row.setdefault("anchor_quality", "candidate")
            row.setdefault("anchor_quality_text", "")
        final_rows.append(row)
    return final_rows


def anchor_neighbors(row: dict) -> int:
    try:
        return max(0, int(row.get("n_reference_items") or 0) - 1)
    except (TypeError, ValueError):
        return 0


def enrich_anchor_candidate_rows(engine, rows: list[dict]) -> list[dict]:
    if not rows or not getattr(engine, "db", None):
        return rows
    try:
        staging_rows = engine.db.get_staging_records(engine.session_id)
        coherence_rows = engine.db.list_coherence_results(engine.session_id)
    except Exception:
        logging.exception("Failed to enrich anchor candidate rows.")
        return rows

    staging_by_record_id = {
        str(row.get("row_id") if row.get("row_id") is not None else row.get("id")): row
        for row in staging_rows
    }
    records_by_cluster: dict[str, list[dict]] = defaultdict(list)
    for result in coherence_rows:
        cluster_id = str(result.get("cluster_id") or "")
        if not cluster_id:
            continue
        record = staging_by_record_id.get(str(result.get("record_id") or ""))
        if record is not None:
            records_by_cluster[cluster_id].append(record)

    distance_engine = SimilarityEngine()
    enriched = []
    for payload in rows:
        row = dict(payload)
        cluster_records = records_by_cluster.get(str(row.get("cluster_id") or ""), [])
        medoid_vector = vector_from_blob(row.get("medoid_vector"))
        if medoid_vector:
            medoid_values = list(medoid_vector)
            def record_distance(record: dict) -> tuple[float, str]:
                vector = vector_from_blob(record.get("feature_vector", record.get("acoustic_vector")))
                if not vector:
                    return (float("inf"), str(record.get("sample_name") or record.get("source_path") or "").lower())
                distance = distance_engine.calculate_distance(medoid_values, vector)
                if not isinstance(distance, (int, float)) or distance != distance:
                    distance = float("inf")
                return (float(distance), str(record.get("sample_name") or record.get("source_path") or "").lower())
            cluster_records = sorted(cluster_records, key=record_distance)
        else:
            cluster_records = sorted(
                cluster_records,
                key=lambda record: str(record.get("sample_name") or record.get("source_path") or "").lower(),
            )
        examples = []
        for record in cluster_records[:8]:
            source_path = str(record.get("source_path") or "")
            name = str(record.get("sample_name") or Path(source_path).name or source_path)
            if not name:
                continue
            examples.append(
                {
                    "name": name,
                    "path": source_path,
                    "pack": str(record.get("pack") or ""),
                }
            )
        row["reference_paths"] = [str(record.get("source_path") or "") for record in cluster_records if record.get("source_path")]
        if examples:
            row["examples"] = examples
            row["example_name"] = examples[0]["name"]
            row["preview_path"] = examples[0]["path"]
        enriched.append(row)
    return enriched
