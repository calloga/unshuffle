"""Shared GUI support helpers, constants, and style primitives.

Start here for:
- UI constants and column/search mappings: `constants`
- stylesheet and palette definitions: `styles`
- model/session rewrite helpers: `state`, `session`
- executed/pending history lookups: `history`
"""

from . import constants, history, session, state, styles

__all__ = ["constants", "history", "session", "state", "styles"]
