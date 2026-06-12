from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from unshuffle.logic.tree_organization import TreeOrganizationProfile, TreeRouteBuilder
from unshuffle.logic.tree_organization.filter_evaluator import parse_query_groups, split_field_term


DESTINATION_PREFIXES = {
    "type": "audio_type",
    "cat": "category",
    "category": "category",
    "sub": "subcategory",
    "subcategory": "subcategory",
    "pack": "pack",
    "packname": "pack",
}

DISPLAY_TO_RECORD_VALUE = {
    ("audio_type", "Utility"): "Non-Audio Assets",
}

RECORD_TO_DISPLAY_VALUE = {
    ("audio_type", "Non-Audio Assets"): "Utility",
}

SEMANTIC_OVERRIDE_ATTR = "_unshuffle_custom_tree_semantic_override"


@dataclass
class ResolvedPresentationNode:
    label: str
    visual_path: tuple[str, ...]
    records: list
    children: list["ResolvedPresentationNode"] = field(default_factory=list)
    node_type: str = "custom"
    semantic_fields: dict[str, str] = field(default_factory=dict)
    consumed_fields: set[str] = field(default_factory=set)
    read_only: bool = False
    source_node_id: str | None = None
    source_node_type: str | None = None
    residual: bool = False


def record_display_value(record, field_name: str) -> str:
    raw_value = getattr(record, field_name, "")
    if raw_value is None:
        raw_value = ""
    value = str(raw_value).strip()
    value = RECORD_TO_DISPLAY_VALUE.get((field_name, value), value)
    if value == "" and field_name == "subcategory":
        return "Other"
    return value


def display_to_record_value(field_name: str, value: str) -> str:
    return DISPLAY_TO_RECORD_VALUE.get((field_name, value), value)


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


def exact_destination_fields_from_filter(query: str | None) -> dict[str, str]:
    groups = parse_query_groups(query or "")
    if len(groups) != 1:
        return {}
    fields: dict[str, str] = {}
    for term in groups[0]:
        split = split_field_term(term)
        if not split:
            continue
        prefix, raw_value = split
        field_name = DESTINATION_PREFIXES.get(prefix.lower())
        if not field_name:
            continue
        value = _strip_quotes(raw_value)
        if not value:
            continue
        fields[field_name] = record_display_value(
            type("RecordValue", (), {field_name: display_to_record_value(field_name, value)})(),
            field_name,
        )
    return fields


def build_normal_resolved_tree(
    records: list,
    levels: list[tuple[str, str]],
    group_records: Callable[[list, list[tuple[str, str]]], dict | list],
    *,
    base_fields: dict[str, str] | None = None,
    base_path: tuple[str, ...] = (),
    consumed_fields: set[str] | None = None,
) -> list[ResolvedPresentationNode]:
    levels = list(levels)
    fields = dict(base_fields or {})
    consumed = set(consumed_fields or set())
    effective_levels = _remaining_levels_for_records(records, levels, fields, consumed)
    if not effective_levels:
        return []
    grouped = group_records(records, effective_levels)
    return _resolved_from_grouped(grouped, effective_levels, base_path, fields, consumed)


def build_route_resolved_tree(
    records: list,
    levels: list[tuple[str, str]],
    group_records: Callable[[list, list[tuple[str, str]]], dict | list],
    *,
    profile: TreeOrganizationProfile | None = None,
    confidence_min: float = 0.0,
    confidence_max: float = 1.0,
    confidence_floor: float | None = None,
    confidence_filter_enabled: bool = True,
) -> list[ResolvedPresentationNode]:
    route_builder = TreeRouteBuilder()
    routes = route_builder.iter_routes(
        records,
        profile,
        levels,
        presentation_mode=True,
        confidence_min=confidence_min,
        confidence_max=confidence_max,
        confidence_floor=confidence_floor,
        confidence_filter_enabled=confidence_filter_enabled,
    )
    metadata = {}
    tree: dict = {}
    for route in routes:
        cursor = tree
        path = ()
        fields: dict[str, str] = {}
        consumed: set[str] = set()
        for part in route.parts:
            path = (*path, part.label)
            fields.update(part.fields)
            consumed.update(part.fields)
            if path not in metadata:
                metadata[path] = {
                    "fields": dict(fields),
                    "consumed": set(consumed),
                    "node_type": node_type_for_fields(fields, part.kind),
                    "source_node_id": part.source_node_id,
                    "source_node_type": part.source_node_type,
                    "read_only": part.read_only,
                    "residual": part.residual,
                    "hide_subbranches": part.hide_subbranches,
                }
            cursor = cursor.setdefault(part.label, {})
            cursor.setdefault("__all_records__", []).append(route.record)
        cursor.setdefault("__records__", []).append(route.record)
    tree, metadata = _collapse_redundant_other_branches(tree, metadata)
    return _resolved_from_custom_tree(tree, (), metadata, levels, group_records)


