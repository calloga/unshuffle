from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field, replace

from ...core.path_safety import sanitize_filename
from .filter_evaluator import FilterEvaluator, parse_query_groups, split_field_term
from .models import TreeOrganizationNode, TreeOrganizationProfile


SEMANTIC_OVERRIDE_ATTR = "_unshuffle_custom_tree_semantic_override"

SEMANTIC_PREFIXES = {
    "type": "audio_type",
    "cat": "category",
    "category": "category",
    "sub": "subcategory",
    "subcategory": "subcategory",
    "pack": "pack",
    "packname": "pack",
    "tag": "tags",
    "tags": "tags",
}

DISPLAY_TO_RECORD_VALUE = {
    ("audio_type", "Utility"): "Non-Audio Assets",
}

RECORD_TO_DISPLAY_VALUE = {
    ("audio_type", "Non-Audio Assets"): "Utility",
}


@dataclass
class RoutePart:
    kind: str
    label: str
    fields: dict[str, str] = field(default_factory=dict)
    source_node_id: str | None = None
    source_node_type: str | None = None
    read_only: bool = False
    residual: bool = False
    hide_subbranches: bool = False


@dataclass
class TreeRoute:
    record: object
    parts: tuple[RoutePart, ...]


@dataclass(frozen=True)
class CompiledTreeProfile:
    nodes: dict[str, TreeOrganizationNode]
    children: dict[str, list[TreeOrganizationNode]]
    enabled_children: dict[str, tuple[TreeOrganizationNode, ...]]
    exact_fields: dict[str, dict[str, str]]
    exact_only: dict[str, bool]
    query_groups: dict[str, tuple[tuple[tuple[str | None, str | None, str], ...], ...]]
    custom_child_index: dict[str, dict[str, dict[str, list[str]]]]
    custom_unindexed_children: dict[str, list[str]]
    parent_fields: dict[str, dict[str, str]]
    safe_segments: dict[str, str]


