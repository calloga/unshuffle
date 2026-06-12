from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from unshuffle.core.constants import CATEGORIES


@dataclass(frozen=True)
class AdditionImportPlan:
    invalid: list[tuple[str, str]]
    filtered: dict[str, list[str]]
    skipped: list[tuple[str, str]]
    conflicts: list[tuple[str, str, str]]

    @property
    def count(self) -> int:
        return sum(len(items) for items in self.filtered.values())


def normalize_aliases(aliases: list[str]) -> list[str]:
    normalized = []
    seen = set()
    for alias in aliases:
        alias_norm = (alias or "").strip().lower()
        if alias_norm and alias_norm not in seen:
            seen.add(alias_norm)
            normalized.append(alias_norm)
    return normalized


def plan_import(controller, rows: list[tuple[str, str]]) -> AdditionImportPlan:
    invalid = [(alias, category) for alias, category in rows if category not in CATEGORIES]
    if invalid:
        return AdditionImportPlan(invalid=invalid, filtered={}, skipped=[], conflicts=[])

    grouped: dict[str, list[str]] = defaultdict(list)
    for alias, category in rows:
        grouped[category].append(alias)

    alias_map = controller._alias_map()
    filtered: dict[str, list[str]] = defaultdict(list)
    skipped: list[tuple[str, str]] = []
    conflicts: list[tuple[str, str, str]] = []
    for category, aliases in grouped.items():
        for alias in aliases:
            token = controller._candidate_token(alias)
            selected_matches = controller._aliases_containing_token(alias_map, token, category) if token else []
            if selected_matches or alias in alias_map:
                skipped.append((alias, category))
                continue
            if token:
                conflicts.extend(
                    (alias, hit_alias, hit_category)
                    for hit_alias, hit_category, _source in controller._aliases_containing_token(alias_map, token, None)
                    if hit_category != category
                )
            filtered[category].append(alias)
    return AdditionImportPlan(invalid=[], filtered=filtered, skipped=skipped, conflicts=conflicts)


def corrections_for_display(controller, rows: list[tuple[str, str, float]]) -> list[tuple[str, str, float]]:
    if not rows:
        return rows
    alias_map = controller._alias_map()
    weighted_tokens = controller.discovery_bridge.get_all_weighted_tokens(alias_map)
    return [(token, category, offset) for token, category, offset in rows if token in weighted_tokens]

