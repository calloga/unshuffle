from __future__ import annotations

from dataclasses import dataclass

from unshuffle.logic.tree_organization import TreeOrganizationProfile, TreeOrganizationResolver
from unshuffle.logic.tree_organization.filter_evaluator import parse_query_groups, split_field_term


_FIELD_PREFIXES = {
    "audio_type": "type",
    "category": "category",
    "subcategory": "subcategory",
}
_CUSTOM_PLACEMENTS = {
    "cat": "category",
    "category": "category",
    "sub": "subcategory",
    "subcat": "subcategory",
    "subcategory": "subcategory",
}


@dataclass(frozen=True)
class CustomTreeFilterOption:
    node_id: str
    label: str
    query: str
    placement: str
    audio_type: str = ""
    parent_category: str = ""
    count: int | None = None


@dataclass(frozen=True)
class EffectiveTaxonomyContext:
    profile_id: str
    projection_signature: str
    options: tuple[CustomTreeFilterOption, ...]

    def options_for(self, placement: str) -> tuple[CustomTreeFilterOption, ...]:
        return tuple(option for option in self.options if option.placement == placement)


def effective_taxonomy_label(custom_label: str, canonical_label: str) -> str:
    custom = str(custom_label or "").strip()
    canonical = str(canonical_label or "").strip()
    if not custom:
        return canonical
    if not canonical or custom.casefold() == canonical.casefold():
        return custom
    return f"{custom} - {canonical}"


def normalize_effective_taxonomy_label(value: str) -> str:
    """Normalize display/search spelling without conflating component labels."""
    return "".join(ch.casefold() for ch in str(value or "") if ch.isalnum())


def _quoted_term(field_name: str, value: str) -> str:
    prefix = _FIELD_PREFIXES[field_name]
    escaped = str(value or "").replace("\\", "\\\\").replace('"', '\\"')
    return f'{prefix}:"{escaped}"'


def _query_text(groups: list[list[str]]) -> str:
    seen: set[tuple[str, ...]] = set()
    rendered = []
    for group in groups:
        key = tuple(term.strip().casefold() for term in group if term.strip())
        if not key or key in seen:
            continue
        seen.add(key)
        rendered.append(" AND ".join(term.strip() for term in group if term.strip()))
    return " OR ".join(rendered)


def _effective_option_query(
    local_query: str,
    parent_fields: dict[str, str],
    placement: str,
    label: str,
) -> str:
    inherited_terms = [
        _quoted_term(field_name, str(parent_fields[field_name]))
        for field_name in ("audio_type", "category", "subcategory")
        if parent_fields.get(field_name)
    ]
    filter_groups = [
        [*inherited_terms, *group]
        for group in parse_query_groups(local_query)
    ]
    return _query_text(filter_groups)


def expand_custom_taxonomy_query(
    query: str,
    options: list[CustomTreeFilterOption],
) -> str:
    """Expand virtual custom taxonomy labels into their effective DB queries."""
    expanded_groups: list[list[str]] = []
    for source_group in parse_query_groups(query):
        combinations: list[list[str]] = [[]]
        for term in source_group:
            split = split_field_term(term)
            replacements: list[list[str]] = []
            if split:
                prefix, raw_value = split
                placement = _CUSTOM_PLACEMENTS.get(prefix.casefold())
                value = TreeOrganizationResolver._strip_quotes(raw_value).strip().casefold()
                if placement and value:
                    for option in options:
                        if option.placement == placement and option.label.strip().casefold() == value:
                            replacements.extend(parse_query_groups(option.query))
            if not replacements:
                replacements = [[term]]
            combinations = [
                [*base, *replacement]
                for base in combinations
                for replacement in replacements
            ]
        expanded_groups.extend(combinations)
    return _query_text(expanded_groups) or query


def effective_taxonomy_query_groups(query: str) -> list[tuple[str, list[tuple[str, str]]]]:
    """Split user-facing taxonomy predicates from terms handled by staging FTS."""
    result: list[tuple[str, list[tuple[str, str]]]] = []
    for group in parse_query_groups(query):
        remaining: list[str] = []
        predicates: list[tuple[str, str]] = []
        for term in group:
            split = split_field_term(term)
            if split:
                prefix, raw_value = split
                placement = _CUSTOM_PLACEMENTS.get(prefix.casefold())
                if placement:
                    predicates.append((placement, TreeOrganizationResolver._strip_quotes(raw_value).strip()))
                    continue
            remaining.append(term)
        result.append((" AND ".join(remaining), predicates))
    return result


def custom_tree_filter_options(
    profile: TreeOrganizationProfile | None,
    node_counts: dict[str, int] | None = None,
) -> list[CustomTreeFilterOption]:
    if profile is None:
        return []
    resolver = TreeOrganizationResolver()
    options: list[CustomTreeFilterOption] = []
    for node in profile.nodes:
        query = str(node.filter_query or "").strip()
        if not node.enabled or node.id == profile.root_node_id or node.node_type != "custom" or not query:
            continue
        parent_fields = resolver.semantic_fields_for_node_parent(profile, node.id)
        if parent_fields.get("category") and not parent_fields.get("subcategory"):
            placement = "subcategory"
        elif parent_fields.get("audio_type") and not parent_fields.get("category"):
            placement = "category"
        else:
            continue
        effective_query = _effective_option_query(query, parent_fields, placement, node.name)
        options.append(
            CustomTreeFilterOption(
                node_id=node.id,
                label=node.name,
                query=effective_query,
                placement=placement,
                audio_type=str(parent_fields.get("audio_type") or ""),
                parent_category=str(parent_fields.get("category") or ""),
                count=(int(node_counts.get(node.id, 0)) if node_counts is not None else None),
            )
        )
    return options
