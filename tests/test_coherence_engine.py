import struct

from dataclasses import replace

import numpy as np
import pytest

from unshuffle.audio import SimilarityEngine
from unshuffle.core.features import FEATURE_VECTOR_SIZE
from unshuffle.logic.coherence import CoherenceEngine
from unshuffle.logic.coherence.anchor_profiles import generate_anchor_candidates
from unshuffle.logic.coherence.models import (
    COHERENCE_STATUS_LOW,
    COHERENCE_STATUS_STABLE,
    COHERENCE_STATUS_UNDERREPRESENTED,
    CoherenceResult,
    CoherenceRecord,
    REFINEMENT_AUTO_STAGED,
    REFINEMENT_PENDING,
)
from unshuffle.logic.coherence.vector_index import records_from_staging_rows, valid_coherence_vector


class _EuclideanSimilarity:
    def calculate_distance(self, v1, v2, d1=0.0, d2=0.0):
        return sum((float(a) - float(b)) ** 2 for a, b in zip(v1, v2)) ** 0.5


def _vec(x):
    base = [float(x), 0.2, 0.2, 0.1, 0.1, *([0.0] * 12), 0.5]
    return base + [0.0] * (FEATURE_VECTOR_SIZE - len(base))


def _record(record_id, x, *, category="Bass", subcategory="Sub", audio_type="Oneshots"):
    return CoherenceRecord(
        record_id=str(record_id),
        category=category,
        subcategory=subcategory,
        vector=_vec(x),
        classification_confidence=0.4,
        audio_type=audio_type,
    )


def _blob(values):
    return struct.pack("<" + "f" * len(values), *values)


def test_valid_coherence_vector_requires_current_v1_length():
    assert valid_coherence_vector(_blob(_vec(0.0))) is not None
    assert valid_coherence_vector(_blob([0.0] * 17)) is None
    assert valid_coherence_vector([0.0] * (FEATURE_VECTOR_SIZE + 1)) is None


def test_vectorized_distance_rejects_obsolete_vector_lengths():
    engine = CoherenceEngine()
    records = [_record(idx, idx / 100) for idx in range(3)]
    records[1] = replace(records[1], vector=[0.0] * 17)

    assert engine._pairwise_distances_vectorized(records) is None
    assert engine._distances_from_vectorized(_vec(0.0), records) is None


def test_vector_index_threshold_counts_only_eligible_valid_vectors():
    rows = []
    for idx in range(8):
        rows.append(
            {
                "row_id": idx,
                "source_path": f"D:/Samples/{idx}.wav",
                "category": "Bass",
                "subcategory": "Sub",
                "confidence": "0.8",
                "acoustic_vector": _blob(_vec(idx / 100)),
            }
        )
    rows.append({"row_id": 20, "source_path": "D:/Samples/info.txt", "category": "Non-Audio Assets"})
    records, stats = records_from_staging_rows(rows)
    assert len(records) == 8
    assert stats.eligible_records == 8
    assert stats.valid_vector_records == 8
    assert stats.can_run


def test_malformed_verified_anchor_radius_is_ignored():
    engine = CoherenceEngine(
        verified_anchors=[
            {
                "audio_type": "Oneshots",
                "category": "Bass",
                "subcategory": "Sub",
                "medoid_vector": _vec(0.0),
                "coherence_radius": "not-a-radius",
            }
        ]
    )
    engine.similarity_engine = _EuclideanSimilarity()

    result = engine._anchor_fit(_record("target", 0.01), "Oneshots", "Bass", "Sub")

    assert result["close"] is False
    assert result["radius"] == 0.0


