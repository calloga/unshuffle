import math
from typing import List


def _finite_float(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def calculate_tonalness(chroma: List[float]) -> float:
    """Derive a tonalness score from a 12-bin chroma vector."""
    if not chroma:
        return 0.0
    values = [number for value in chroma if (number := _finite_float(value)) is not None]
    if not values:
        return 0.0
    average = sum(max(0.0, min(1.0, value)) for value in values) / len(values)
    return max(0.0, min(1.0, 1.0 - average))


def cosine_distance(values_a: List[float], values_b: List[float]) -> float:
    """Calculate cosine distance as 1 - cosine similarity."""
    if len(values_a) != len(values_b):
        return float("inf")
    if any(_finite_float(value) is None for value in [*values_a, *values_b]):
        return float("inf")
    dot_product = sum(a * b for a, b in zip(values_a, values_b))
    magnitude_a = math.sqrt(sum(a * a for a in values_a))
    magnitude_b = math.sqrt(sum(b * b for b in values_b))

    if magnitude_a < 1e-9 or magnitude_b < 1e-9:
        return 1.0

    similarity = dot_product / (magnitude_a * magnitude_b)
    similarity = max(-1.0, min(1.0, similarity))
    return 1.0 - similarity
