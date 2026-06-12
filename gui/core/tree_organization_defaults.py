from __future__ import annotations

import re
import uuid

from gui.models.library_tree import build_tree_payload
from unshuffle.logic.tree_organization import TreeOrganizationNode, TreeOrganizationProfile
from unshuffle.logic.tree_organization.models import utc_now_iso


FIELD_PREFIXES = {
    "audio_type": "type",
    "category": "cat",
    "subcategory": "sub",
}

DEFAULT_EDIT_LEVELS = [("audio_type", "type"), ("category", "category"), ("subcategory", "subcategory")]


def build_default_tree_nodes(records: list, library_tab=None, *, collapse_residual_other: bool) -> list[TreeOrganizationNode]:
    tree_model = getattr(library_tab, "tree_model", None) if library_tab else None
    nodes = [
        TreeOrganizationNode(
            id="root",
            parent_id=None,
            name="Root",
            filter_query=None,
            node_type="system",
            sort_order=0,
            enabled=True,
        )
    ]
    grouped = build_tree_payload(
        records,
        list(DEFAULT_EDIT_LEVELS),
        getattr(tree_model, "confidence_min", 0.0) if tree_model else 0.0,
        getattr(tree_model, "confidence_max", 1.0) if tree_model else 1.0,
        confidence_floor=getattr(tree_model, "confidence_floor", None) if tree_model else None,
        confidence_filter_enabled=getattr(tree_model, "confidence_filter_enabled", True) if tree_model else True,
    )
    append_default_profile_nodes(
        nodes,
        "root",
        grouped,
        list(DEFAULT_EDIT_LEVELS),
        (),
        collapse_residual_other=collapse_residual_other,
    )
    ensure_default_utility_node(nodes)
    return nodes


def make_default_shaped_profile(
    profile_id: str,
    name: str,
    records: list,
    library_tab=None,
    *,
    collapse_residual_other: bool,
) -> TreeOrganizationProfile:
    now = utc_now_iso()
    return TreeOrganizationProfile(
        id=profile_id,
        name=name,
        root_node_id="root",
        nodes=build_default_tree_nodes(records, library_tab, collapse_residual_other=collapse_residual_other),
        created_at=now,
        updated_at=now,
    )


def append_default_profile_nodes(
    nodes: list[TreeOrganizationNode],
    parent_id: str,
    grouped,
    levels: list,
    path: tuple[str, ...],
    *,
    collapse_residual_other: bool,
) -> None:
    if not levels or not isinstance(grouped, dict):
        return
    field, _node_type = levels[0]
    if collapse_residual_other and field == "subcategory" and len(grouped) == 1 and "Other" in grouped:
        if len(levels) == 1:
            return
        append_default_profile_nodes(
            nodes,
            parent_id,
            grouped["Other"],
            levels[1:],
            path,
            collapse_residual_other=collapse_residual_other,
        )
        return
    for order, name in enumerate(sorted(grouped), start=1):
        display_name = str(name or "Other")
        node_id = default_node_id((*path, display_name))
        is_residual = field == "subcategory" and display_name == "Other"
        nodes.append(
            TreeOrganizationNode(
                id=node_id,
                parent_id=parent_id,
                name=display_name,
                filter_query=None if is_residual else default_filter_query(field, display_name),
                node_type="fallback" if is_residual else "system",
                sort_order=order,
                enabled=True,
            )
        )
        append_default_profile_nodes(
            nodes,
            node_id,
            grouped[name],
            levels[1:],
            (*path, display_name),
            collapse_residual_other=collapse_residual_other,
        )


def ensure_default_utility_node(nodes: list[TreeOrganizationNode]) -> None:
    if any(node.parent_id == "root" and node.name == "Utility" for node in nodes):
        return
    root_child_count = sum(1 for node in nodes if node.parent_id == "root")
    nodes.append(
        TreeOrganizationNode(
            id=default_node_id(("Utility",)),
            parent_id="root",
            name="Utility",
            filter_query=default_filter_query("audio_type", "Utility"),
            node_type="system",
            sort_order=root_child_count + 1,
            enabled=True,
        )
    )


def default_node_id(parts: tuple[str, ...]) -> str:
    slug = "_".join(re.sub(r"[^a-z0-9]+", "_", part.lower()).strip("_") for part in parts)
    return f"node_{slug[:80] or uuid.uuid4().hex[:8]}"


def default_filter_query(field: str, value: str) -> str | None:
    prefix = FIELD_PREFIXES.get(field)
    if not prefix:
        return None
    raw_value = "Non-Audio Assets" if field == "audio_type" and value == "Utility" else value
    escaped = raw_value.replace('"', '\\"')
    return f'{prefix}:"{escaped}"'
