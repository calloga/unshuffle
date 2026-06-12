"""Top-level package surface for Unshuffle.

Navigation:

- `unshuffle.logic`: analysis, classification, discovery, planning, execution
- `unshuffle.persistence`: DB access, staging search, metadata persistence
- `unshuffle.audio`: acoustic similarity and audio metadata helpers
- `unshuffle.runtime`: engine internals, bootstrap, locking, cache
- `unshuffle.bridge`: workflow/search/discovery facades for GUI and CLI seams
- `unshuffle.core`: shared leaf utilities such as models, tags, config, tokenizer
"""

from __future__ import annotations

from .core.constants import APP_NAME, APP_VERSION, Version

__all__ = [
    "APP_NAME",
    "APP_VERSION",
    "Version",
]