def test_refinement_candidates_fall_back_to_later_supported_category():
    from unshuffle.logic.coherence.refinement_candidates import refinement_candidates_for_engine

    target = _record("target", 0.0, category="Bass", subcategory="Old")
    supported = [_record(f"k{idx}", 0.2 + idx * 0.01, category="Kicks", subcategory="New") for idx in range(4)]
    unsupported = [_record("s0", 0.01, category="Snares", subcategory="Tiny")]
    records = [target, *unsupported, *supported]
    result = CoherenceResult(
        record_id="target",
        category="Bass",
        subcategory="Old",
        coherence_status=COHERENCE_STATUS_LOW,
        coherence_score=0.1,
    )

    class _Engine:
        def _global_neighbors(self, _record, _records, _limit):
            return [(unsupported[0], 0.01), *[(record, 0.2) for record in supported]]

        def _anchor_fit(self, *_args):
            return {"close": False}

        def _target_cluster_adjacency(self, *_args):
            return None

        def _refinement_state(self, **_kwargs):
            return REFINEMENT_PENDING

        def _second_best_improvement(self, *_args):
            return 0.0

    candidates = refinement_candidates_for_engine(
        _Engine(),
        records,
        [result],
        {"target": {"assigned_medoid_distance": 1.0}},
        [],
        normal_improvement_threshold=0.15,
        strong_improvement_threshold=0.35,
        cluster_adjacency_travel_penalty=0.5,
    )

    assert len(candidates) == 1
    assert candidates[0].suggested_category == "Kicks"
    assert candidates[0].suggested_subcategory == "New"


def test_vector_index_preserves_blank_subcategory():
    records, stats = records_from_staging_rows(
        [
            {
                "row_id": 1,
                "source_path": "D:/Samples/kick.wav",
                "category": "Kicks",
                "subcategory": "",
                "confidence": "0.8",
                "acoustic_vector": _blob(_vec(0.1)),
            }
        ]
    )

    assert stats.valid_vector_records == 1
    assert records[0].subcategory == ""


def test_vector_index_groups_minimum_by_audio_type():
    rows = []
    for idx in range(4):
        rows.append(
            {
                "row_id": idx,
                "source_path": f"D:/Samples/loop-{idx}.wav",
                "category": "Bass",
                "subcategory": "Sub",
                "audio_type": "Loops",
                "confidence": "0.8",
                "acoustic_vector": _blob(_vec(idx / 100)),
            }
        )
        rows.append(
            {
                "row_id": idx + 10,
                "source_path": f"D:/Samples/shot-{idx}.wav",
                "category": "Bass",
                "subcategory": "Sub",
                "audio_type": "Oneshots",
                "confidence": "0.8",
                "acoustic_vector": _blob(_vec(idx / 100)),
            }
        )

    records, stats = records_from_staging_rows(rows)

    assert len(records) == 8
    assert {record.audio_type for record in records} == {"Loops", "Oneshots"}
    assert not stats.has_minimum_group


def test_coherence_engine_marks_small_groups_underrepresented():
    engine = CoherenceEngine(_EuclideanSimilarity())
    results, candidates = engine.audit([_record(1, 0.1), _record(2, 0.11)])
    assert {result.coherence_status for result in results} == {COHERENCE_STATUS_UNDERREPRESENTED}
    assert candidates == []


def test_verified_anchor_stabilizes_underrepresented_group():
    engine = CoherenceEngine(
        _EuclideanSimilarity(),
        verified_anchors=[
            {
                "category": "Bass",
                "subcategory": "Sub",
                "medoid_vector": _vec(0.1),
                "coherence_radius": 0.1,
            }
        ],
    )
    results, _candidates = engine.audit([_record(1, 0.11)])
    assert results[0].coherence_status == COHERENCE_STATUS_STABLE
    assert results[0].anchor_fit_status == "close"


def test_verified_anchor_matches_audio_type_when_present():
    engine = CoherenceEngine(
        _EuclideanSimilarity(),
        verified_anchors=[
            {
                "audio_type": "Loops",
                "category": "Bass",
                "subcategory": "Sub",
                "medoid_vector": _vec(0.1),
                "coherence_radius": 0.1,
            }
        ],
    )

    loop_results, _ = engine.audit([_record("loop", 0.11, audio_type="Loops")])
    shot_results, _ = engine.audit([_record("shot", 0.11, audio_type="Oneshots")])

    assert loop_results[0].coherence_status == COHERENCE_STATUS_STABLE
    assert shot_results[0].coherence_status == COHERENCE_STATUS_UNDERREPRESENTED


def test_coherence_engine_keeps_tight_group_stable():
    engine = CoherenceEngine(_EuclideanSimilarity())
    records = [_record(i, 0.1 + (i * 0.001)) for i in range(8)]
    results, candidates = engine.audit(records)
    assert candidates == []
    assert all(result.coherence_status == COHERENCE_STATUS_STABLE for result in results)


