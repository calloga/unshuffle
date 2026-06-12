from __future__ import annotations

import math
import logging
from collections import defaultdict

import numpy as np
from PySide6.QtCore import QPointF

from .coherence_distance import _distance_between_payloads, _distance_payload_for_vector
from .coherence_geometry import _assign_to_sunflower_shell, _layer_point, _spread_duplicate_offsets, _sunflower_offsets
from .coherence_math import _degenerate_projection, _distance_matrix, _even_sample, _mds_coords, _normalize_coords, _stable_unit
from .coherence_view_model import AnalyzerPoint


def _continuous_acoustic_projection(
    points: list[AnalyzerPoint],
    distance_fn,
    *,
    vector_distance_fn=None,
    category_layers: bool = False,
) -> list[tuple[AnalyzerPoint, QPointF]]:
    count = len(points)
    if count <= 0:
        return []
    if count == 1:
        return [(points[0], QPointF(0.5, 0.5))]
    categories = {point.category for point in points if point.category}
    if category_layers and len(categories) > 1:
        return _category_layered_globe(points, distance_fn, vector_distance_fn or _default_vector_distance)
    if count <= 220:
        coords = _mds_coords(_distance_matrix_for_points(points, distance_fn))
        if coords.shape[1] >= 2 and not _degenerate_projection(coords):
            return _layered_globe_from_coords(points, coords)
    
  
    landmark_limit = 220
    if count > 5000:
        landmark_limit = 80
    elif count > 2000:
        landmark_limit = 120
    elif count > 1000:
        landmark_limit = 160
        
    landmark_indexes = _projection_landmark_indexes(points, limit=min(landmark_limit, count))
    if len(landmark_indexes) < 3:
        return _fallback_layered_globe(points)
    landmark_points = [points[idx] for idx in landmark_indexes]
    landmark_coords = _mds_coords(_distance_matrix_for_points(landmark_points, distance_fn))
    if landmark_coords.shape[1] < 2 or _degenerate_projection(landmark_coords):
        return _fallback_layered_globe(points)
    landmark_coords = _normalize_coords(landmark_coords, margin=-1.0)
    landmark_coords = (landmark_coords - 0.5) * 2.0
    coord_by_index = {
        source_idx: QPointF(float(landmark_coords[coord_idx, 0]), float(landmark_coords[coord_idx, 1]))
        for coord_idx, source_idx in enumerate(landmark_indexes)
    }
    local_coords = np.zeros((count, 2), dtype=float)
    spatial_index = None
    if count >= 200:
        try:
            from unshuffle.logic.coherence.spatial_index import SpatialIndex
            from unshuffle.core.features import normalize_distance_vector
            landmark_vectors = np.array([normalize_distance_vector(points[idx].vector) for idx in landmark_indexes], dtype=np.float32)
            spatial_index = SpatialIndex(landmark_vectors)
        except Exception:
            logging.exception("Coherence projection spatial index unavailable; falling back to direct landmark distances.")
            spatial_index = None

    for idx, point in enumerate(points):
        if idx in coord_by_index:
            coord = coord_by_index[idx]
            local_coords[idx, 0] = coord.x()
            local_coords[idx, 1] = coord.y()
            continue
        if spatial_index is not None:
            try:
                from unshuffle.core.features import normalize_distance_vector
                norm_v = normalize_distance_vector(point.vector)
                labels, _ = spatial_index.query(np.asarray(norm_v, dtype=np.float32), k=min(8, len(landmark_indexes)))
                cand_landmark_indices = [landmark_indexes[l] for l in labels[0]]
                nearest = sorted(
                    ((float(distance_fn(point, points[l_idx])), l_idx) for l_idx in cand_landmark_indices),
                    key=lambda item: (item[0], str(points[item[1]].record_id)),
                )
            except Exception:
                logging.exception("Coherence projection spatial query failed; falling back to direct landmark distances.")
                nearest = sorted(
                    ((float(distance_fn(point, points[landmark_idx])), landmark_idx) for landmark_idx in landmark_indexes),
                    key=lambda item: (item[0], str(points[item[1]].record_id)),
                )[:8]
        else:
            nearest = sorted(
                ((float(distance_fn(point, points[landmark_idx])), landmark_idx) for landmark_idx in landmark_indexes),
                key=lambda item: (item[0], str(points[item[1]].record_id)),
            )[:8]
        if not nearest:
            continue
        if nearest[0][0] <= 1e-9:
            coord = coord_by_index[nearest[0][1]]
            local_coords[idx, 0] = coord.x()
            local_coords[idx, 1] = coord.y()
            continue
        total_weight = 0.0
        x = 0.0
        y = 0.0
        for distance, landmark_idx in nearest:
            weight = 1.0 / max(distance * distance, 1e-9)
            coord = coord_by_index[landmark_idx]
            total_weight += weight
            x += coord.x() * weight
            y += coord.y() * weight
        local_coords[idx, 0] = x / total_weight
        local_coords[idx, 1] = y / total_weight
    return _layered_globe_from_coords(points, local_coords)


