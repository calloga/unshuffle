from unshuffle.audio import SimilarityEngine
from unshuffle.core.features import FEATURE_VECTOR_SIZE


def test_similarity_ranking_uses_extracted_vectors():
    engine = SimilarityEngine(extractor_path="unused")
    target = _v([0.20, 0.30, 0.40, 0.10, 0.50] + [1.0] * 12 + [0.25])
    close = _v([0.22, 0.31, 0.42, 0.11, 0.48] + [1.0] * 12 + [0.25])
    far = _v([0.90, 0.80, 0.10, 0.60, 0.10] + [0.0] * 12 + [1.50])

    ranked = sorted(
        [
            ("close.wav", engine.calculate_distance(target, close)),
            ("far.wav", engine.calculate_distance(target, far)),
        ],
        key=lambda item: item[1],
    )

    assert ranked[0][0] == "close.wav"
    assert ranked[0][1] < ranked[1][1]


def test_similarity_tonalness_handles_fixture_vectors():
    engine = SimilarityEngine(extractor_path="unused")
    tonal_vector = _v([0.0] * engine.IDX_CHROMA_START + [1.0] + [0.0] * 11 + [0.5])
    flat_vector = _v([0.0] * engine.IDX_CHROMA_START + [1.0] * 12 + [0.5])

    assert engine._calculate_tonalness(tonal_vector[engine.IDX_CHROMA_START:engine.IDX_CHROMA_START + 12]) > 0
    assert engine._calculate_tonalness(flat_vector[engine.IDX_CHROMA_START:engine.IDX_CHROMA_START + 12]) == 0


def _v(values):
    return list(values) + [0.0] * (FEATURE_VECTOR_SIZE - len(values))
