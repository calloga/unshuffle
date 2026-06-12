"""Central GUI style facade."""

from .theme import ThemeManager
from .tokens_geometry import DEFAULT_ZOOM_PERCENT, ZOOM_PRESETS, clamp_zoom
from .theme import normalize_theme_key, resolve_system_theme_key
from .tokens_semantic import (
    ASH,
    ASH_THEME_KEY,
    DEFAULT,
    DEFAULT_THEME_KEY,
    CATEGORY_IDENTITY_MAP,
    OCEAN,
    OCEAN_THEME_KEY,
    PEARL,
    PEARL_THEME_KEY,
    SUNSET,
    SUNSET_THEME_KEY,
    SYSTEM_THEME_KEY,
    THEMES,
    ThemeColors,
)

__all__ = [
    "ThemeManager",
    "ThemeColors",
    "THEMES",
    "SYSTEM_THEME_KEY",
    "DEFAULT_THEME_KEY",
    "OCEAN_THEME_KEY",
    "ASH_THEME_KEY",
    "SUNSET_THEME_KEY",
    "PEARL_THEME_KEY",
    "CATEGORY_IDENTITY_MAP",
    "OCEAN",
    "ASH",
    "SUNSET",
    "PEARL",
    "DEFAULT",
    "normalize_theme_key",
    "resolve_system_theme_key",
    "ZOOM_PRESETS",
    "DEFAULT_ZOOM_PERCENT",
    "clamp_zoom",
]