def _category_layered_globe(points: list[AnalyzerPoint], distance_fn, vector_distance_fn) -> list[tuple[AnalyzerPoint, QPointF]]:
    by_category: dict[str, list[AnalyzerPoint]] = defaultdict(list)
    for point in points:
        by_category[point.category or "Uncategorized"].append(point)
    categories = sorted(by_category)
    if len(categories) <= 1:
        return _continuous_acoustic_projection(points, distance_fn, vector_distance_fn=vector_distance_fn, category_layers=False)

    category_centroids = {category: _mean_vector(category_points) for category, category_points in by_category.items()}
    global_centroid = _mean_vector(points)
    category_order = sorted(
        categories,
        key=lambda category: (
            float(vector_distance_fn(category_centroids.get(category, []), global_centroid)),
            category,
        ),
    )
    layer_count = len(category_order)
    layer_by_category = {category: idx for idx, category in enumerate(category_order)}
    projected: list[tuple[AnalyzerPoint, QPointF]] = []
    for category in category_order:
        category_points = by_category[category]
        layer = layer_by_category[category]
        inner, outer = _layer_band_bounds(layer, layer_count)
        local_coords = _local_acoustic_coords(category_points, distance_fn)
        projected.extend(_points_on_layer(category_points, local_coords, inner, outer))
    return projected


