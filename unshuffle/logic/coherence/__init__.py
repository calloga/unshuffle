"""Post-classification acoustic coherence audit.

This package is intentionally advisory: it can report low-coherence rows,
candidate refinements, and anchor candidates, but it does not commit file
operations or rewrite classifications without an explicit user action.
"""

from .coherence_engine import CoherenceEngine
from .models import (
    AnchorProfile,
    CoherenceRecord,
    CoherenceResult,
    CoherenceRunSummary,
    RefinementCandidate,
)
from .service import run_coherence_audit

__all__ = [
    "AnchorProfile",
    "CoherenceEngine",
    "CoherenceRecord",
    "CoherenceResult",
    "CoherenceRunSummary",
    "RefinementCandidate",
    "run_coherence_audit",
]