def build_custom_resolved_tree(
    profile: TreeOrganizationProfile,
    records: list,
    levels: list[tuple[str, str]],
    group_records: Callable[[list, list[tuple[str, str]]], dict | list],
    *,
    confidence_min: float = 0.0,
    confidence_max: float = 1.0,
    confidence_floor: float | None = None,
    confidence_filter_enabled: bool = True,
) -> list[ResolvedPresentationNode]:
    return build_route_resolved_tree(
        records,
        levels,
        group_records,
        profile=profile,
        confidence_min=confidence_min,
        confidence_max=confidence_max,
        confidence_floor=confidence_floor,
        confidence_filter_enabled=confidence_filter_enabled,
    )


def _resolved_from_custom_tree(
    tree: dict,
    path: tuple[str, ...],
    metadata: dict,
    levels,
    group_records,
    parent_fields: dict[str, str] | None = None,
    parent_consumed: set[str] | None = None,
) -> list[ResolvedPresentationNode]:
    nodes = []
    inherited_fields = dict(parent_fields or {})
    inherited_consumed = set(parent_consumed or set())
    for label in sorted(key for key in tree if key not in {"__records__", "__all_records__"}):
        branch = tree[label]
        node_path = (*path, label)
        records = _flatten_custom_records(branch)
        meta = metadata.get(node_path, {})
        meta_fields = dict(meta.get("fields") or {})
        fields = dict(inherited_fields)
        fields.update(meta_fields)
        residual = bool(meta.get("residual") or label == "Other" and not fields)
        if label == "Utility" and not fields:
            fields = {"audio_type": "Utility"}
        if not meta_fields and not residual:
            fields.update(_label_matched_semantic_fields(records, label))
        consumed = inherited_consumed | set(meta.get("consumed") or set(fields))
        fallback_node_type = str(meta.get("node_type") or ("fallback" if residual else "custom"))
        node = ResolvedPresentationNode(
            label=label,
            visual_path=node_path,
            records=records,
            node_type=node_type_for_fields(fields, fallback_node_type),
            semantic_fields=fields,
            consumed_fields=consumed,
            read_only=bool(meta.get("read_only") or (label == "Utility" and fields.get("audio_type") == "Utility")),
            source_node_id=meta.get("source_node_id"),
            source_node_type=meta.get("source_node_type"),
            residual=residual,
        )
        child_groups = {key: val for key, val in branch.items() if key not in {"__records__", "__all_records__"}}
        if bool(meta.get("hide_subbranches")):
            node.children = []
        elif child_groups:
            node.children = _resolved_from_custom_tree(
                branch,
                node_path,
                metadata,
                levels,
                group_records,
                fields,
                consumed,
            )
        else:
            semantic_children = build_normal_resolved_tree(
                records,
                levels,
                group_records,
                base_fields=fields,
                base_path=node_path,
                consumed_fields=consumed,
            )
            node.children = semantic_children
        nodes.append(node)
    return nodes