class TreeRouteBuilder:
    def __init__(self, evaluator: FilterEvaluator | None = None):
        self.evaluator = evaluator or FilterEvaluator()
        self._compiled_signature = None
        self._compiled_profile: CompiledTreeProfile | None = None

    def routes_for(
        self,
        records: list,
        profile: TreeOrganizationProfile | None = None,
        levels: list[tuple[str, str]] | None = None,
        *,
        presentation_mode: bool = True,
        confidence_min: float = 0.0,
        confidence_max: float = 1.0,
        confidence_floor: float | None = None,
        confidence_filter_enabled: bool = True,
    ) -> list[TreeRoute]:
        return list(
            self.iter_routes(
                records,
                profile,
                levels,
                presentation_mode=presentation_mode,
                confidence_min=confidence_min,
                confidence_max=confidence_max,
                confidence_floor=confidence_floor,
                confidence_filter_enabled=confidence_filter_enabled,
            )
        )

    def iter_routes(
        self,
        records: list,
        profile: TreeOrganizationProfile | None = None,
        levels: list[tuple[str, str]] | None = None,
        *,
        presentation_mode: bool = True,
        confidence_min: float = 0.0,
        confidence_max: float = 1.0,
        confidence_floor: float | None = None,
        confidence_filter_enabled: bool = True,
    ):
        levels = list(levels or _default_levels())
        if profile is not None:
            profile = semantic_profile_for_records(profile, records, self.evaluator)
        if profile is None:
            for record in records:
                if not self._record_in_confidence_range(record, confidence_min, confidence_max):
                    continue
                yield TreeRoute(
                    record,
                    tuple(
                        self._native_route_parts(
                            record,
                            levels,
                            {},
                            set(),
                            confidence_floor=confidence_floor,
                            confidence_filter_enabled=confidence_filter_enabled,
                        )
                    ),
                )
            return
        compiled = self._compile_profile(profile)
        for record in records:
            if not self._record_in_confidence_range(record, confidence_min, confidence_max):
                continue
            yield TreeRoute(
                record,
                tuple(
                    self._custom_route_parts(
                        record,
                        profile,
                        compiled,
                        levels,
                        presentation_mode=presentation_mode,
                        confidence_floor=confidence_floor,
                        confidence_filter_enabled=confidence_filter_enabled,
                    )
                ),
            )

    def resolve_parts(self, record, profile: TreeOrganizationProfile, records: list | None = None) -> tuple[RoutePart, ...]:
        if records is not None:
            profile = semantic_profile_for_records(profile, records, self.evaluator)
        compiled = self._compile_profile(profile)
        return tuple(self._profile_prefix_parts(record, profile, compiled))

    def routed_records(
        self,
        profile: TreeOrganizationProfile,
        records: list,
        levels: list[tuple[str, str]] | None = None,
    ) -> dict[tuple[str, ...], list]:
        result: dict[tuple[str, ...], list] = defaultdict(list)
        for route in self.routes_for(records, profile, levels, presentation_mode=False):
            key = tuple(part.label for part in route.parts) or ("Other",)
            result[key].append(route.record)
        return dict(result)

    def _custom_route_parts(
        self,
        record,
        profile: TreeOrganizationProfile,
        compiled: CompiledTreeProfile,
        levels: list[tuple[str, str]],
        *,
        presentation_mode: bool,
        confidence_floor: float | None,
        confidence_filter_enabled: bool,
    ) -> list[RoutePart]:
        if getattr(record, SEMANTIC_OVERRIDE_ATTR, False):
            return self._native_route_parts(
                record,
                levels,
                {},
                set(),
                confidence_floor=confidence_floor,
                confidence_filter_enabled=confidence_filter_enabled,
            )

        prefix = self._profile_prefix_parts(record, profile, compiled)
        if not prefix:
            if not presentation_mode:
                return []
            native = self._native_route_parts(
                record,
                levels,
                {},
                set(),
                confidence_floor=confidence_floor,
                confidence_filter_enabled=confidence_filter_enabled,
            )
            if record_display_value(record, "audio_type") == "Utility":
                native = native[1:]
            return [
                self._presentation_unmatched_part(record),
                *native,
            ]
        if prefix[-1].hide_subbranches:
            return prefix

        fields: dict[str, str] = {}
        consumed: set[str] = set()
        for part in prefix:
            for field_name, value in part.fields.items():
                fields.setdefault(field_name, value)
            consumed.update(part.fields)
        return [
            *prefix,
            *self._native_route_parts(
                record,
                levels,
                fields,
                consumed,
                confidence_floor=confidence_floor,
                confidence_filter_enabled=confidence_filter_enabled,
            ),
        ]

    def _profile_prefix_parts(
        self,
        record,
        profile: TreeOrganizationProfile,
        compiled: CompiledTreeProfile,
    ) -> list[RoutePart]:
        current = compiled.nodes[profile.root_node_id]
        parts: list[RoutePart] = []
        semantic_fields: dict[str, str] = {}
        while True:
            enabled_children = compiled.enabled_children.get(current.id, ())
            custom = self._matching_custom_children(record, current.id, enabled_children, compiled, semantic_fields)
            if len(custom) > 1:
                raise ValueError("Custom sibling overlap must be resolved before routing.")
            next_node = custom[0] if custom else None
            if next_node is None:
                system = [
                    node
                    for node in enabled_children
                    if node.node_type == "system"
                    and node.id != profile.root_node_id
                    and self._matches(record, node, compiled, semantic_fields)
                ]
                next_node = system[0] if system else None
            if next_node is None:
                fallbacks = [node for node in enabled_children if node.node_type == "fallback"]
                next_node = fallbacks[0] if fallbacks else None
            if next_node is None:
                break

            semantic_fields = self._merged_semantic_fields(semantic_fields, next_node, compiled)
            fields = {field: value for field, value in semantic_fields.items() if field != "tags"}
            exact_fields = compiled.exact_fields.get(next_node.id) or {}
            if next_node.node_type == "custom" and (not exact_fields or set(exact_fields) <= {"tags"}):
                part_kind = "filter"
            else:
                part_kind = node_type_for_fields(fields, next_node.node_type)
            parts.append(
                RoutePart(
                    kind=part_kind,
                    label=compiled.safe_segments[next_node.id],
                    fields=fields,
                    source_node_id=next_node.id,
                    source_node_type=next_node.node_type,
                    read_only=_is_read_only_utility_node(next_node, profile.root_node_id),
                    residual=next_node.node_type == "fallback",
                    hide_subbranches=bool(getattr(next_node, "hide_subbranches", False)),
                )
            )
            if getattr(next_node, "hide_subbranches", False):
                break
            current = next_node
        return parts

    def _matching_custom_children(
        self,
        record,
        parent_id: str,
        enabled_children: tuple[TreeOrganizationNode, ...],
        compiled: CompiledTreeProfile,
        inherited_fields: dict[str, str],
    ) -> list[TreeOrganizationNode]:
        if not enabled_children:
            return []

        candidate_ids: set[str] = set()
        indexed = compiled.custom_child_index.get(parent_id, {})
        active_indexed = [
            (field_name, by_value)
            for field_name, by_value in indexed.items()
            if field_name not in inherited_fields
        ]
        single_index_field = ""
        if len(active_indexed) == 1:
            single_index_field, by_value = active_indexed[0]
            for value in record_display_values(record, single_index_field):
                candidate_ids.update(by_value.get(value, ()))
        else:
            for field_name, by_value in active_indexed:
                for value in record_display_values(record, field_name):
                    candidate_ids.update(by_value.get(value, ()))
        candidate_ids.update(compiled.custom_unindexed_children.get(parent_id, ()))

        if inherited_fields:
            for node in enabled_children:
                if node.node_type != "custom":
                    continue
                exact = compiled.exact_fields.get(node.id) or {}
                if exact and all(field_name in inherited_fields for field_name in exact):
                    candidate_ids.add(node.id)

        ordered = []
        for node in enabled_children:
            if node.id not in candidate_ids or node.node_type != "custom":
                continue
            if compiled.exact_only.get(node.id):
                exact = compiled.exact_fields.get(node.id) or {}
                if (
                    single_index_field
                    and not inherited_fields
                    and len(exact) == 1
                    and single_index_field in exact
                ):
                    ordered.append(node)
                elif self._matches_exact_fields(record, exact, inherited_fields):
                    ordered.append(node)
            elif self._matches(record, node, compiled, inherited_fields):
                ordered.append(node)
        return ordered

    @staticmethod
    def _matches_exact_fields(record, exact_fields: dict[str, str], inherited_fields: dict[str, str]) -> bool:
        for field_name, expected in exact_fields.items():
            if field_name in inherited_fields:
                continue
            if field_name == "tags":
                if expected not in record_display_values(record, field_name):
                    return False
            elif record_display_value(record, field_name) != expected:
                return False
        return True

    def _native_route_parts(
        self,
        record,
        levels: list[tuple[str, str]],
        base_fields: dict[str, str],
        consumed_fields: set[str],
        *,
        confidence_floor: float | None,
        confidence_filter_enabled: bool,
    ) -> list[RoutePart]:
        parts: list[RoutePart] = []
        fields = dict(base_fields or {})
        consumed = set(consumed_fields or set())
        for field_name, node_type in levels:
            label = self._level_display_value(
                record,
                field_name,
                confidence_floor=confidence_floor,
                confidence_filter_enabled=confidence_filter_enabled,
            )
            if not label:
                continue
            expected = fields.get(field_name)
            if field_name in consumed and expected is not None and expected == label:
                continue
            next_fields = dict(fields)
            next_fields[field_name] = label
            parts.append(
                RoutePart(
                    kind=node_type,
                    label=label,
                    fields={field_name: label},
                    read_only=field_name == "audio_type" and label == "Utility",
                    residual=field_name == "subcategory" and label == "Other",
                )
            )
            fields = next_fields
            consumed.add(field_name)
        return parts

    def _presentation_unmatched_part(self, record) -> RoutePart:
        if record_display_value(record, "audio_type") == "Utility":
            return RoutePart(
                kind="type",
                label="Utility",
                fields={"audio_type": "Utility"},
                read_only=True,
            )
        return RoutePart(kind="fallback", label="Other", residual=True)

    def _level_display_value(
        self,
        record,
        field_name: str,
        *,
        confidence_floor: float | None,
        confidence_filter_enabled: bool,
    ) -> str:
        low_confidence_uncategorized = False
        if confidence_filter_enabled and confidence_floor is not None:
            try:
                low_confidence_uncategorized = (
                    float(getattr(record, "confidence", 0.0) or 0.0) < confidence_floor
                    and not getattr(record, "is_manual", False)
                    and not getattr(record, "is_hands_off", False)
                )
            except (ValueError, TypeError):
                low_confidence_uncategorized = False

        if field_name == "confidence_band":
            try:
                conf = float(getattr(record, "confidence", 0.0) or 0.0)
            except (ValueError, TypeError):
                return "Unknown Confidence"
            if conf >= 0.9:
                return "90-100% (High Confidence)"
            if conf >= 0.7:
                return "70-90% (Medium Confidence)"
            if conf >= 0.5:
                return "50-70% (Low Confidence)"
            return "0-50% (Uncertain / Noise)"

        if low_confidence_uncategorized and field_name in {"category", "subcategory"}:
            return "Uncategorized" if field_name == "category" else "Other"
        return record_display_value(record, field_name)

    @staticmethod
    def _record_in_confidence_range(record, confidence_min: float, confidence_max: float) -> bool:
        try:
            conf = float(getattr(record, "confidence", 0.0) or 0.0)
        except (ValueError, TypeError):
            return True
        return confidence_min <= conf <= confidence_max

    def _matches(
        self,
        record,
        node: TreeOrganizationNode,
        compiled: CompiledTreeProfile,
        inherited_fields: dict[str, str] | None = None,
    ) -> bool:
        if not node.filter_query:
            return True
        inherited = set((inherited_fields or {}).keys())
        groups = compiled.query_groups.get(node.id, ())
        if not groups:
            return True
        for group in groups:
            active_terms = [term for term in group if not term[0] or term[0] not in inherited]
            if all(self._matches_compiled_term(record, term) for term in active_terms):
                return True
        return False

    def _matches_compiled_term(self, record, term: tuple[str | None, str | None, str]) -> bool:
        field_name, display_value, raw_term = term
        if field_name:
            if field_name == "tags":
                return self.evaluator._matches_field(record, field_name, display_value or "")
            return record_display_value(record, field_name) == display_value
        return self.evaluator._matches_term(record, raw_term)

    def _merged_semantic_fields(
        self,
        inherited_fields: dict[str, str],
        node: TreeOrganizationNode,
        compiled: CompiledTreeProfile,
    ) -> dict[str, str]:
        fields = dict(inherited_fields)
        for field_name, value in compiled.exact_fields.get(node.id, {}).items():
            if field_name not in fields:
                fields[field_name] = query_display_value(field_name, value)
        return fields

    @classmethod
    def _exact_semantic_fields(cls, query: str | None) -> dict[str, str]:
        return cls._exact_semantic_fields_from_groups(cls._compiled_query_groups(query))

    @staticmethod
    def _compiled_query_groups(query: str | None) -> tuple[tuple[tuple[str | None, str | None, str], ...], ...]:
        groups = parse_query_groups(query or "")
        compiled_groups = []
        for group in groups:
            compiled_terms = []
            for term in group:
                split = split_field_term(term)
                if split:
                    prefix, raw_value = split
                    field_name = SEMANTIC_PREFIXES.get(prefix.lower())
                    if field_name:
                        compiled_terms.append(
                            (
                                field_name,
                                query_display_value(field_name, _strip_quotes(raw_value)),
                                term,
                            )
                        )
                        continue
                compiled_terms.append((None, None, term))
            compiled_groups.append(tuple(compiled_terms))
        return tuple(compiled_groups)

    @staticmethod
    def _exact_semantic_fields_from_groups(
        groups: tuple[tuple[tuple[str | None, str | None, str], ...], ...]
    ) -> dict[str, str]:
        fields: dict[str, str] = {}
        if len(groups) != 1:
            return fields
        for field_name, display_value, _raw_term in groups[0]:
            if field_name is None:
                continue
            if display_value:
                fields[field_name] = display_value
        return fields

    def _compile_profile(self, profile: TreeOrganizationProfile) -> CompiledTreeProfile:
        signature = (
            profile.root_node_id,
            tuple(
                (
                    node.id,
                    node.parent_id,
                    node.name,
                    node.filter_query,
                    node.node_type,
                    node.sort_order,
                    node.enabled,
                    getattr(node, "hide_subbranches", False),
                )
                for node in profile.nodes
            ),
        )
        if signature == self._compiled_signature and self._compiled_profile is not None:
            return self._compiled_profile

        nodes = {node.id: node for node in profile.nodes}
        children = children_by_parent(profile.nodes)
        enabled_children = {
            parent_id: tuple(node for node in siblings if node.enabled)
            for parent_id, siblings in children.items()
        }
        query_groups = {node.id: self._compiled_query_groups(node.filter_query) for node in profile.nodes}
        exact_fields = {node.id: self._exact_semantic_fields_from_groups(query_groups[node.id]) for node in profile.nodes}
        exact_only = {node.id: self._is_exact_semantic_only(query_groups[node.id]) for node in profile.nodes}
        custom_child_index, custom_unindexed_children = self._custom_child_indexes(profile.nodes, exact_fields)
        parent_fields: dict[str, dict[str, str]] = {}

        def fields_for_parent(node_id: str) -> dict[str, str]:
            if node_id in parent_fields:
                return parent_fields[node_id]
            node = nodes.get(node_id)
            if node is None or node.parent_id is None:
                parent_fields[node_id] = {}
                return parent_fields[node_id]
            parent = nodes.get(node.parent_id)
            if parent is None or parent.parent_id is None:
                parent_fields[node_id] = {}
                return parent_fields[node_id]
            inherited = dict(fields_for_parent(parent.id))
            for field_name, value in exact_fields.get(parent.id, {}).items():
                if field_name not in inherited:
                    inherited[field_name] = value
            parent_fields[node_id] = inherited
            return inherited

        for node in profile.nodes:
            fields_for_parent(node.id)

        compiled = CompiledTreeProfile(
            nodes=nodes,
            children=children,
            enabled_children=enabled_children,
            exact_fields=exact_fields,
            exact_only=exact_only,
            query_groups=query_groups,
            custom_child_index=custom_child_index,
            custom_unindexed_children=custom_unindexed_children,
            parent_fields=parent_fields,
            safe_segments={node.id: safe_segment(node.name) for node in profile.nodes},
        )
        self._compiled_signature = signature
        self._compiled_profile = compiled
        return compiled

    @staticmethod
    def _custom_child_indexes(
        nodes: list[TreeOrganizationNode],
        exact_fields: dict[str, dict[str, str]],
    ) -> tuple[dict[str, dict[str, dict[str, list[str]]]], dict[str, list[str]]]:
        indexes: dict[str, dict[str, dict[str, list[str]]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))  # type: ignore
        unindexed: dict[str, list[str]] = defaultdict(list)
        for node in nodes:
            if node.parent_id is None or node.node_type != "custom":
                continue
            exact = exact_fields.get(node.id) or {}
            if not exact:
                unindexed[node.parent_id].append(node.id)
                continue
            for field_name, value in exact.items():
                indexes[node.parent_id][field_name][value].append(node.id)
        return (
            {
                parent_id: {
                    field_name: dict(by_value)
                    for field_name, by_value in by_field.items()
                }
                for parent_id, by_field in indexes.items()
            },
            dict(unindexed),
        )

    @staticmethod
    def _is_exact_semantic_only(groups: tuple[tuple[tuple[str | None, str | None, str], ...], ...]) -> bool:
        return len(groups) == 1 and bool(groups[0]) and all(term[0] for term in groups[0])


