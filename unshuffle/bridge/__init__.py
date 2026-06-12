"""Curated facades between app surfaces and layered backend packages.

Start here for:
- full workflow operations: `WorkflowBridge`, `create_workflow_bridge`
- staged search and diagnostics: `SearchBridge`
- persistence-oriented UI helpers: `PersistenceBridge`
- discovery workflows: `DiscoveryBridge`
"""

from .discovery_bridge import DiscoveryBridge
from .persistence_bridge import PersistenceBridge
from .search_bridge import SearchBridge
from .workflow_bridge import WorkflowBridge, create_workflow_bridge

__all__ = [
    "DiscoveryBridge",
    "PersistenceBridge",
    "SearchBridge",
    "WorkflowBridge",
    "create_workflow_bridge",
]
