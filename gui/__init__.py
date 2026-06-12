"""GUI package surface for Unshuffle.

Navigation:
- `gui.__main__`: package launcher for `python -m gui`
- `gui.main`: top-level `ModernApp` window package and app assembly
- `gui.core`: controllers, workers, workflow orchestration, search/query logic
- `gui.models`: table/tree/proxy models
- `gui.views`: table/tree/dock widgets that present model data
- `gui.widgets`: reusable UI components such as the library tab, footer, sliders
- `gui.dialogs`: modal dialogs and help/build flows
- `gui.utils`: shared constants, styles, state/session/history helpers
"""

from .main import ModernApp

__all__ = ["ModernApp"]