def record_display_value(record, field_name: str) -> str:
    if field_name == "tags":
        tags = record_display_values(record, field_name)
        return tags[0] if tags else ""
    raw_value = getattr(record, field_name, "")
    if raw_value is None:
        raw_value = ""
    value = str(raw_value).strip()
    value = RECORD_TO_DISPLAY_VALUE.get((field_name, value), value)
    if value == "" and field_name == "subcategory":
        return "Other"
    return value


def record_display_values(record, field_name: str) -> tuple[str, ...]:
    if field_name != "tags":
        return (record_display_value(record, field_name),)
    values = []
    seen = set()
    for tag in getattr(record, "tags", []) or []:
        value = str(tag or "").strip()
        if value and value not in seen:
            seen.add(value)
            values.append(value)
    return tuple(values)


def display_to_record_value(field_name: str, value: str) -> str:
    return DISPLAY_TO_RECORD_VALUE.get((field_name, value), value)


def query_display_value(field_name: str, value: str) -> str:
    value = display_to_record_value(field_name, value)
    value = RECORD_TO_DISPLAY_VALUE.get((field_name, value), value)
    if value == "" and field_name == "subcategory":
        return "Other"
    return value


def node_type_for_fields(fields: dict[str, str], fallback: str = "custom") -> str:
    if fields.get("pack"):
        return "pack"
    if fields.get("subcategory"):
        return "subcategory"
    if fields.get("category"):
        return "category"
    if fields.get("audio_type"):
        return "type"
    return fallback


