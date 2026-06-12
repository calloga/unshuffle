"""GUI control layer and workflow orchestration.

Start here for:
- app/workflow orchestration: `WorkflowController`, `WorkerManager`
- search/filter coordination: `SearchController`, `SearchEngine`, `FilterController`
- staging/draft management: `DataManager`, `DraftingController`, `ReorgManager`
- view/audio/settings glue: `ViewController`, `AudioController`, `AcousticController`, `SettingsController`
- scan-flow helpers: `main/window/scan_flow`, `filter_query`
"""

from .data_manager import DataManager
from .worker_manager import WorkerManager
from .workers import ScanWorker, CommitWorker, UndoWorker, SearchWorker, SessionLoadWorker, SimilarityWorker, TaggingWorker, TreeRebuildWorker, CoherenceWorker
from .search_engine import SearchEngine
from .filter_query import (
    active_categories_for_search,
    active_confidence_range_for_search,
    active_saved_filter_queries_for_search,
    active_source_filters_for_search,
    confidence_filter_query,
    query_contains_token,
    remove_confidence_filters,
    remove_filter_query,
    source_filter_query,
    tree_highlight_text,
    tree_skip_fields,
)
from .reorg_manager import ReorgManager
from .audio_controller import AudioController
from .settings_controller import SettingsController, create_app_settings
from .search_controller import SearchController
from .workflow_controller import WorkflowController
from .drafting_controller import DraftingController
from .acoustic_controller import AcousticController
from .filter_controller import FilterController
from .view_controller import ViewController
from .system_controller import SystemController
from .tagging_controller import TaggingController
from .coherence_controller import CoherenceController
from .tree_organization_controller import TreeOrganizationController
from . import main_window_scan_flow

__all__ = [
    "AcousticController",
    "AudioController",
    "CommitWorker",
    "CoherenceController",
    "CoherenceWorker",
    "DataManager",
    "DraftingController",
    "FilterController",
    "ReorgManager",
    "ScanWorker",
    "SearchController",
    "SearchEngine",
    "SearchWorker",
    "SessionLoadWorker",
    "SimilarityWorker",
    "TaggingWorker",
    "SettingsController",
    "create_app_settings",
    "TreeRebuildWorker",
    "TreeOrganizationController",
    "UndoWorker",
    "ViewController",
    "SystemController",
    "TaggingController",
    "WorkerManager",
    "WorkflowController",
    "active_categories_for_search",
    "active_confidence_range_for_search",
    "active_saved_filter_queries_for_search",
    "active_source_filters_for_search",
    "confidence_filter_query",
    "main_window_scan_flow",
    "query_contains_token",
    "remove_confidence_filters",
    "remove_filter_query",
    "source_filter_query",
    "tree_highlight_text",
    "tree_skip_fields",
]
