"""Main-window package surface.

Start here for:
- main app window and bootstrap: `ModernApp`, `main`
- menu/session/build helpers: `actions`
- filter/view wrapper helpers: `filters_view`
"""

from .launcher import main
from .window import ModernApp

__all__ = ["ModernApp", "main"]
