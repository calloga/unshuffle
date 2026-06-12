"""Shared geometry and spacing tokens for GUI styling."""

from __future__ import annotations

from dataclasses import dataclass


ZOOM_PRESETS = (90, 100, 110, 125)
DEFAULT_ZOOM_PERCENT = 100
WINDOW_MIN_WIDTH = 250
WINDOW_MIN_HEIGHT = 400
MAIN_LAYOUT_MARGIN_NONE = 0
AUDIO_BAR_COLLAPSED_HEIGHT = 0


@dataclass(frozen=True)
class SpacingScale:
    xs: int = 4
    sm: int = 6
    md: int = 8
    lg: int = 10
    xl: int = 12
    xxl: int = 15


@dataclass(frozen=True)
class RadiusScale:
    sm: int = 4
    md: int = 6
    lg: int = 10


SPACING = SpacingScale()
RADIUS = RadiusScale()


def clamp_zoom(zoom_percent: int) -> int:
    value = zoom_percent
    if value in ZOOM_PRESETS:
        return value
    # fallback to nearest preset
    return min(ZOOM_PRESETS, key=lambda z: abs(z - value))
