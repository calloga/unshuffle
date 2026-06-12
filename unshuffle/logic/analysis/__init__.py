"""Structural scanning and graph analysis.

Start here for:
- package scan entrypoint: `run_analysis`
- discovery corpus extraction: `build_discovery_data`
- graph/context types: `AnalysisContext`, `TokenRegistry`
- global scan-wide frequency boosts: `GlobalFrequencyAnalyzer`
"""

from .frequency import GlobalFrequencyAnalyzer
from .service import AnalysisContext, TokenRegistry, build_discovery_data, build_node_graph, run_analysis

__all__ = [
    "AnalysisContext",
    "GlobalFrequencyAnalyzer",
    "TokenRegistry",
    "build_discovery_data",
    "build_node_graph",
    "run_analysis",
]