def _local_acoustic_coords(points: list[AnalyzerPoint], distance_fn) -> np.ndarray:
    count = len(points)
    if count <= 1:
        return np.zeros((count, 2), dtype=float)
    if count <= 160:
        coords = _mds_coords(_distance_matrix_for_points(points, distance_fn))
        if coords.shape[1] >= 2 and not _degenerate_projection(coords):
            return coords
    landmark_indexes = _projection_landmark_indexes(points, limit=min(120, count))
    if len(landmark_indexes) < 3:
        offsets = _sunflower_offsets(count)
        return np.array([[offset.x(), offset.y()] for offset in offsets], dtype=float)
    landmark_points = [points[idx] for idx in landmark_indexes]
    landmark_coords = _mds_coords(_distance_matrix_for_points(landmark_points, distance_fn))
    if landmark_coords.shape[1] < 2 or _degenerate_projection(landmark_coords):
        offsets = _sunflower_offsets(count)
        return np.array([[offset.x(), offset.y()] for offset in offsets], dtype=float)
    landmark_coords = _normalize_coords(landmark_coords, margin=-1.0)
    landmark_coords = (landmark_coords - 0.5) * 2.0
    coord_by_index = {
        source_idx: QPointF(float(landmark_coords[coord_idx, 0]), float(landmark_coords[coord_idx, 1]))
        for coord_idx, source_idx in enumerate(landmark_indexes)
    }
    coords = np.zeros((count, 2), dtype=float)
    spatial_index = None
    if count >= 200:
        try:
            from unshuffle.logic.coherence.spatial_index import SpatialIndex
            from unshuffle.core.features import normalize_distance_vector
            landmark_vectors = np.array([normalize_distance_vector(points[idx].vector) for idx in landmark_indexes], dtype=np.float32)
            spatial_index = SpatialIndex(landmark_vectors)
        except Exception:
            logging.exception("Local coherence projection spatial index unavailable; falling back to direct landmark distances.")
            spatial_index = None

    for idx, point in enumerate(points):
        if idx in coord_by_index:
            coord = coord_by_index[idx]
            coords[idx, 0] = coord.x()
            coords[idx, 1] = coord.y()
            continue
        if spatial_index is not None:
            try:
                from unshuffle.core.features import normalize_distance_vector
                norm_v = normalize_distance_vector(point.vector)
                labels, _ = spatial_index.query(np.asarray(norm_v, dtype=np.float32), k=min(8, len(landmark_indexes)))
                cand_landmark_indices = [landmark_indexes[l] for l in labels[0]]
                nearest = sorted(
                    ((float(distance_fn(point, points[l_idx])), l_idx) for l_idx in cand_landmark_indices),
                    key=lambda item: (item[0], str(points[item[1]].record_id)),
                )
            except Exception:
                logging.exception("Local coherence projection spatial query failed; falling back to direct landmark distances.")
                nearest = sorted(
                    ((float(distance_fn(point, points[landmark_idx])), landmark_idx) for landmark_idx in landmark_indexes),
                    key=lambda item: (item[0], str(points[item[1]].record_id)),
                )[:8]
        else:
            nearest = sorted(
                ((float(distance_fn(point, points[landmark_idx])), landmark_idx) for landmark_idx in landmark_indexes),
                key=lambda item: (item[0], str(points[item[1]].record_id)),
            )[:8]
        if not nearest:
            continue
        if nearest[0][0] <= 1e-9:
            coord = coord_by_index[nearest[0][1]]
            coords[idx, 0] = coord.x()
            coords[idx, 1] = coord.y()
            continue
        total_weight = 0.0
        x = 0.0
        y = 0.0
        for distance, landmark_idx in nearest:
            weight = 1.0 / max(distance * distance, 1e-9)
            coord = coord_by_index[landmark_idx]
            total_weight += weight
            x += coord.x() * weight
            y += coord.y() * weight
        coords[idx, 0] = x / total_weight
        coords[idx, 1] = y / total_weight
    return coords


def _points_on_layer(
    points: list[AnalyzerPoint],
    coords: np.ndarray,
    inner: float,
    outer: float,
) -> list[tuple[AnalyzerPoint, QPointF]]:
    if not points:
        return []
    coords = np.asarray(coords, dtype=float)
    if coords.shape[0] != len(points) or coords.shape[1] < 2:
        offsets = _sunflower_offsets(len(points))
        coords = np.array([[offset.x(), offset.y()] for offset in offsets], dtype=float)
    normalized = _normalize_coords(coords[:, :2], margin=0.0)
    thickness = max(0.012, (outer - inner) * 0.74)
    center = (inner + outer) / 2.0
    output: list[tuple[AnalyzerPoint, QPointF]] = []
    for idx, point in enumerate(points):
        t = float(normalized[idx, 0])
        band_fraction = center + (float(normalized[idx, 1]) - 0.5) * thickness
        band_fraction = max(inner + 0.006, min(outer - 0.006, band_fraction))
        tangent_jitter = (_stable_unit(point.record_id, "category-layer-t") - 0.5) * 0.012
        radius_jitter = (_stable_unit(point.record_id, "category-layer-r") - 0.5) * 0.006
        pos = _layer_point(t + tangent_jitter, band_fraction + radius_jitter)
        output.append((point, QPointF(max(0.02, min(0.98, pos.x())), max(0.04, min(0.96, pos.y())))))
    return output


