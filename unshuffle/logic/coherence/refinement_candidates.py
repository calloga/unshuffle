from __future__ import annotations

from typing import Any

import math
from collections import Counter

import numpy as np

from .formatting import (
    _bucket_label,
    _candidate_id,
    _profile_float,
    _refinement_subcategory_normalizer,
)
from .models import (
    COHERENCE_STATUS_LOW,
    CoherenceRecord,
    CoherenceResult,
    RefinementCandidate,
)


def refinement_candidates_for_engine(
    engine,
    records: list[CoherenceRecord],
    results: list[CoherenceResult],
    group_context: dict[str, dict[str, float | str]],
    cluster_profiles: list[dict[str, Any]] | None = None,
    *,
    normal_improvement_threshold: float,
    strong_improvement_threshold: float,
    cluster_adjacency_travel_penalty: float,
) -> list[RefinementCandidate]:
    cluster_profiles = list(cluster_profiles or [])
    profile_by_id = {str(profile.get("cluster_id") or ""): profile for profile in cluster_profiles}
    normalize_subcategory = _refinement_subcategory_normalizer()
    result_by_id = {result.record_id: result for result in results}
    low_records = [
        record
        for record in records
        if record.record_id in result_by_id
        and (
            result_by_id[record.record_id].coherence_status == COHERENCE_STATUS_LOW
            or record.category == "Uncategorized"
        )
    ]
    candidates: list[RefinementCandidate] = []
    for record in low_records:
        source_is_uncategorized = record.category == "Uncategorized"
        neighbors = engine._global_neighbors(record, records, 10)
        if not neighbors:
            continue
        current_anchor = engine._anchor_fit(record, record.audio_type, record.category, record.subcategory)
        if current_anchor["close"]:
            continue

        category_counts = Counter(item.category for item, _distance in neighbors)
        ranked_categories: list[tuple[str, int, float]] = []
        for category, count in category_counts.items():
            category_distances = [distance for item, distance in neighbors if item.category == category]
            mean_distance = float(np.mean(category_distances)) if category_distances else float("inf")
            ranked_categories.append((category, count, mean_distance))
        ranked_categories.sort(key=lambda item: (item[2], -item[1]))
        if source_is_uncategorized:
            ranked_categories = [item for item in ranked_categories if item[0] != "Uncategorized"]
        if not ranked_categories:
            continue
        selected = None
        for candidate_category, category_agree_count, _mean_category in ranked_categories:
            category_neighbors = [
                (candidate, distance)
                for candidate, distance in neighbors
                if candidate.category == candidate_category
                and (
                    candidate.audio_type,
                    candidate.category,
                    candidate.subcategory,
                )
                != (record.audio_type, record.category, record.subcategory)
            ]
            if not category_neighbors:
                continue
            counts = Counter(
                (
                    item.audio_type,
                    item.category,
                    normalize_subcategory(item.category, item.subcategory),
                )
                for item, _distance in category_neighbors
            )
            ranked_groups: list[tuple[tuple[str, str, str], int, float]] = []
            for group_key, count in counts.items():
                group_audio_type, group_category, group_subcategory = group_key
                group_distances = [
                    distance
                    for item, distance in category_neighbors
                    if (
                        item.audio_type,
                        item.category,
                        normalize_subcategory(item.category, item.subcategory),
                    )
                    == (group_audio_type, group_category, group_subcategory)
                ]
                mean_distance = float(np.mean(group_distances)) if group_distances else float("inf")
                ranked_groups.append((group_key, count, mean_distance))
            ranked_groups.sort(key=lambda item: (item[2], -item[1]))
            for group_key, agree_count, mean_alt in ranked_groups:
                alt_audio_type, alt_category, alt_subcategory = group_key
                alt_anchor = engine._anchor_fit(record, alt_audio_type, alt_category, alt_subcategory)
                has_close_alt_anchor = bool(alt_anchor["close"])
                if category_agree_count < 4 and not has_close_alt_anchor:
                    continue
                if len(category_neighbors) < 4 and not has_close_alt_anchor:
                    continue
                if agree_count < 3 and not has_close_alt_anchor:
                    continue
                selected = (
                    alt_audio_type,
                    alt_category,
                    alt_subcategory,
                    agree_count,
                    mean_alt,
                    category_agree_count,
                    has_close_alt_anchor,
                    ranked_groups,
                )
                break
            if selected is not None:
                break
        if selected is None:
            continue
        (
            alt_audio_type,
            alt_category,
            alt_subcategory,
            agree_count,
            mean_alt,
            category_agree_count,
            has_close_alt_anchor,
            ranked_groups,
        ) = selected
        context = group_context.get(record.record_id, {})
        assigned_medoid = _profile_float(context.get("assigned_medoid_distance"), default=float("inf"))
        adjacency = None
        if source_is_uncategorized:
            improvement_ratio = max(
                category_agree_count / max(len(neighbors), 1),
                agree_count / max(category_agree_count, 1),
            )
            adjusted_improvement = improvement_ratio
        else:
            if not math.isfinite(assigned_medoid) or assigned_medoid <= 0:
                continue
            improvement_ratio = (assigned_medoid - mean_alt) / max(assigned_medoid, 1e-9)
            adjacency = engine._target_cluster_adjacency(
                record,
                profile_by_id.get(str(context.get("cluster_id") or "")),
                cluster_profiles,
                (alt_audio_type, alt_category, alt_subcategory),
            )
            adjusted_improvement = improvement_ratio
            if adjacency and adjacency.get("is_close"):
                adjusted_improvement *= cluster_adjacency_travel_penalty
            if improvement_ratio < normal_improvement_threshold:
                continue
        confidence = record.classification_confidence
        confidence_caution = False
        if confidence is not None and confidence >= 0.85:
            overwhelming = (
                category_agree_count >= 6
                and agree_count >= 4
                and adjusted_improvement >= strong_improvement_threshold
            )
            if not overwhelming and not has_close_alt_anchor:
                confidence_caution = True
        winner_score = max(0.0, min(1.0, improvement_ratio))
        second_score = engine._second_best_improvement(
            ranked_groups,
            assigned_medoid,
            (alt_audio_type, alt_category, alt_subcategory),
        )
        state = engine._refinement_state(
            record=record,
            winner_score=winner_score,
            second_score=second_score,
            improvement_ratio=adjusted_improvement,
            force_pending=confidence_caution,
        )
        evidence = (
            f"- Low internal coherence; {category_agree_count}/10 nearest acoustic neighbors "
            f"first point to {alt_category}."
        )
        evidence += (
            f"\n- Within {alt_category}, {agree_count}/{category_agree_count} nearest neighbors "
            f"fit {_bucket_label(alt_audio_type, alt_category, alt_subcategory)} better."
        )
        if source_is_uncategorized:
            evidence += "\n- Uncategorized is a holding bucket, so this suggestion is based on the nearest known neighbors."
        else:
            evidence += f"\n- Alternate fit improves on the current assignment by {improvement_ratio:.0%}."
        if adjacency:
            adjacent_label = _bucket_label(
                str(adjacency.get("audio_type") or ""),
                str(adjacency.get("category") or ""),
                str(adjacency.get("subcategory") or ""),
            )
            relation = "close neighboring" if adjacency.get("is_close") else "neighboring"
            evidence += (
                f"\n- Target is a {relation} cluster ({adjacent_label}, "
                f"{_profile_float(adjacency.get('adjacency_ratio')):.2f}x separation)."
            )
            if adjacency.get("is_close"):
                evidence += "\n- Because these buckets are acoustically adjacent, the move requires stronger evidence."
        if has_close_alt_anchor:
            evidence += "\n- Close verified anchor match supports this target."
        if confidence_caution:
            evidence += "\n- Original classification was confident, so this suggestion needs manual review."
        also_matched = [
            f"- Also matched {_bucket_label('', category, '')} ({count}/10 neighbors)"
            for category, count in category_counts.most_common(4)
            if category != alt_category
        ]
        also_matched.extend([
            f"- Also matched {_bucket_label(audio_type, category, subcategory)} ({count}/10 neighbors)"
            for (audio_type, category, subcategory), count in counts.most_common(4)
            if (audio_type, category, subcategory) != (alt_audio_type, alt_category, alt_subcategory)
        ])
        if also_matched:
            evidence += "\n" + "\n".join(also_matched[:3])
        if confidence is None:
            evidence += "\n- Classification confidence unavailable."
        candidates.append(
            RefinementCandidate(
                candidate_id=_candidate_id(record.record_id, alt_audio_type, alt_category, alt_subcategory),
                record_id=record.record_id,
                current_category=record.category,
                current_subcategory=record.subcategory,
                suggested_category=alt_category,
                suggested_subcategory=alt_subcategory,
                evidence=evidence,
                confidence_score=round(winner_score, 6),
                state=state,
                current_audio_type=record.audio_type,
                suggested_audio_type=alt_audio_type,
            )
        )
    return candidates