def _collapse_redundant_other_branches(tree: dict, metadata: dict) -> tuple[dict, dict]:
    def walk(branch: dict, output_path: tuple[str, ...], source_path: tuple[str, ...]) -> tuple[dict, dict]:
        child_labels = [key for key in branch if key not in {"__records__", "__all_records__"}]
        if child_labels == ["Other"]:
            other_source_path = (*source_path, "Other")
            other_branch = branch["Other"]
            other_meta = metadata.get(other_source_path, {})
            parent_meta = metadata.get(source_path, {})
            is_subcategory = (
                other_meta.get("node_type") == "subcategory"
                or parent_meta.get("node_type") == "category"
            )
            if is_subcategory:
                return walk(other_branch, output_path, other_source_path)

        collapsed = {}
        collapsed_metadata = {}
        if "__records__" in branch:
            collapsed["__records__"] = list(branch.get("__records__", []))
        if "__all_records__" in branch:
            collapsed["__all_records__"] = list(branch.get("__all_records__", []))
        for label in child_labels:
            child_output_path = (*output_path, label)
            child_source_path = (*source_path, label)
            child_branch, child_metadata = walk(branch[label], child_output_path, child_source_path)
            collapsed[label] = child_branch
            if child_source_path in metadata:
                collapsed_metadata[child_output_path] = dict(metadata[child_source_path])
            collapsed_metadata.update(child_metadata)
        return collapsed, collapsed_metadata

    return walk(tree, (), ())


def _label_matched_semantic_fields(records, label: str) -> dict[str, str]:
    if not records:
        return {}
    for field_name in ("category", "audio_type", "subcategory", "pack"):
        if label == "Other" and field_name == "subcategory":
            continue
        if _records_share_display_value(records, field_name, label):
            return {field_name: label}
    return {}


def _resolved_from_grouped(grouped, levels, base_path, parent_fields, consumed_fields) -> list[ResolvedPresentationNode]:
    if not levels or not isinstance(grouped, dict):
        return []
    field_name, node_type = levels[0]
    is_leaf = len(levels) == 1
    if field_name == "subcategory" and not is_leaf and len(grouped) == 1 and "Other" in grouped:
        return _resolved_from_grouped(grouped["Other"], levels[1:], base_path, parent_fields, consumed_fields)
    if field_name == "subcategory" and is_leaf and len(grouped) == 1 and "Other" in grouped:
        return []

    nodes = []
    for label in sorted(grouped):
        branch = grouped[label]
        records = list(branch) if is_leaf else _flatten_group(branch)
        fields = dict(parent_fields)
        fields[field_name] = str(label)
        path = (*base_path, str(label))
        node = ResolvedPresentationNode(
            label=str(label),
            visual_path=path,
            records=records,
            node_type=node_type,
            semantic_fields=fields,
            consumed_fields=set(consumed_fields) | {field_name},
            read_only=field_name == "audio_type" and str(label) == "Utility",
            residual=field_name == "subcategory" and str(label) == "Other",
        )
        if not is_leaf:
            node.children = _resolved_from_grouped(branch, levels[1:], path, fields, node.consumed_fields)
        nodes.append(node)
    return nodes


def _remaining_levels_for_records(records, levels, semantic_fields, consumed_fields):
    remaining = []
    for field_name, node_type in levels:
        expected = semantic_fields.get(field_name)
        if field_name in consumed_fields and expected is not None and _records_share_display_value(records, field_name, expected):
            continue
        remaining.append((field_name, node_type))
    return remaining


def _records_share_display_value(records, field_name: str, expected: str) -> bool:
    if not records:
        return False
    return all(record_display_value(record, field_name) == expected for record in records)


def _flatten_custom_records(value: dict) -> list:
    if "__all_records__" in value:
        return list(value.get("__all_records__", []))
    records = list(value.get("__records__", []))
    for key, child in value.items():
        if key not in {"__records__", "__all_records__"}:
            records.extend(_flatten_custom_records(child))
    return records


def _flatten_group(group) -> list:
    if isinstance(group, list):
        return list(group)
    records = []
    for child in group.values():
        records.extend(_flatten_group(child))
    return records


def _strip_quotes(value: str) -> str:
    value = (value or "").strip()
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        return value[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return value
