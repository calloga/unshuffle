import json
import logging
import math
import struct
from typing import Dict, List, Optional, Sequence

from .vector_math import calculate_tonalness, cosine_distance


CURRENT_FEATURE_SPACE_VERSION = "unshuffle-audio-v1"
CURRENT_EXTRACTOR_VERSION = "unshuffle_extractor 1.0.0"
CURRENT_FEATURE_SCHEMA = (
    "brightness",
    "percussivity",
    "fft_register",
    "zcr",
    "decay",
    "chroma_0",
    "chroma_1",
    "chroma_2",
    "chroma_3",
    "chroma_4",
    "chroma_5",
    "chroma_6",
    "chroma_7",
    "chroma_8",
    "chroma_9",
    "chroma_10",
    "chroma_11",
    "active_duration",
    "loopiness_score",
    "transient_tail_score",
)
FEATURE_VECTOR_SIZE = len(CURRENT_FEATURE_SCHEMA)
CURRENT_FEATURE_VECTOR_SIZE = FEATURE_VECTOR_SIZE
CURRENT_VECTOR_SCHEMA = CURRENT_FEATURE_SCHEMA
FEATURE_INDEX = {name: idx for idx, name in enumerate(CURRENT_FEATURE_SCHEMA)}

IDX_BRIGHTNESS = FEATURE_INDEX["brightness"]
IDX_PERCUSSIVITY = FEATURE_INDEX["percussivity"]
IDX_FFT_REGISTER = FEATURE_INDEX["fft_register"]
IDX_ZCR = FEATURE_INDEX["zcr"]
IDX_DECAY = FEATURE_INDEX["decay"]
IDX_CHROMA_START = FEATURE_INDEX["chroma_0"]
IDX_ACTIVE_DURATION = FEATURE_INDEX["active_duration"]
IDX_LOOPINESS_SCORE = FEATURE_INDEX["loopiness_score"]
IDX_TRANSIENT_TAIL_SCORE = FEATURE_INDEX["transient_tail_score"]
SILENCE_THRESHOLD = 0.001

DEFAULT_DISTANCE_WEIGHTS = {
    "brightness": 1.0,
    "percussivity": 1.0,
    "fft_register": 1.0,
    "zcr": 1.0,
    "decay": 1.0,
    "tonalness": 1.0,
    "chroma": 0.5,
    "active_duration": 0.8,
}


def feature_index(name: str) -> int:
    return FEATURE_INDEX[name]


def vector_to_feature_values(vector: Sequence[float]) -> Dict[str, float]:
    if len(vector) != FEATURE_VECTOR_SIZE:
        return {}
    return {name: float(vector[idx]) for idx, name in enumerate(CURRENT_FEATURE_SCHEMA)}


def vector_from_feature_values(values: Dict[str, float]) -> Optional[List[float]]:
    try:
        return [float(values[name]) for name in CURRENT_FEATURE_SCHEMA]
    except (KeyError, TypeError, ValueError):
        return None


def feature_blob_from_vector(vector: Sequence[float]) -> Optional[bytes]:
    sanitized = sanitize_vector(list(vector))
    if not sanitized:
        return None
    return struct.pack("<" + ("f" * FEATURE_VECTOR_SIZE), *sanitized)


def feature_value(vector: Sequence[float] | bytes | str | None, name: str, default: float = 0.0) -> float:
    parsed = vector_from_blob(vector)
    if not parsed:
        return default
    idx = FEATURE_INDEX.get(name)
    if idx is None or idx >= len(parsed):
        return default
    return float(parsed[idx])


def normalize_distance_vector(vec: List[float]) -> List[float]:
    normalized = [item for item in vec]
    if len(normalized) <= IDX_DECAY:
        return normalized
    normalized[IDX_BRIGHTNESS] = _normalize_brightness(normalized[IDX_BRIGHTNESS])
    normalized[IDX_PERCUSSIVITY] = _clamp01(normalized[IDX_PERCUSSIVITY])
    normalized[IDX_FFT_REGISTER] = _normalize_fft_register(normalized[IDX_FFT_REGISTER])
    normalized[IDX_ZCR] = _clamp01(normalized[IDX_ZCR])
    normalized[IDX_DECAY] = _normalize_decay(normalized[IDX_DECAY])
    chroma = normalized[IDX_CHROMA_START : IDX_CHROMA_START + 12]
    if len(chroma) == 12:
        max_chroma = max(chroma)
        if max_chroma > 1.0:
            normalized[IDX_CHROMA_START : IDX_CHROMA_START + 12] = [
                _clamp01(value / max_chroma) for value in chroma
            ]
        else:
            normalized[IDX_CHROMA_START : IDX_CHROMA_START + 12] = [_clamp01(value) for value in chroma]
    for name in ("loopiness_score", "transient_tail_score"):
        idx = FEATURE_INDEX[name]
        if idx < len(normalized):
            normalized[idx] = _clamp01(normalized[idx])
    return normalized