def test_coherence_engine_separates_same_category_by_audio_type():
    engine = CoherenceEngine(_EuclideanSimilarity())
    records = [_record(f"loop-{idx}", 0.1 + (idx * 0.001), audio_type="Loops") for idx in range(8)]
    records.extend(_record(f"shot-{idx}", 5.0 + (idx * 0.001), audio_type="Oneshots") for idx in range(8))

    results, candidates = engine.audit(records)

    assert candidates == []
    by_id = {result.record_id: result for result in results}
    assert by_id["loop-0"].cluster_id.startswith("loops_bass_sub_")
    assert by_id["shot-0"].cluster_id.startswith("oneshots_bass_sub_")
    assert all(result.coherence_status == COHERENCE_STATUS_STABLE for result in results)


def test_generated_anchor_candidates_preserve_audio_type():
    engine = CoherenceEngine(_EuclideanSimilarity())
    records = [_record(f"loop-{idx}", 0.1 + (idx * 0.001), audio_type="Loops") for idx in range(8)]
    records.extend(_record(f"shot-{idx}", 5.0 + (idx * 0.001), audio_type="Oneshots") for idx in range(8))
    results, _candidates = engine.audit(records)

    anchors = generate_anchor_candidates(records, results, engine.similarity_engine)

    assert {anchor.audio_type for anchor in anchors} == {"Loops", "Oneshots"}
    assert {anchor.profile_payload["audio_type"] for anchor in anchors} == {"Loops", "Oneshots"}


def test_density_ratio_tunes_high_outlier_sensitivity():
    engine = CoherenceEngine(_EuclideanSimilarity())
    values = np.array([1.0, 1.1, 1.2, 2.7])
    clusters = np.array([0, 0, 0, 1])

    sparse_flags = engine._high_outliers(values, clusters, {0: 1.0, 1: 0.25})
    dense_flags = engine._high_outliers(values, clusters, {0: 1.0, 1: 4.0})

    assert not bool(sparse_flags[3])
    assert bool(dense_flags[3])


def test_coherence_engine_finds_low_coherence_outlier():
    engine = CoherenceEngine(_EuclideanSimilarity())
    records = [_record(i, 0.1 + (i * 0.001)) for i in range(8)]
    records.append(_record(99, 5.0))
    results, _candidates = engine.audit(records)
    by_id = {result.record_id: result for result in results}
    assert by_id["99"].coherence_status == COHERENCE_STATUS_LOW


def test_vectorized_default_distances_match_similarity_engine():
    similarity = SimilarityEngine()
    engine = CoherenceEngine(similarity)
    records = [
        CoherenceRecord(
            str(idx),
            "Bass",
            "Sub",
            _vec(0.2 + idx),
        )
        for idx in range(4)
    ]
    distances = engine._pairwise_distances(records)
    for left in range(len(records)):
        for right in range(len(records)):
            expected = similarity.calculate_distance(records[left].vector, records[right].vector)
            assert distances[left, right] == pytest.approx(expected, abs=1e-6)


def test_vectorized_default_distances_apply_feature_scale_normalization():
    similarity = SimilarityEngine()
    engine = CoherenceEngine(similarity)
    records = [
        CoherenceRecord("normalized", "Bass", "Sub", _vec(0.5)),
        CoherenceRecord("raw", "Bass", "Sub", _vec(0.5)),
    ]

    distances = engine._pairwise_distances(records)

    assert distances[0, 1] == pytest.approx(0.0, abs=1e-6)


def test_vectorized_input_cache_invalidates_when_same_ids_have_new_vectors():
    similarity = SimilarityEngine()
    engine = CoherenceEngine(similarity)
    first = [
        CoherenceRecord("same-left", "Bass", "Sub", _vec(0.1)),
        CoherenceRecord("same-right", "Bass", "Sub", _vec(0.9)),
    ]
    second = [
        CoherenceRecord("same-left", "Bass", "Sub", _vec(0.2)),
        CoherenceRecord("same-right", "Bass", "Sub", _vec(0.2)),
    ]

    first_distances = engine._pairwise_distances(first)
    second_distances = engine._pairwise_distances(second)

    assert first_distances[0, 1] > 0
    assert second_distances[0, 1] == pytest.approx(0.0, abs=1e-6)


