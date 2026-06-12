from __future__ import annotations

import math
from dataclasses import dataclass

from unshuffle.core.features import (
    DEFAULT_DISTANCE_WEIGHTS,
    IDX_ACTIVE_DURATION,
    IDX_BRIGHTNESS,
    IDX_CHROMA_START,
    IDX_DECAY,
    FEATURE_VECTOR_SIZE,
    IDX_FFT_REGISTER,
    IDX_PERCUSSIVITY,
    IDX_ZCR,
    SILENCE_THRESHOLD,
    normalize_distance_vector,
)
from unshuffle.core.vector_math import calculate_tonalness


@dataclass(frozen=True)
class AnalyzerDistancePayload:
    normalized: tuple[float, ...]
    silence_prefix_sum: float
    total_sum: float
    chroma: tuple[float, ...]
    chroma_magnitude: float
    tonalness: float


def _distance_payload_for_vector(vector: list[float]) -> AnalyzerDistancePayload | None:
    if len(vector or []) != FEATURE_VECTOR_SIZE:
        return None
    normalized = tuple(value for value in normalize_distance_vector(vector))
    chroma = normalized[IDX_CHROMA_START : IDX_CHROMA_START + 12]
    return AnalyzerDistancePayload(
        normalized=normalized,
        silence_prefix_sum=sum(normalized[:IDX_CHROMA_START]),
        total_sum=sum(normalized),
        chroma=tuple(chroma),
        chroma_magnitude=math.sqrt(sum(value * value for value in chroma)),
        tonalness=calculate_tonalness(list(chroma)),
    )


def _distance_between_payloads(
    left: AnalyzerDistancePayload | None,
    right: AnalyzerDistancePayload | None,
    *,
    weights: dict[str, float] | None = None,
) -> float:
    if left is None or right is None:
        return float("inf")
    active_weights = DEFAULT_DISTANCE_WEIGHTS if weights is None else weights
    v1 = left.normalized
    v2 = right.normalized

    if left.silence_prefix_sum < SILENCE_THRESHOLD or right.silence_prefix_sum < SILENCE_THRESHOLD:
        s1 = left.total_sum < SILENCE_THRESHOLD
        s2 = right.total_sum < SILENCE_THRESHOLD
        return 0.0 if s1 == s2 else 2.0

    d_brightness = abs(v1[IDX_BRIGHTNESS] - v2[IDX_BRIGHTNESS])
    d_percussivity = abs(v1[IDX_PERCUSSIVITY] - v2[IDX_PERCUSSIVITY])
    d_fft_reg = abs(v1[IDX_FFT_REGISTER] - v2[IDX_FFT_REGISTER])
    d_zcr = abs(v1[IDX_ZCR] - v2[IDX_ZCR])
    d_decay = abs(v1[IDX_DECAY] - v2[IDX_DECAY])
    d_tonalness = abs(left.tonalness - right.tonalness)
    d_chroma = _cached_cosine_distance(left.chroma, right.chroma, left.chroma_magnitude, right.chroma_magnitude)

    chroma_weight = active_weights["chroma"]
    avg_percussivity = (v1[IDX_PERCUSSIVITY] + v2[IDX_PERCUSSIVITY]) / 2
    avg_tonalness = (left.tonalness + right.tonalness) / 2
    chroma_weight *= avg_tonalness * (1.0 - avg_percussivity)

    total_dist = (
        active_weights["brightness"] * d_brightness
        + active_weights["percussivity"] * d_percussivity
        + active_weights["fft_register"] * d_fft_reg
        + active_weights["zcr"] * d_zcr
        + active_weights["decay"] * d_decay
        + active_weights["tonalness"] * d_tonalness
        + chroma_weight * d_chroma
    )

    if len(v1) > IDX_ACTIVE_DURATION and len(v2) > IDX_ACTIVE_DURATION:
        d1 = v1[IDX_ACTIVE_DURATION]
        d2 = v2[IDX_ACTIVE_DURATION]
        if d1 > 0 and d2 > 0:
            length_diff = abs(d1 - d2)
            length_penalty = min(length_diff / max(d1, d2), 1.0)
            total_dist += active_weights["active_duration"] * length_penalty
    return total_dist


def _cached_cosine_distance(
    values_a: tuple[float, ...],
    values_b: tuple[float, ...],
    magnitude_a: float,
    magnitude_b: float,
) -> float:
    if magnitude_a < 1e-9 or magnitude_b < 1e-9:
        return 1.0
    if len(values_a) >= 12 and len(values_b) >= 12:
        dot_product = (
            values_a[0] * values_b[0]
            + values_a[1] * values_b[1]
            + values_a[2] * values_b[2]
            + values_a[3] * values_b[3]
            + values_a[4] * values_b[4]
            + values_a[5] * values_b[5]
            + values_a[6] * values_b[6]
            + values_a[7] * values_b[7]
            + values_a[8] * values_b[8]
            + values_a[9] * values_b[9]
            + values_a[10] * values_b[10]
            + values_a[11] * values_b[11]
        )
    else:
        dot_product = sum(a * b for a, b in zip(values_a, values_b))
    similarity = dot_product / (magnitude_a * magnitude_b)
    if similarity > 1.0:
        similarity = 1.0
    elif similarity < -1.0:
        similarity = -1.0
    return 1.0 - similarity
