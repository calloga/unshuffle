from __future__ import annotations

from dataclasses import dataclass

from unshuffle.logic.tree_organization import TreeOrganizationProfile, TreeOrganizationResolver


@dataclass(frozen=True)
class CustomTreeFilterOption:
    node_id: str
    label: str
    query: str
    placement: str
    audio_type: str = ""


def custom_tree_filter_options(profile: TreeOrganizationProfile | None) -> list[CustomTreeFilterOption]:
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
        options.append(
            CustomTreeFilterOption(
                node_id=node.id,
                label=node.name,
                query=query,
                placement=placement,
                audio_type=str(parent_fields.get("audio_type") or ""),
            )
        )
    return options