def test_refinement_evidence_includes_also_matched_neighbors():
    engine = CoherenceEngine(_EuclideanSimilarity())
    target = _record("target", 0.0, category="Bass", subcategory="")
    records = [target]
    records.extend(_record(f"k{idx}", 0.10 + idx * 0.001, category="Kicks", subcategory="") for idx in range(4))
    records.extend(_record(f"s{idx}", 0.20 + idx * 0.001, category="Snares", subcategory="") for idx in range(3))
    records.extend(_record(f"c{idx}", 0.30 + idx * 0.001, category="Claps", subcategory="") for idx in range(3))

    candidates = engine._refinement_candidates(
        records,
        [
            CoherenceResult(
                record_id="target",
                category="Bass",
                subcategory="",
                coherence_status=COHERENCE_STATUS_LOW,
                coherence_score=0.1,
            )
        ],
        {"target": {"assigned_medoid_distance": 1.0}},
    )

    assert len(candidates) == 1
    assert candidates[0].suggested_category == "Kicks"
    assert "\n- Also matched Snares" in candidates[0].evidence


def test_refinement_checks_category_before_suggesting_type_change():
    engine = CoherenceEngine(_EuclideanSimilarity())
    target = _record("target", 0.0, category="Kicks", subcategory="Generic", audio_type="Loops")
    records = [target]
    records.extend(
        _record(f"k{idx}", 0.10 + idx * 0.001, category="Kicks", subcategory="Generic", audio_type="Oneshots")
        for idx in range(5)
    )
    records.extend(
        _record(f"s{idx}", 0.30 + idx * 0.001, category="Snares", subcategory="Generic", audio_type="Loops")
        for idx in range(5)
    )

    candidates = engine._refinement_candidates(
        records,
        [CoherenceResult("target", "Kicks", "Generic", COHERENCE_STATUS_LOW, 0.1)],
        {"target": {"assigned_medoid_distance": 1.0}},
    )

    assert len(candidates) == 1
    assert candidates[0].suggested_category == "Kicks"
    assert candidates[0].suggested_audio_type == "Oneshots"
    assert "first point to Kicks" in candidates[0].evidence


def test_refinement_does_not_carry_cross_category_taxonomy_subcategory():
    engine = CoherenceEngine(_EuclideanSimilarity())
    target = _record("target", 0.0, category="Percussion", subcategory="Membranophones", audio_type="Oneshots")
    records = [target]
    records.extend(
        _record(f"k{idx}", 0.10 + idx * 0.001, category="Kicks", subcategory="Membranophones", audio_type="Oneshots")
        for idx in range(5)
    )
    records.extend(
        _record(f"p{idx}", 0.60 + idx * 0.001, category="Percussion", subcategory="Membranophones", audio_type="Oneshots")
        for idx in range(5)
    )

    candidates = engine._refinement_candidates(
        records,
        [CoherenceResult("target", "Percussion", "Membranophones", COHERENCE_STATUS_LOW, 0.1)],
        {"target": {"assigned_medoid_distance": 1.0}},
    )

    assert len(candidates) == 1
    assert candidates[0].suggested_category == "Kicks"
    assert candidates[0].suggested_subcategory == ""


def test_uncategorized_refinement_candidate_is_auto_staged():
    engine = CoherenceEngine(_EuclideanSimilarity())
    target = _record("target", 0.0, category="Uncategorized", subcategory="")
    records = [target]
    records.extend(_record(f"k{idx}", 0.10 + idx * 0.001, category="Kicks", subcategory="") for idx in range(4))
    records.extend(_record(f"s{idx}", 0.40 + idx * 0.001, category="Snares", subcategory="") for idx in range(3))
    records.extend(_record(f"c{idx}", 0.60 + idx * 0.001, category="Claps", subcategory="") for idx in range(3))

    candidates = engine._refinement_candidates(
        records,
        [CoherenceResult("target", "Uncategorized", "-", COHERENCE_STATUS_LOW, 0.1)],
        {"target": {"assigned_medoid_distance": 1.0}},
    )

    assert len(candidates) == 1
    assert candidates[0].state == REFINEMENT_AUTO_STAGED


