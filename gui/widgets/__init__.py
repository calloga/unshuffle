"""Reusable GUI widgets and composite UI building blocks.

Start here for:
- primary workbench surface: `LibraryTab`
- build workspace: `BuildPage`
- app chrome/support widgets: `ModernFooter`, `ModernMenuBar`, `VibeAnchorBar`
- controls: `TypeToggle`, `ModernKnob`, `ModernRangeSlider`, `SidebarCarousel`
- buttons/sidebar primitives: `AnimatedIconButton`, `SidebarIconButton`, `LibrarySidebar`
"""

from .buttons import AnimatedIconButton, SidebarIconButton
from .vibe_anchor_bar import VibeAnchorBar
from .footer import ModernFooter
from .menu_bar import ModernMenuBar
from .sliders import ModernKnob, ModernRangeSlider
from .toggles import TypeToggle
from .sections import CollapsibleSection
from .carousels import SidebarCarousel
from .sidebar import LibrarySidebar, SignalFloorControl, LibrarySidebarItem, SavedFilterItem
from .labels import section_label, ElidingLabel
from .delegates import ComboDelegate, TagPillDelegate
from .library_tab import LibraryTab
from .build_page import BuildPage
from .system_page import SystemPage
from .coherence_analyzer import CoherenceAnalyzerPage
from .history_page import HistoryPage

__all__ = [
    'AnimatedIconButton',
    'VibeAnchorBar',
    'ModernFooter',
    'ModernMenuBar',
    'SidebarIconButton',
    'ModernKnob',
    'ModernRangeSlider',
    'TypeToggle',
    'CollapsibleSection',
    'SidebarCarousel',
    'LibrarySidebar',
    'SignalFloorControl',
    'LibrarySidebarItem',
    'SavedFilterItem',
    'section_label',
    'ElidingLabel',
    'ComboDelegate',
    'TagPillDelegate',
    'LibraryTab',
    'BuildPage',
    'SystemPage',
    'CoherenceAnalyzerPage',
    'HistoryPage',
]
