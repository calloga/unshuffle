import json
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


class NodeType(Enum):
    ROOT = auto()
    FILE = auto()
    LEAF = auto()
    CONTAINER = auto()


@dataclass(slots=True)
class LibNode:
    path: Path
    name: str
    node_type: NodeType
    children: List["LibNode"] = field(default_factory=list)
    extension: Optional[str] = None
    hash: Optional[str] = None
    pack_candidate_weight: float = 0.0
    path_weighted_tokens: List[str] = field(default_factory=list)
    name_weighted_tokens: List[str] = field(default_factory=list)
    unweighted_tokens: List[str] = field(default_factory=list)
    is_pure_container: bool = False
    is_duplicate_container: bool = False
    is_child_of_duplicate: bool = False
    duplicate_child_bonus: float = 0.0
    is_large_container: bool = False
    is_standard_container: bool = False
    is_preserved: bool = False
    preserved_root: Optional[Path] = None
    weight_evidence: Dict[str, float] = field(default_factory=dict)
    parent: Optional["LibNode"] = None


@dataclass
class PlanRecord:
    source_path: Path
    pack: str
    category: str
    audio_type: str
    confidence: str
    subcategory: Optional[str] = None
    evidence: Dict = field(default_factory=dict)
    is_preserved: bool = False
    preserved_root: Optional[Path] = None
    is_manual: bool = False
    duration: float = 0.0
    pack_candidates: List[Tuple[str, float]] = field(default_factory=list)
    hash: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    feature_vector: Optional[bytes] = None
    acoustic_vector: Optional[bytes] = None
    feature_space_version: Optional[str] = None
    feature_schema_json: Optional[str] = None
    analysis_status: Optional[str] = None
    analysis_tags_json: Optional[str] = None
    staging_row_id: Optional[int] = None

    def __post_init__(self) -> None:
        self.pack = str(self.pack)
        self.category = str(self.category)
        self.audio_type = str(self.audio_type)
        if self.subcategory:
            self.subcategory = str(self.subcategory)
        if self.tags:
            self.tags = [str(tag) for tag in self.tags]
        if self.confidence is not None:
            self.confidence = str(self.confidence)
        if not isinstance(self.source_path, Path):
            self.source_path = Path(self.source_path)
        if self.feature_vector is None and self.acoustic_vector is not None:
            self.feature_vector = self.acoustic_vector
        elif self.acoustic_vector is None and self.feature_vector is not None:
            self.acoustic_vector = self.feature_vector


def parse_pack_candidates(raw_value: Any) -> List[Tuple[str, float]]:
    try:
        data = json.loads(raw_value) if raw_value else []
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []

    parsed: List[Tuple[str, float]] = []
    for item in data:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            continue
        name, score = item
        try:
            parsed.append((str(name), float(score)))
        except (TypeError, ValueError):
            continue
    return parsed


def plan_record_from_staging_row(row: Dict[str, Any], parse_tags_fn) -> PlanRecord:
    tags_raw = row.get("tags", "[]")
    try:
        tags = json.loads(tags_raw) if tags_raw else []
    except (json.JSONDecodeError, TypeError):
        tags = parse_tags_fn(str(tags_raw))
    evidence_raw = row.get("evidence_json")
    try:
        evidence = json.loads(evidence_raw) if evidence_raw else {"reconstructed": True}
        if not isinstance(evidence, dict):
            evidence = {"reconstructed": True}
    except (json.JSONDecodeError, TypeError):
        evidence = {"reconstructed": True}

    return PlanRecord(
        source_path=Path(row["source_path"]),
        pack=row["pack"],
        category=row["category"],
        subcategory=row.get("subcategory"),
        audio_type=row.get("audio_type", "Oneshots"),
        confidence=str(row.get("confidence", "0.0")),
        evidence=evidence,
        duration=row.get("duration", 0.0),
        hash=row.get("hash"),
        tags=tags,
        pack_candidates=parse_pack_candidates(row.get("pack_candidates", "")),
        feature_vector=row.get("feature_vector", row.get("acoustic_vector")),
        feature_space_version=row.get("feature_space_version"),
        feature_schema_json=row.get("feature_schema_json"),
        analysis_status=row.get("analysis_status"),
        analysis_tags_json=row.get("analysis_tags_json"),
        is_preserved=bool(row.get("is_preserved", False)),
        preserved_root=Path(row["preserved_root"]) if row.get("preserved_root") else None,
        staging_row_id=int(row["row_id"]) if row.get("row_id") is not None else None,
    )


def plan_records_from_staging_rows(rows: Iterable[Dict[str, Any]], parse_tags_fn) -> List[PlanRecord]:
    return [plan_record_from_staging_row(row, parse_tags_fn) for row in rows]


def stable_record_identity(record: PlanRecord) -> Tuple[str, str]:
    path_str = str(getattr(record, "source_path", "")).replace("\\", "/").strip().lower()
    hash_str = str(getattr(record, "hash", "") or "").strip().lower()
    return (path_str, hash_str)


def plan_record_sort_key(record: PlanRecord, mode: str = "filename") -> Any:
    filename = record.source_path.name.lower()
    mode = (mode or "filename").strip().lower()

    if mode == "pack":
        return (str(getattr(record, "pack", "")).lower(), filename)
    if mode == "category":
        return (str(getattr(record, "category", "")).lower(), filename)
    if mode == "tags":
        tags = getattr(record, "tags", "")
        if isinstance(tags, list):
            tags = " ".join(str(tag) for tag in tags)
        return (str(tags).lower(), filename)
    if mode == "path":
        return str(record.source_path).replace("\\", "/").lower()
    if mode == "confidence":
        try:
            return (-float(getattr(record, "confidence", 0.0)), filename)
        except (TypeError, ValueError):
            return (0.0, filename)
    return filename