def test_uncategorized_refinement_can_suggest_known_home_without_low_status():
    engine = CoherenceEngine(_EuclideanSimilarity())
    target = _record("target", 0.0, category="Uncategorized", subcategory="")
    records = [target]
    records.extend(_record(f"k{idx}", 0.10 + idx * 0.001, category="Kicks", subcategory="") for idx in range(4))
    records.extend(_record(f"s{idx}", 0.40 + idx * 0.001, category="Snares", subcategory="") for idx in range(3))
    records.extend(_record(f"u{idx}", 0.80 + idx * 0.001, category="Uncategorized", subcategory="") for idx in range(3))

    candidates = engine._refinement_candidates(
        records,
        [CoherenceResult("target", "Uncategorized", "", COHERENCE_STATUS_STABLE, 0.9)],
        {},
    )

    assert len(candidates) == 1
    assert candidates[0].suggested_category == "Kicks"
    assert candidates[0].state == REFINEMENT_AUTO_STAGED


def test_strong_refinement_candidate_is_auto_staged():
    engine = CoherenceEngine(_EuclideanSimilarity())
    target = _record("target", 0.0, category="Bass", subcategory="")
    target = replace(target, classification_confidence=1.0)
    records = [target]
    records.extend(_record(f"k{idx}", 0.10 + idx * 0.001, category="Kicks", subcategory="") for idx in range(6))
    records.extend(_record(f"s{idx}", 0.70 + idx * 0.001, category="Snares", subcategory="") for idx in range(4))

    candidates = engine._refinement_candidates(
        records,
        [CoherenceResult("target", "Bass", "", COHERENCE_STATUS_LOW, 0.1)],
        {"target": {"assigned_medoid_distance": 1.0}},
    )

    assert len(candidates) == 1
    assert candidates[0].state == REFINEMENT_AUTO_STAGED


def test_marginal_alternate_fit_is_not_refinement_candidate():
    engine = CoherenceEngine(_EuclideanSimilarity())
    target = _record("target", 0.0, category="Bass", subcategory="")
    records = [target]
    records.extend(_record(f"k{idx}", 0.90 + idx * 0.001, category="Kicks", subcategory="") for idx in range(6))
    records.extend(_record(f"s{idx}", 0.95 + idx * 0.001, category="Snares", subcategory="") for idx in range(4))

    candidates = engine._refinement_candidates(
        records,
        [CoherenceResult("target", "Bass", "", COHERENCE_STATUS_LOW, 0.1)],
        {"target": {"assigned_medoid_distance": 1.0}},
    )

    assert candidates == []


def test_close_cluster_adjacency_keeps_marginal_refinement_manual():
    engine = CoherenceEngine(_EuclideanSimilarity())
    target = _record("target", 0.0, category="Percussion", subcategory="Shakers")
    records = [target]
    records.extend(_record(f"h{idx}", 0.80 + idx * 0.001, category="Hats & Cymbals", subcategory="Hats") for idx in range(4))
    records.extend(_record(f"s{idx}", 0.95 + idx * 0.001, category="Snares", subcategory="") for idx in range(4))
    group_context = {"target": {"assigned_medoid_distance": 1.0, "cluster_id": "current"}}
    cluster_profiles = [
        {
            "cluster_id": "current",
            "audio_type": "Oneshots",
            "category": "Percussion",
            "subcategory": "Shakers",
            "medoid_vector": _vec(0.0),
            "radius": 0.5,
        },
        {
            "cluster_id": "hats",
            "audio_type": "Oneshots",
            "category": "Hats & Cymbals",
            "subcategory": "Hats",
            "medoid_vector": _vec(0.05),
            "radius": 0.5,
        },
    ]

    candidates_without_adjacency = engine._refinement_candidates(
        records,
        [CoherenceResult("target", "Percussion", "Shakers", COHERENCE_STATUS_LOW, 0.1)],
        {"target": {"assigned_medoid_distance": 1.0}},
    )
    candidates_with_adjacency = engine._refinement_candidates(
        records,
        [CoherenceResult("target", "Percussion", "Shakers", COHERENCE_STATUS_LOW, 0.1)],
        group_context,
        cluster_profiles,
    )

    assert len(candidates_without_adjacency) == 1
    assert len(candidates_with_adjacency) == 1
    assert candidates_with_adjacency[0].state == REFINEMENT_PENDING
    assert "acoustically adjacent" in candidates_with_adjacency[0].evidence


