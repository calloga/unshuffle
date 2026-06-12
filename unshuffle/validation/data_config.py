from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from unshuffle.core.assets import asset_path
from unshuffle.core.config import DEFAULT_CONFIG
from unshuffle.core.features import CURRENT_EXTRACTOR_VERSION, CURRENT_VECTOR_SCHEMA, feature_blob_from_vector
from unshuffle.logic.coherence.anchor_profiles import validate_anchor_payload


@dataclass(frozen=True)
class ValidationIssue:
    path: Path
    message: str


_LIST_KEYS = {
    "LOOP_INDICATORS",
    "WEAK_LOOP_INDICATORS",
    "ONESHOT_INDICATORS",
    "ONESHOT_HINT_TOKENS",
    "NOISE_WORDS",
    "HIDDEN_SYSTEM_FILES",
    "PERCUSSIVE_CATEGORIES",
}
_DICT_KEYS = {
    "ALIAS_TABLE",
    "CATEGORY_SUPPRESSION_RULES",
    "CATEGORY_SUPPRESS_MAP",
    "SUB_TAXONOMY_MAP",
}


def validate_release_data(root: Path | None = None) -> list[ValidationIssue]:
    repo_root = Path(root) if root is not None else asset_path("data").parent
    data_dir = repo_root / "data"
    issues: list[ValidationIssue] = []

    config = _load_json(data_dir / "config.json", issues)
    taxonomy_categories = _validate_taxonomy_dir(data_dir / "taxonomy", issues)
    if isinstance(config, dict):
        _validate_config(data_dir / "config.json", config, taxonomy_categories, issues)
    _validate_system_anchors(data_dir / "anchors" / "system_anchors.json", taxonomy_categories, issues)

    metadata_dir = data_dir / "metadata"
    if not metadata_dir.exists():
        issues.append(ValidationIssue(metadata_dir, "metadata directory is missing"))
    else:
        for metadata_file in sorted(metadata_dir.glob("*.json")):
            metadata = _load_json(metadata_file, issues)
            if metadata_file.name == "genre_relationships.json" and isinstance(metadata, dict):
                _validate_genre_relationships(metadata_file, metadata, issues)

    return issues


def _load_json(path: Path, issues: list[ValidationIssue]) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        issues.append(ValidationIssue(path, "file is missing"))
    except json.JSONDecodeError as exc:
        issues.append(ValidationIssue(path, f"invalid JSON: {exc.msg} at line {exc.lineno}, column {exc.colno}"))
    except OSError as exc:
        issues.append(ValidationIssue(path, f"could not read file: {exc}"))
    return None


def _validate_config(
    path: Path,
    config: dict[str, Any],
    taxonomy_categories: set[str],
    issues: list[ValidationIssue],
) -> None:
    for key, default_value in DEFAULT_CONFIG.items():
        if key not in config:
            continue
        value = config[key]
        if key in _LIST_KEYS and not isinstance(value, list):
            issues.append(ValidationIssue(path, f"{key} must be a list"))
        if key in _DICT_KEYS and not isinstance(value, dict):
            issues.append(ValidationIssue(path, f"{key} must be an object"))
        if key == "LOG_LEVEL" and not isinstance(value, str):
            issues.append(ValidationIssue(path, "LOG_LEVEL must be a string"))

    for key in _LIST_KEYS:
        value = config.get(key)
        if isinstance(value, list):
            _validate_string_list(path, key, value, issues)

    known_categories = taxonomy_categories | {"Uncategorized", "Non-Audio Assets"}
    for category, suppressed in dict(config.get("CATEGORY_SUPPRESSION_RULES", {})).items():
        if str(category) not in known_categories:
            issues.append(ValidationIssue(path, f"CATEGORY_SUPPRESSION_RULES uses unknown category: {category}"))
        if not isinstance(suppressed, list):
            issues.append(ValidationIssue(path, f"CATEGORY_SUPPRESSION_RULES[{category}] must be a list"))
            continue
        for suppressed_category in suppressed:
            if str(suppressed_category) not in known_categories:
                issues.append(
                    ValidationIssue(path, f"CATEGORY_SUPPRESSION_RULES[{category}] suppresses unknown category: {suppressed_category}")
                )

    percussive = config.get("PERCUSSIVE_CATEGORIES", [])
    if isinstance(percussive, list):
        for category in percussive:
            if str(category) not in known_categories:
                issues.append(ValidationIssue(path, f"PERCUSSIVE_CATEGORIES contains unknown category: {category}"))


def _validate_taxonomy_dir(path: Path, issues: list[ValidationIssue]) -> set[str]:
    categories: set[str] = set()
    if not path.exists():
        issues.append(ValidationIssue(path, "taxonomy directory is missing"))
        return categories

    for taxonomy_file in sorted(path.glob("*.json")):
        data = _load_json(taxonomy_file, issues)
        if not isinstance(data, dict):
            issues.append(ValidationIssue(taxonomy_file, "taxonomy file must contain an object"))
            continue
        category = data.get("category")
        taxonomy = data.get("taxonomy")
        if not isinstance(category, str) or not category.strip():
            issues.append(ValidationIssue(taxonomy_file, "category must be a non-empty string"))
            continue
        if category in categories:
            issues.append(ValidationIssue(taxonomy_file, f"duplicate taxonomy category: {category}"))
        categories.add(category)
        if "default_sub" in data and not isinstance(data["default_sub"], str):
            issues.append(ValidationIssue(taxonomy_file, "default_sub must be a string when present"))
        if not isinstance(taxonomy, dict) or not taxonomy:
            issues.append(ValidationIssue(taxonomy_file, "taxonomy must be a non-empty object"))
            continue
        _validate_taxonomy_node(taxonomy_file, taxonomy, issues, ())

    return categories


