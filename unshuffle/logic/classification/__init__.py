"""Classification, scoring, audio-type detection, and file diagnosis.

Start here for:
- main categorization entrypoint: `classify_node`
- filename/component scoring: `compute_component_score`
- loop vs oneshot detection: `detect_audio_type`
- explainability/inspection: `diagnose_file`, `format_file_diagnosis`
"""

from .diagnostics import FileDiagnosis, TokenContribution, diagnose_file, format_file_diagnosis
from .service import (
    apply_suppression,
    classify_node,
    compute_component_score,
    detect_audio_type,
    get_scoring_engine,
    get_subcategory,
    is_category_alias,
    reset_scoring_engine,
    tokenize,
    weighted_adjustment_tokens,
)

__all__ = [
    "FileDiagnosis",
    "TokenContribution",
    "apply_suppression",
    "classify_node",
    "compute_component_score",
    "detect_audio_type",
    "diagnose_file",
    "format_file_diagnosis",
    "get_scoring_engine",
    "get_subcategory",
    "is_category_alias",
    "reset_scoring_engine",
    "tokenize",
    "weighted_adjustment_tokens",
]