def test_refinement_evidence_shows_close_cluster_adjacency():
    engine = CoherenceEngine(_EuclideanSimilarity())
    target = _record("target", 0.0, category="Percussion", subcategory="Shakers")
    records = [target]
    records.extend(_record(f"h{idx}", 0.10 + idx * 0.001, category="Hats & Cymbals", subcategory="Hats") for idx in range(4))
    records.extend(_record(f"s{idx}", 0.70 + idx * 0.001, category="Snares", subcategory="") for idx in range(4))
    candidates = engine._refinement_candidates(
        records,
        [CoherenceResult("target", "Percussion", "Shakers", COHERENCE_STATUS_LOW, 0.1)],
        {"target": {"assigned_medoid_distance": 1.0, "cluster_id": "current"}},
        [
            {
                "cluster_id": "current",
                "audio_type": "Oneshots",
                "category": "Percussion",
                "subcategory": "Shakers",
                "medoid_vector": _vec(0.0),
                "radius": 0.5,
            },
            {
                "cluster_id": "hats",
                "audio_type": "Oneshots",
                "category": "Hats & Cymbals",
                "subcategory": "Hats",
                "medoid_vector": _vec(0.05),
                "radius": 0.5,
            },
        ],
    )

    assert len(candidates) == 1
    assert "Target is a close neighboring cluster" in candidates[0].evidence
    assert "the move requires stronger evidence" in candidates[0].evidence
    assert candidates[0].confidence_score < 0.9


def test_audit_results_include_nearest_adjacent_cluster_summary():
    engine = CoherenceEngine(_EuclideanSimilarity())
    records = []
    records.extend(_record(f"p{idx}", 0.00 + idx * 0.001, category="Percussion", subcategory="Shakers") for idx in range(3))
    records.extend(_record(f"h{idx}", 0.20 + idx * 0.001, category="Hats & Cymbals", subcategory="Hats") for idx in range(3))

    results, _candidates = engine.audit(records)

    summary = results[0].nearest_neighbor_summary
    assert summary is not None
    adjacency = summary["nearest_adjacent_cluster"]
    assert adjacency["category"] == "Hats & Cymbals"
    assert "adjacency_ratio" in adjacency


def test_candidate_below_strong_confidence_ratio_stays_pending():
    engine = CoherenceEngine(_EuclideanSimilarity())
    target = _record("target", 0.0, category="Bass", subcategory="")
    target = replace(target, classification_confidence=1.0)
    records = [target]
    records.extend(_record(f"k{idx}", 0.70 + idx * 0.001, category="Kicks", subcategory="") for idx in range(6))
    records.extend(_record(f"s{idx}", 0.90 + idx * 0.001, category="Snares", subcategory="") for idx in range(4))

    candidates = engine._refinement_candidates(
        records,
        [CoherenceResult("target", "Bass", "", COHERENCE_STATUS_LOW, 0.1)],
        {"target": {"assigned_medoid_distance": 1.0}},
    )

    assert len(candidates) == 1
    assert candidates[0].state == REFINEMENT_PENDING


def test_verified_anchor_supports_current_assignment_for_outlier():
    engine = CoherenceEngine(
        _EuclideanSimilarity(),
        verified_anchors=[
            {
                "category": "Bass",
                "subcategory": "Sub",
                "medoid_vector": _vec(5.0),
                "coherence_radius": 0.1,
            }
        ],
    )
    records = [_record(i, 0.1 + (i * 0.001)) for i in range(8)]
    records.append(_record(99, 5.0))

    results, candidates = engine.audit(records)
    by_id = {result.record_id: result for result in results}

    assert by_id["99"].coherence_status == COHERENCE_STATUS_STABLE
    assert by_id["99"].anchor_fit_status == "close"
    assert not by_id["99"].is_outlier
    assert candidates == []


def test_close_alternate_anchor_can_compensate_for_low_neighbor_count():
    engine = CoherenceEngine(
        _EuclideanSimilarity(),
        verified_anchors=[
            {
                "category": "Kicks",
                "subcategory": "",
                "medoid_vector": _vec(0.0),
                "coherence_radius": 0.1,
            }
        ],
    )
    target = _record("target", 0.0, category="Bass", subcategory="")
    records = [target]
    records.extend(_record(f"k{idx}", 0.05 + idx * 0.001, category="Kicks", subcategory="") for idx in range(2))

    candidates = engine._refinement_candidates(
        records,
        [CoherenceResult("target", "Bass", "", COHERENCE_STATUS_LOW, 0.1)],
        {"target": {"assigned_medoid_distance": 1.0}},
    )

    assert len(candidates) == 1
    assert candidates[0].suggested_category == "Kicks"
    assert "Close verified anchor match" in candidates[0].evidence
