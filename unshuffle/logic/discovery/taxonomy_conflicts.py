import json
from collections import defaultdict
from pathlib import Path
from typing import Any


def _iter_taxonomy_documents(taxonomy_dir: Path):
    for tax_file in sorted(taxonomy_dir.glob("*.json")):
        try:
            with open(tax_file, "r", encoding="utf-8") as file_handle:
                data = json.load(file_handle)
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            yield tax_file, data


def _collect_alias_occurrences(taxonomy_dir: Path) -> dict[str, list[dict[str, str]]]:
    occurrences: dict[str, list[dict[str, str]]] = defaultdict(list)
    for tax_file, data in _iter_taxonomy_documents(taxonomy_dir):
        category = str(data.get("category") or tax_file.stem)
        taxonomy = data.get("taxonomy")
        if not isinstance(taxonomy, dict):
            continue
        for bucket, values in taxonomy.items():
            if not isinstance(values, list):
                continue
            for alias in values:
                if not isinstance(alias, str):
                    continue
                occurrences[alias.lower()].append(
                    {
                        "alias": alias,
                        "category": category,
                        "bucket": str(bucket),
                        "taxonomy_file": tax_file.name,
                    }
                )
    return occurrences


def find_cross_taxonomy_conflicts(taxonomy_dir: Path) -> list[dict[str, Any]]:
    conflicts: list[dict[str, Any]] = []
    for alias, hits in sorted(_collect_alias_occurrences(taxonomy_dir).items()):
        categories = sorted({hit["category"] for hit in hits})
        if len(categories) <= 1:
            continue
        conflicts.append(
            {
                "alias": alias,
                "categories": categories,
                "occurrences": sorted(hits, key=lambda item: (item["category"], item["bucket"], item["taxonomy_file"])),
            }
        )
    return conflicts
