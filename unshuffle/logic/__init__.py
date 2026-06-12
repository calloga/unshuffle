"""Logic-layer feature index.

Start here for:
- structural scanning and graph analysis: `run_analysis`
- classification and diagnostics: `classify_node`, `detect_audio_type`, `diagnose_file`
- alias discovery tooling: `run_discovery`, `run_import`
- plan construction: `run_plan`
- execution/file-transfer behavior: `ExecutionMixin`
"""

from .analysis import AnalysisContext, GlobalFrequencyAnalyzer, TokenRegistry, build_node_graph, run_analysis
from .classification import (
    FileDiagnosis,
    TokenContribution,
    classify_node,
    compute_component_score,
    detect_audio_type,
    diagnose_file,
    format_file_diagnosis,
    get_scoring_engine,
    get_subcategory,
    is_category_alias,
    reset_scoring_engine,
    tokenize,
)
from .discovery import load_alias_table, run_discovery, run_import, save_alias_table, show_token_weights
from .execution import ExecutionMixin
from .planning import run_plan

__all__ = [
    "AnalysisContext",
    "ExecutionMixin",
    "FileDiagnosis",
    "GlobalFrequencyAnalyzer",
    "TokenContribution",
    "TokenRegistry",
    "build_node_graph",
    "classify_node",
    "compute_component_score",
    "detect_audio_type",
    "diagnose_file",
    "format_file_diagnosis",
    "get_scoring_engine",
    "get_subcategory",
    "is_category_alias",
    "load_alias_table",
    "reset_scoring_engine",
    "run_analysis",
    "run_discovery",
    "run_import",
    "run_plan",
    "save_alias_table",
    "show_token_weights",
    "tokenize",
]