def semantic_field_for_term(term: str) -> str | None:
    split = split_field_term(term)
    if not split:
        return None
    prefix, _value = split
    return SEMANTIC_PREFIXES.get(prefix.lower())


def children_by_parent(nodes: list[TreeOrganizationNode]) -> dict[str, list[TreeOrganizationNode]]:
    children: dict[str, list[TreeOrganizationNode]] = defaultdict(list)
    for node in nodes:
        if node.parent_id is not None:
            children[node.parent_id].append(node)
    for siblings in children.values():
        siblings.sort(key=lambda node: (node.sort_order, node.name.lower()))
    return children


def safe_segment(name: str) -> str:
    return sanitize_filename((name or "").strip()) or "Folder"


def _strip_quotes(value: str) -> str:
    value = (value or "").strip()
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        return value[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return value


def _is_read_only_utility_node(node: TreeOrganizationNode, root_id: str) -> bool:
    return (
        node.parent_id == root_id
        and node.node_type == "system"
        and node.name == "Utility"
        and node.filter_query == 'type:"Non-Audio Assets"'
    )


def semantic_profile_for_records(
    profile: TreeOrganizationProfile,
    records: list,
    evaluator: FilterEvaluator | None = None,
) -> TreeOrganizationProfile:
    evaluator = evaluator or FilterEvaluator()
    nodes: list[TreeOrganizationNode] = []
    changed = False
    for node in profile.nodes:
        replacement_query = semantic_equivalent_query(node.filter_query, records, evaluator)
        if replacement_query and replacement_query != node.filter_query:
            nodes.append(replace(node, filter_query=replacement_query))
            changed = True
        else:
            nodes.append(node)
    return replace(profile, nodes=nodes) if changed else profile


def semantic_equivalent_query(query: str | None, records: list, evaluator: FilterEvaluator | None = None) -> str | None:
    text = (query or "").strip()
    if not text or not records:
        return None
    groups = parse_query_groups(text)
    if len(groups) != 1 or len(groups[0]) != 1:
        return None
    term = groups[0][0].strip()
    if not term or split_field_term(term):
        return None
    evaluator = evaluator or FilterEvaluator()
    matched = [record for record in records if evaluator.matches(record, text)]
    if not matched:
        return None
    value = _strip_quotes(term)
    candidates = []
    for prefix, field_name in (
        ("packname", "pack"),
        ("category", "category"),
        ("subcategory", "subcategory"),
        ("type", "audio_type"),
        ("tag", "tags"),
    ):
        if all(_record_field_matches_plain(record, field_name, value, evaluator) for record in matched):
            candidates.append(prefix)
    if len(candidates) != 1:
        return None
    escaped = value.replace('"', '\\"')
    return f'{candidates[0]}:"{escaped}"'


def _record_field_matches_plain(record, field_name: str, value: str, evaluator: FilterEvaluator) -> bool:
    if field_name == "tags":
        text = " ".join(str(tag) for tag in (getattr(record, "tags", []) or []))
    else:
        text = record_display_value(record, field_name)
    return evaluator._token_prefix_match(text, value)


def _default_levels() -> list[tuple[str, str]]:
    return [
        ("audio_type", "type"),
        ("category", "category"),
        ("subcategory", "subcategory"),
        ("pack", "pack"),
    ]
