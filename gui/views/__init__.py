"""GUI views that render model-backed data.

Start here for:
- staging/workbench table interactions: `StagingTableView`
- library tree presentation and actions: `LibraryTreeView`
- docked read-only discovery panel: `DockView`
"""

from .library_tree import LibraryTreeView
from .staging_table import StagingTableView
from .dock_view import DockView

__all__ = ['LibraryTreeView', 'StagingTableView', 'DockView']
