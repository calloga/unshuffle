from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ...core.constants import NO_SIGNAL_THRESHOLD, refresh_alias_structures
from ...core.models import LibNode, NodeType
from .service import classify_node, get_scoring_engine, reset_scoring_engine

if TYPE_CHECKING:
    from .scoring import ScoringEngine


@dataclass(frozen=True)
class TokenContribution:
    token: str
    category: str | None
    weight: float | None
    specificity: float | None
    contribution: float | None
    status: str
    component: str | None = None


@dataclass(frozen=True)
class FileDiagnosis:
    target_file: Path
    display_path: Path
    tokens: tuple[str, ...]
    token_contributions: tuple[TokenContribution, ...]
    category_scores: dict[str, float]
    best_category: str
    floor_threshold: float
    below_floor: bool


def _resolve_display_path(target_file: Path, scan_root: Path | None) -> Path:
    if scan_root is None:
        return target_file
    try:
        return target_file.relative_to(scan_root)
    except ValueError:
        return target_file


def _build_engine(db=None) -> "ScoringEngine":
    if db is not None:
        refresh_alias_structures(db)
        reset_scoring_engine()
    return get_scoring_engine()


def diagnose_file(target_file: Path, scan_root: Path | None = None, db=None) -> FileDiagnosis:
    _build_engine(db=db)
    display_path = _resolve_display_path(target_file, scan_root)
    node = LibNode(
        path=display_path,
        name=display_path.name,
        node_type=NodeType.FILE,
        extension=display_path.suffix,
    )
    best_category, _confidence, evidence = classify_node(node)
    trace = (evidence or {}).get("trace") or {}
    component_map = trace.get("components") or {}

    category_scores = dict(sorted(((str(k), float(v)) for k, v in ((evidence or {}).get("raw") or {}).items()), key=lambda item: item[1], reverse=True))
    token_contributions: list[TokenContribution] = []
    all_tokens = []

    for component_name in ("filename", "parent", "pack"):
        component = component_map.get(component_name) or {}
        all_tokens.extend(component.get("tokens") or [])
        for entry in component.get("token_trace") or []:
            status = str(entry.get("status", ""))
            token = str(entry.get("token", ""))
            if status == "matched":
                for match in entry.get("matches") or []:
                    token_contributions.append(
                        TokenContribution(
                            token=token,
                            category=str(match.get("category")),
                            weight=float(match.get("weight")) if match.get("weight") is not None else None,
                            specificity=float(match.get("specificity")) if match.get("specificity") is not None else None,
                            contribution=float(match.get("contribution")) if match.get("contribution") is not None else None,
                            status=status,
                            component=component_name,
                        )
                    )
            else:
                token_contributions.append(
                    TokenContribution(
                        token=token,
                        category=None,
                        weight=None,
                        specificity=float(entry.get("specificity")) if entry.get("specificity") is not None else None,
                        contribution=None,
                        status=status,
                        component=component_name,
                    )
                )

    if not all_tokens:
        all_tokens = [display_path.name]

    below_floor = best_category == "Uncategorized" and bool(category_scores)
    if not category_scores and best_category == "Uncategorized":
        below_floor = True

    return FileDiagnosis(
        target_file=target_file,
        display_path=display_path,
        tokens=tuple(all_tokens),
        token_contributions=tuple(token_contributions),
        category_scores=category_scores,
        best_category=best_category,
        floor_threshold=NO_SIGNAL_THRESHOLD,
        below_floor=below_floor,
    )


def format_file_diagnosis(diagnosis: FileDiagnosis) -> str:
    lines = [
        "--- Diagnosing File ---",
        f"Path: {diagnosis.display_path}",
        f"Tokens: {set(diagnosis.tokens)}",
        "",
        "Token breakdown:",
    ]

    for entry in diagnosis.token_contributions:
        if entry.status == "noise":
            lines.append(f"  {entry.token:<15} | [NOISE]")
        elif entry.status == "not_found":
            lines.append(f"  {entry.token:<15} | [{entry.component or 'trace'}: NOT FOUND]")
        else:
            lines.append(
                f"  {entry.token:<15} | Src: {entry.component or '-':<8} | Cat: {entry.category:<20} | "
                f"W: {entry.weight:.4f} | Spec: {entry.specificity:.4f} | "
                f"Contrib: {entry.contribution:.4f}"
            )

    lines.append("")
    lines.append("Final Scores:")
    for category, score in diagnosis.category_scores.items():
        lines.append(f"  > {category:<25} | {score:.4f}")

    if diagnosis.category_scores and diagnosis.below_floor:
        top_category, top_score = next(iter(diagnosis.category_scores.items()))
        lines.append("")
        lines.append(
            f"[!] Raw best score ({top_category}: {top_score:.4f}) "
            f"is below {diagnosis.floor_threshold:.2f} floor."
        )

    lines.append("")
    lines.append(f"Result: {diagnosis.best_category}")
    return "\n".join(lines)