def _validate_taxonomy_node(
    path: Path,
    node: dict[str, Any],
    issues: list[ValidationIssue],
    trail: tuple[str, ...],
) -> None:
    for bucket, value in node.items():
        if not isinstance(bucket, str) or not bucket.strip():
            issues.append(ValidationIssue(path, f"bucket {'/'.join(trail) or '<root>'} has a non-string or empty name"))
            continue
        current_trail = (*trail, bucket)
        if isinstance(value, list):
            _validate_string_list(path, "/".join(current_trail), value, issues)
        elif isinstance(value, dict):
            if not value:
                issues.append(ValidationIssue(path, f"bucket {'/'.join(current_trail)} is empty"))
            _validate_taxonomy_node(path, value, issues, current_trail)
        else:
            issues.append(ValidationIssue(path, f"bucket {'/'.join(current_trail)} must contain a list or object"))


def _validate_genre_relationships(path: Path, data: dict[str, Any], issues: list[ValidationIssue]) -> None:
    music = data.get("music")
    if not isinstance(music, dict):
        issues.append(ValidationIssue(path, "music must be an object"))
        return
    families = music.get("families")
    if not isinstance(families, dict) or not families:
        issues.append(ValidationIssue(path, "music.families must be a non-empty object"))
        return
    _validate_metadata_tree(path, families, issues, ("music", "families"))


def _validate_system_anchors(
    path: Path,
    taxonomy_categories: set[str],
    issues: list[ValidationIssue],
) -> None:
    payloads = _load_json(path, issues)
    if payloads is None:
        return
    if not isinstance(payloads, list):
        issues.append(ValidationIssue(path, "system anchors file must contain a list"))
        return
    known_categories = taxonomy_categories | {"Uncategorized", "Non-Audio Assets"}
    seen_ids: set[str] = set()
    for index, payload in enumerate(payloads):
        label = f"anchor[{index}]"
        if not isinstance(payload, dict):
            issues.append(ValidationIssue(path, f"{label} must be an object"))
            continue
        anchor_id = str(payload.get("anchor_id") or "").strip()
        if not anchor_id:
            issues.append(ValidationIssue(path, f"{label} anchor_id must be a non-empty string"))
        elif anchor_id in seen_ids:
            issues.append(ValidationIssue(path, f"{label} duplicate anchor_id: {anchor_id}"))
        seen_ids.add(anchor_id)

        ok, reason = validate_anchor_payload(payload, known_categories)
        if not ok:
            issues.append(ValidationIssue(path, f"{label} invalid anchor payload: {reason}"))

        features = payload.get("features") or {}
        if not isinstance(features, dict):
            continue
        if features.get("extractor_version") != CURRENT_EXTRACTOR_VERSION:
            issues.append(ValidationIssue(path, f"{label} extractor_version must be {CURRENT_EXTRACTOR_VERSION}"))
        if tuple(features.get("vector_schema") or ()) != CURRENT_VECTOR_SCHEMA:
            issues.append(ValidationIssue(path, f"{label} vector_schema must match current feature schema"))
        for vector_name in ("medoid_vector", "cluster_centroid", "cluster_std"):
            vector = features.get(vector_name)
            if not isinstance(vector, (list, tuple)) or feature_blob_from_vector(vector) is None:
                issues.append(ValidationIssue(path, f"{label} {vector_name} must be a valid current-schema vector"))


def _validate_metadata_tree(path: Path, node: Any, issues: list[ValidationIssue], trail: tuple[str, ...]) -> None:
    if isinstance(node, dict):
        if not node:
            issues.append(ValidationIssue(path, f"{'.'.join(trail)} is empty"))
        for key, value in node.items():
            if not isinstance(key, str) or not key.strip():
                issues.append(ValidationIssue(path, f"{'.'.join(trail)} has a non-string or empty key"))
                continue
            _validate_metadata_tree(path, value, issues, (*trail, key))
    elif isinstance(node, list):
        _validate_string_list(path, ".".join(trail), node, issues)
    elif not isinstance(node, (str, int, float, bool)) and node is not None:
        issues.append(ValidationIssue(path, f"{'.'.join(trail)} has unsupported value type {type(node).__name__}"))


def _validate_string_list(path: Path, label: str, values: list[Any], issues: list[ValidationIssue]) -> None:
    seen: set[str] = set()
    for index, value in enumerate(values):
        if not isinstance(value, str) or not value.strip():
            issues.append(ValidationIssue(path, f"{label}[{index}] must be a non-empty string"))
            continue
        folded = value.casefold()
        if folded in seen:
            issues.append(ValidationIssue(path, f"{label} contains duplicate value: {value}"))
        seen.add(folded)


def main() -> int:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    issues = validate_release_data(root)
    if not issues:
        print("Data/config validation passed.")
        return 0
    for issue in issues:
        print(f"{issue.path}: {issue.message}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
