from __future__ import annotations

import math


def clamp_density_ratio(value: float, *, min_ratio: float, max_ratio: float) -> float:
    if not math.isfinite(value):
        return 1.0
    return max(min_ratio, min(max_ratio, value))


def outlier_iqr_multiplier(
    density_ratio: float,
    *,
    base_multiplier: float,
    min_multiplier: float,
    max_multiplier: float,
    min_density_ratio: float,
    max_density_ratio: float,
) -> float:
    ratio = clamp_density_ratio(
        density_ratio,
        min_ratio=min_density_ratio,
        max_ratio=max_density_ratio,
    )
    multiplier = base_multiplier / math.sqrt(ratio)
    return max(min_multiplier, min(max_multiplier, multiplier))
