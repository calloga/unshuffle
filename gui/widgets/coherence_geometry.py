from __future__ import annotations

import math
from collections import defaultdict

from PySide6.QtCore import QPointF


def _layer_point(t: float, radius_fraction: float) -> QPointF:
    t = max(0.0, min(1.0, t))
    radius_fraction = max(0.0, min(1.0, radius_fraction))
    angle = math.tau * t - math.pi / 2.0
    x_radius = 0.47 * radius_fraction
    y_radius = 0.47 * radius_fraction
    return QPointF(
        0.5 + math.cos(angle) * x_radius,
        0.5 + math.sin(angle) * y_radius,
    )


def _sunflower_offsets(count: int) -> list[QPointF]:
    if count <= 1:
        return [QPointF(0.0, 0.0)]
    golden_angle = math.pi * (3.0 - math.sqrt(5.0))
    offsets = []
    for idx in range(count):
        radius = math.sqrt((idx + 0.5) / count)
        angle = idx * golden_angle
        offsets.append(QPointF(math.cos(angle) * radius, math.sin(angle) * radius))
    return offsets


def _assign_to_sunflower_shell(target_offsets: list[QPointF], count: int) -> list[QPointF]:
    if count <= 1 or len(target_offsets) != count:
        return target_offsets
    slots = _sunflower_offsets(count)
    target_order = sorted(
        range(count),
        key=lambda idx: (
            math.hypot(target_offsets[idx].x(), target_offsets[idx].y()),
            math.atan2(target_offsets[idx].y(), target_offsets[idx].x()),
            idx,
        ),
    )
    slot_order = sorted(
        range(count),
        key=lambda idx: (
            math.hypot(slots[idx].x(), slots[idx].y()),
            math.atan2(slots[idx].y(), slots[idx].x()),
            idx,
        ),
    )
    assigned = [QPointF(0.0, 0.0) for _ in range(count)]
    for target_idx, slot_idx in zip(target_order, slot_order):
        assigned[target_idx] = slots[slot_idx]
    return assigned


def _spread_duplicate_offsets(offsets: list[QPointF]) -> list[QPointF]:
    if len(offsets) <= 1:
        return offsets
    seen: dict[tuple[int, int], int] = defaultdict(int)
    spread = []
    for offset in offsets:
        key = (round(offset.x() * 1000), round(offset.y() * 1000))
        duplicate_index = seen[key]
        seen[key] += 1
        if duplicate_index <= 0:
            spread.append(offset)
            continue
        angle = duplicate_index * math.pi * (3.0 - math.sqrt(5.0))
        radius = min(0.08, 0.012 * math.sqrt(duplicate_index))
        spread.append(
            QPointF(
                max(-1.0, min(1.0, offset.x() + math.cos(angle) * radius)),
                max(-1.0, min(1.0, offset.y() + math.sin(angle) * radius)),
            )
        )
    return spread