def _normalize_brightness(value: float) -> float:
    value = max(0.0, value)
    if value > 4.0:
        value /= 10000.0
    return min(value, 2.0)


def _normalize_fft_register(value: float) -> float:
    value = max(0.0, value)
    if value > 4.0:
        value /= 16.0
    return min(value, 2.0)


def _normalize_decay(value: float) -> float:
    if value < 0.0:
        value = (value + 10.0) / 10.0
    return max(0.0, min(value, 2.0))


def _clamp01(value: float) -> float:
    return max(0.0, min(value, 1.0))


def vector_from_blob(value) -> Optional[List[float]]:
    if value is None:
        return None
    if isinstance(value, bytes):
        vector_size = len(value) // 4
        if len(value) % 4 != 0 or vector_size != FEATURE_VECTOR_SIZE:
            return None
        return list(struct.unpack("<" + ("f" * vector_size), value))
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except Exception:
            return None
    if isinstance(value, (list, tuple)) and len(value) == FEATURE_VECTOR_SIZE:
        try:
            return [float(item) for item in value]
        except (TypeError, ValueError):
            return None
    return None


def sanitize_vector(vec: List[float]) -> Optional[List[float]]:
    if not isinstance(vec, list) or len(vec) != FEATURE_VECTOR_SIZE:
        return None
    sanitized = []
    for idx, item in enumerate(vec):
        try:
            value = float(item)
        except (TypeError, ValueError):
            value = 0.0
        if not math.isfinite(value):
            logging.debug("Non-finite feature vector value at index %s; coercing to 0.0", idx)
            sanitized.append(0.0)
        elif idx == IDX_ACTIVE_DURATION:
            sanitized.append(max(0.0, min(60.0, value)))
        elif idx in {IDX_LOOPINESS_SCORE, IDX_TRANSIENT_TAIL_SCORE}:
            sanitized.append(_clamp01(value))
        else:
            sanitized.append(max(0.0, min(10.0, value)))
    return sanitized


def calculate_similarity_distance(
    v1: List[float],
    v2: List[float],
    weights: Optional[Dict[str, float]] = None,
    d1: float = 0.0,
    d2: float = 0.0,
) -> float:
    if len(v1) != FEATURE_VECTOR_SIZE or len(v2) != FEATURE_VECTOR_SIZE:
        return float("inf")

    active_weights = DEFAULT_DISTANCE_WEIGHTS if weights is None else weights
    v1 = normalize_distance_vector(v1)
    v2 = normalize_distance_vector(v2)
    if all(abs(left - right) <= 1e-9 for left, right in zip(v1, v2)):
        return 0.0

    if sum(v1[:IDX_CHROMA_START]) < SILENCE_THRESHOLD or sum(v2[:IDX_CHROMA_START]) < SILENCE_THRESHOLD:
        s1 = sum(v1) < SILENCE_THRESHOLD
        s2 = sum(v2) < SILENCE_THRESHOLD
        return 0.0 if s1 == s2 else 2.0

    d_brightness = abs(v1[IDX_BRIGHTNESS] - v2[IDX_BRIGHTNESS])
    d_percussivity = abs(v1[IDX_PERCUSSIVITY] - v2[IDX_PERCUSSIVITY])
    d_fft_reg = abs(v1[IDX_FFT_REGISTER] - v2[IDX_FFT_REGISTER])
    d_zcr = abs(v1[IDX_ZCR] - v2[IDX_ZCR])
    d_decay = abs(v1[IDX_DECAY] - v2[IDX_DECAY])

    c1 = v1[IDX_CHROMA_START : IDX_CHROMA_START + 12]
    c2 = v2[IDX_CHROMA_START : IDX_CHROMA_START + 12]
    t1 = calculate_tonalness(c1)
    t2 = calculate_tonalness(c2)
    d_tonalness = abs(t1 - t2)
    d_chroma = 0.0 if sum(abs(item) for item in c1) < 1e-9 and sum(abs(item) for item in c2) < 1e-9 else cosine_distance(c1, c2)

    chroma_weight = active_weights["chroma"]
    avg_percussivity = (v1[IDX_PERCUSSIVITY] + v2[IDX_PERCUSSIVITY]) / 2
    avg_tonalness = (t1 + t2) / 2
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

    d1 = v1[IDX_ACTIVE_DURATION] if len(v1) > IDX_ACTIVE_DURATION else d1
    d2 = v2[IDX_ACTIVE_DURATION] if len(v2) > IDX_ACTIVE_DURATION else d2
    if d1 > 0 and d2 > 0:
        length_diff = abs(d1 - d2)
        length_penalty = min(length_diff / max(d1, d2), 1.0)
        total_dist += active_weights["active_duration"] * length_penalty

    return total_dist
