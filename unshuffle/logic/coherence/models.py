from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


COHERENCE_STATUS_STABLE = "stable"
COHERENCE_STATUS_UNDERREPRESENTED = "underrepresented"
COHERENCE_STATUS_CLUSTERED = "clustered"
COHERENCE_STATUS_LOW = "low_coherence"
COHERENCE_STATUS_MISCATEGORIZATION = "possible_miscategorization"
COHERENCE_STATUS_INVALID = "invalid_vector"
COHERENCE_STATUS_SKIPPED = "skipped"

REFINEMENT_PENDING = "pending"
REFINEMENT_ACCEPTED = "accepted"
REFINEMENT_IGNORED = "ignored"
REFINEMENT_AUTO_STAGED = "auto_staged"

ANCHOR_CANDIDATE = "candidate"
ANCHOR_VERIFIED = "verified"
ANCHOR_IGNORED = "ignored"


@dataclass(frozen=True)
class CoherenceRecord:
    record_id: str
    category: str
    subcategory: str
    vector: list[float]
    classification_confidence: float | None = None
    audio_type: str = ""
    source_path: str = ""
    pack: str = ""


@dataclass(frozen=True)
class CoherenceResult:
    record_id: str
    category: str
    subcategory: str
    coherence_status: str
    coherence_score: float
    cluster_id: str | None = None
    is_outlier: bool = False
    review_reason: str | None = None
    suggested_alternate_category: str | None = None
    suggested_alternate_subcategory: str | None = None
    nearest_neighbor_summary: dict[str, Any] | None = None
    anchor_fit_status: str | None = None


@dataclass(frozen=True)
class RefinementCandidate:
    candidate_id: str
    record_id: str
    current_category: str
    current_subcategory: str
    suggested_category: str
    suggested_subcategory: str
    evidence: str
    coherence_status: str = COHERENCE_STATUS_MISCATEGORIZATION
    confidence_score: float = 0.0
    state: str = REFINEMENT_PENDING
    current_audio_type: str = ""
    suggested_audio_type: str = ""


@dataclass(frozen=True)
class AnchorProfile:
    anchor_id: str
    category: str
    subcategory: str
    cluster_id: str
    feature_space_version: str
    extractor_version: str
    vector_schema: tuple[str, ...]
    medoid_vector: list[float]
    cluster_centroid: list[float]
    cluster_std: list[float]
    coherence_radius: float
    n_reference_items: int
    state: str = ANCHOR_CANDIDATE
    profile_payload: dict[str, Any] = field(default_factory=dict)
    audio_type: str = ""


@dataclass(frozen=True)
class CoherenceRunSummary:
    total_records: int
    eligible_records: int
    valid_vector_records: int
    coverage: float
    ran: bool
    reason: str = ""
    result_count: int = 0
    pending_candidate_count: int = 0
    auto_staged_candidate_count: int = 0
    anchor_candidate_count: int = 0