def _layered_globe_from_coords(points: list[AnalyzerPoint], coords: np.ndarray) -> list[tuple[AnalyzerPoint, QPointF]]:
    coords = np.asarray(coords, dtype=float)
    if coords.shape[0] != len(points) or coords.shape[1] < 2:
        return _fallback_layered_globe(points)
    centered = coords - np.mean(coords[:, :2], axis=0)
    radii = np.linalg.norm(centered[:, :2], axis=1)
    max_radius = max(float(np.max(radii)), 1e-9)
    angles = np.arctan2(centered[:, 1], centered[:, 0])
    norm_radius = np.clip(radii / max_radius, 0.0, 1.0)
    layer_count = _layer_count_for_points(len(points))
    layers = np.minimum(layer_count - 1, np.floor(norm_radius * layer_count).astype(int))
    output = []
    for layer in range(layer_count):
        indexes = [idx for idx, assigned_layer in enumerate(layers) if assigned_layer == layer]
        if not indexes:
            continue
        layer_angles = np.array([angles[idx] for idx in indexes], dtype=float)
        layer_radii = np.array([norm_radius[idx] for idx in indexes], dtype=float)
        angle_min = float(np.min(layer_angles))
        angle_span = max(float(np.max(layer_angles) - angle_min), 1e-9)
        radius_center = float(np.mean(layer_radii))
        radius_span = max(float(np.max(layer_radii) - np.min(layer_radii)), 1e-9)
        inner, outer = _layer_band_bounds(layer, layer_count)
        layer_fraction = (inner + outer) / 2.0
        thickness = max(0.01, (outer - inner) * 0.42)
        for idx in indexes:
            t = (float(angles[idx]) - angle_min) / angle_span
            t = max(0.0, min(1.0, t))
            band_fraction = layer_fraction + ((float(norm_radius[idx]) - radius_center) / radius_span) * thickness
            band_fraction = max(inner + 0.006, min(outer - 0.006, band_fraction))
            tangent_jitter = (_stable_unit(points[idx].record_id, "t") - 0.5) * 0.018
            pos = _layer_point(t + tangent_jitter, band_fraction)
            output.append((points[idx], QPointF(max(0.02, min(0.98, pos.x())), max(0.04, min(0.96, pos.y())))))
    return output


def _layer_count_for_points(count: int) -> int:
    return max(4, min(8, (round(math.sqrt(max(1, count)) / 14.0)) + 3))


def _background_layer_count(projected: list[tuple[AnalyzerPoint, QPointF]], category_filter: str = "") -> int:
    if not projected:
        return 0
    categories = {point.category for point, _pos in projected if point.category}
    if not category_filter and len(categories) > 1:
        return len(categories)
    return _layer_count_for_points(len(projected))


def _layer_band_bounds(layer: int, layer_count: int) -> tuple[float, float]:
    gap = 0.0
    inner = 0.07 + layer / max(1, layer_count) * 0.88 + gap
    outer = 0.07 + (layer + 1) / max(1, layer_count) * 0.88 - gap
    return inner, max(inner + 0.01, outer)


def _projection_landmark_indexes(points: list[AnalyzerPoint], *, limit: int) -> list[int]:
    if len(points) <= limit:
        return list(range(len(points)))
    by_cluster: dict[str, list[int]] = defaultdict(list)
    for idx, point in enumerate(points):
        by_cluster[point.cluster_id].append(idx)
    selected: list[int] = []
    for _cluster_id, indexes in sorted(by_cluster.items(), key=lambda item: (-len(item[1]), item[0])):
        selected.extend(_even_sample(indexes, min(3, len(indexes))))
        if len(selected) >= limit:
            return sorted(set(selected[:limit]))
    remaining = [idx for idx in range(len(points)) if idx not in set(selected)]
    selected.extend(_even_sample(remaining, max(0, limit - len(set(selected)))))
    return sorted(set(selected))[:limit]


def _fallback_layered_globe(points: list[AnalyzerPoint]) -> list[tuple[AnalyzerPoint, QPointF]]:
    offsets = _sunflower_offsets(len(points))
    return [
        (point, QPointF(0.5 + offset.x() * 0.46, 0.5 + offset.y() * 0.46))
        for point, offset in zip(points, offsets)
    ]


