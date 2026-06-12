from __future__ import annotations

import math
import numpy as np


def _vector_signature(vector: list[float]) -> int:
    if not vector:
        return 0
    total = 0
    for idx, value in enumerate(vector):
        try:
            number = float(value)
        except (TypeError, ValueError):
            number = 0.0
        if not math.isfinite(number):
            number = 0.0
        total = (total + round(number * 1000.0) * (idx + 1)) & 0xFFFFFFFF
    return total


from functools import lru_cache


@lru_cache(maxsize=16384)
def _stable_hash(value: str) -> int:
    digest = 0
    for ch in (value or ""):
        digest = ((digest * 33) + ord(ch)) & 0xFFFFFFFF
    return digest


@lru_cache(maxsize=16384)
def _stable_hue(value: str) -> int:
    return (_stable_hash(value) * 137) % 360


def _stable_unit(*parts: object) -> float:
    return (_stable_hash(":".join(str(part) for part in parts)) % 10000) / 10000.0


def _normalize_coords(coords: np.ndarray, *, margin: float) -> np.ndarray:
    coords = np.asarray(coords, dtype=float)
    if coords.size == 0:
        return np.zeros((0, 2), dtype=float)
    if coords.ndim == 1:
        coords = coords.reshape((-1, 1))
    mins = coords.min(axis=0)
    spans = np.maximum(coords.max(axis=0) - mins, 1e-9)
    normalized = (coords - mins) / spans
    if margin < 0:
        return normalized
    return normalized * (1.0 - margin * 2.0) + margin


def _distance_matrix(vectors: list[list[float]], distance_fn) -> np.ndarray:
    count = len(vectors)
    matrix = np.zeros((count, count), dtype=float)
    for i in range(count):
        for j in range(i + 1, count):
            distance = float(distance_fn(vectors[i], vectors[j]))
            matrix[i, j] = distance
            matrix[j, i] = distance
    return matrix


def _mds_coords(distances: np.ndarray) -> np.ndarray:
    distances = np.asarray(distances, dtype=float)
    count = int(distances.shape[0]) if distances.ndim == 2 else 0
    if count <= 0:
        return np.zeros((0, 2), dtype=float)
    if count == 1:
        return np.zeros((1, 2), dtype=float)
    try:
        squared = distances**2
        centered = np.eye(count) - np.ones((count, count), dtype=float) / count
        gram = -0.5 * centered @ squared @ centered
        values, vectors = np.linalg.eigh(gram)
    except np.linalg.LinAlgError:
        return np.zeros((count, 2), dtype=float)
    order = np.argsort(values)[::-1]
    values = values[order]
    vectors = vectors[:, order]
    dims = min(2, len(values))
    coords = vectors[:, :dims] * np.sqrt(np.maximum(values[:dims], 0.0))
    if coords.shape[1] < 2:
        coords = np.pad(coords, ((0, 0), (0, 2 - coords.shape[1])))
    return coords


def _degenerate_projection(coords: np.ndarray) -> bool:
    if coords.size == 0:
        return True
    spans = np.ptp(coords, axis=0)
    largest = float(np.max(spans)) if len(spans) else 0.0
    smallest = float(np.min(spans)) if len(spans) else 0.0
    return largest <= 1e-9 or smallest / max(largest, 1e-9) < 0.06


def _even_sample(items: list, limit: int) -> list:
    if len(items) <= limit:
        return list(items)
    if limit <= 1:
        return [items[0]]
    step = (len(items) - 1) / (limit - 1)
    return [items[round(idx * step)] for idx in range(limit)]