def _mean_vector(points: list[AnalyzerPoint]) -> list[float]:
    if not points:
        return []
    width = len(points[0].vector or [])
    if width <= 0:
        return []
    totals = [0.0] * width
    count = 0
    for point in points:
        vector = point.vector or []
        if len(vector) != width:
            continue
        count += 1
        for idx, value in enumerate(vector):
            totals[idx] += value
    if not count:
        return []
    return [value / count for value in totals]


def _squared_vector_distance(left: list[float], right: list[float]) -> float:
    if not left or not right:
        return 0.0
    total = 0.0
    for l_value, r_value in zip(left, right):
        delta = l_value - r_value
        total += delta * delta
    return total


def _distance_matrix_for_points(points: list[AnalyzerPoint], distance_fn) -> np.ndarray:
    count = len(points)
    matrix = np.zeros((count, count), dtype=float)
    for i in range(count):
        for j in range(i + 1, count):
            distance = float(distance_fn(points[i], points[j]))
            matrix[i, j] = distance
            matrix[j, i] = distance
    return matrix


def _default_vector_distance(left: list[float], right: list[float]) -> float:
    distance = _distance_between_payloads(
        _distance_payload_for_vector(left or []),
        _distance_payload_for_vector(right or []),
    )
    if not math.isfinite(distance):
        return 10.0
    return max(0.0, distance)


def _landmark_cluster_offsets(
    points: list[AnalyzerPoint],
    representative: list[float],
    distance_fn,
    *,
    vector_distance_fn=None,
    sunflower_shell: bool = False,
) -> list[QPointF]:
    count = len(points)
    if count <= 1:
        return [QPointF(0.0, 0.0)]
    landmark_limit = min(96, count)
    vector_distance_fn = vector_distance_fn or _default_vector_distance
    try:
        ordered_indexes = sorted(
            range(count),
            key=lambda idx: (float(vector_distance_fn(points[idx].vector, representative)), str(points[idx].record_id)),
        )
        landmark_indexes = sorted(set(_even_sample(ordered_indexes, landmark_limit)))
        if len(landmark_indexes) < 3:
            return []
        landmark_points = [points[idx] for idx in landmark_indexes]
        coords = _mds_coords(_distance_matrix_for_points(landmark_points, distance_fn))
        if coords.shape[1] < 2 or _degenerate_projection(coords):
            return []
        coords = _normalize_coords(coords, margin=-1.0)
        coords = (coords - 0.5) * 2.0
        landmark_by_index = {
            source_idx: QPointF(float(coords[coord_idx, 0]), float(coords[coord_idx, 1]))
            for coord_idx, source_idx in enumerate(landmark_indexes)
        }
        offsets: list[QPointF] = [QPointF(0.0, 0.0) for _ in range(count)]
        for idx in range(count):
            if idx in landmark_by_index:
                offsets[idx] = landmark_by_index[idx]
                continue
            nearest = sorted(
                ((float(distance_fn(points[idx], points[landmark_idx])), landmark_idx) for landmark_idx in landmark_indexes),
                key=lambda item: (item[0], str(points[item[1]].record_id)),
            )[:6]
            if not nearest:
                continue
            if nearest[0][0] <= 1e-9:
                offsets[idx] = landmark_by_index[nearest[0][1]]
                continue
            total_weight = 0.0
            x = 0.0
            y = 0.0
            for distance, landmark_idx in nearest:
                weight = 1.0 / max(distance * distance, 1e-9)
                point = landmark_by_index[landmark_idx]
                total_weight += weight
                x += point.x() * weight
                y += point.y() * weight
            if total_weight > 0:
                offsets[idx] = QPointF(x / total_weight, y / total_weight)
        offsets = _spread_duplicate_offsets(offsets)
        if sunflower_shell:
            return _assign_to_sunflower_shell(offsets, count)
        return offsets
    except Exception:
        return []
