from __future__ import annotations

from collections import defaultdict
from dataclasses import replace

from unshuffle.logic.tree_organization import TreeOrganizationNode, TreeOrganizationResolver


class TreeOrganizationIndexCountMixin:
    def _after_structure_change(self, selected_id: str) -> None:
        self._rebuild_node_indexes()
        self._render_tree()
        self._select_node(selected_id)
        self._validate()
        self._schedule_count_refresh()

    def _replace_node(self, updated: TreeOrganizationNode) -> None:
        self._nodes = [updated if node.id == updated.id else node for node in self._nodes]
        self._rebuild_node_indexes()

    def _renumber_siblings(self, parent_id: str, preferred_order: list[TreeOrganizationNode] | None = None) -> None:
        ordered = list(preferred_order) if preferred_order is not None else self._children_by_parent().get(parent_id, [])
        order_by_id = {node.id: index + 1 for index, node in enumerate(ordered)}
        self._nodes = [
            replace(node, sort_order=order_by_id[node.id]) if node.id in order_by_id else node
            for node in self._nodes
        ]
        self._rebuild_node_indexes()

    def _descendant_ids(self, node_id: str) -> set[str]:
        return set(self._descendant_lookup.get(node_id, set()))

    def _children_by_parent(self) -> dict[str, list[TreeOrganizationNode]]:
        return self._children_lookup

    def _node_by_id(self) -> dict[str, TreeOrganizationNode]:
        return self._node_lookup

    def _rebuild_node_indexes(self) -> None:
        self._node_lookup = {node.id: node for node in self._nodes}
        children: dict[str, list[TreeOrganizationNode]] = defaultdict(list)
        for node in self._nodes:
            if node.parent_id is not None:
                children[node.parent_id].append(node)
        for siblings in children.values():
            siblings.sort(key=lambda node: (node.sort_order, node.name.lower()))
        self._children_lookup = dict(children)
        self._descendant_lookup = {node.id: self._collect_descendants(node.id) for node in self._nodes}

    def _collect_descendants(self, node_id: str) -> set[str]:
        found: set[str] = set()
        stack = list(self._children_lookup.get(node_id, []))
        while stack:
            child = stack.pop()
            if child.id in found:
                continue
            found.add(child.id)
            stack.extend(self._children_lookup.get(child.id, []))
        return found

    def _preview_count(self, node: TreeOrganizationNode) -> int:
        return self._match_count_cache.get(node.id, 0)

    def _rebuild_count_cache_for_current_nodes(self) -> None:
        self._counts_dirty = False
        profile = self._profile_from_ui()
        nodes = self._node_by_id()
        counts: dict[str, int] = defaultdict(int)
        resolver = TreeOrganizationResolver()
        path_index = self._node_ids_by_route_path(resolver)
        try:
            routed = resolver.routed_records(profile, self._records)
        except ValueError:
            self._match_count_cache = {}
            return
        root = nodes.get(profile.root_node_id)
        if root is not None:
            counts[root.id] = len(self._records)
        for route_parts, routed_records in routed.items():
            record_count = len(routed_records)
            for path_len in range(1, len(route_parts) + 1):
                node_id = path_index.get(tuple(route_parts[:path_len]))
                if node_id:
                    counts[node_id] += record_count
        self._match_count_cache = dict(counts)

    def _schedule_count_refresh(self, delay_ms: int = 350) -> None:
        self._counts_dirty = True
        self._count_refresh_timer.start(delay_ms)

    def _refresh_counts_after_idle(self) -> None:
        if not self._counts_dirty:
            return
        self._rebuild_count_cache_for_current_nodes()
        self._update_visible_count_tooltips()
        self._sync_fields_from_selection()

    def _update_visible_count_tooltips(self) -> None:
        for node_id, item in self._tree_items.items():
            node = self._node_lookup.get(node_id)
            if node is None:
                continue
            count = self._preview_count(node)
            item.setToolTip(f"{node.name}\n{count} routed item{'s' if count != 1 else ''}")

    def _node_ids_by_route_path(self, resolver: TreeOrganizationResolver) -> dict[tuple[str, ...], str]:
        children = self._children_by_parent()
        path_index: dict[tuple[str, ...], str] = {}

        def walk(parent_id: str, parent_path: tuple[str, ...], seen: set[str]) -> None:
            if parent_id in seen:
                return
            seen = {*seen, parent_id}
            for child in children.get(parent_id, []):
                safe_name = resolver._safe_segment(child.name)
                child_path = (*parent_path, safe_name)
                path_index[child_path] = child.id
                walk(child.id, child_path, seen)

        walk(self._profile.root_node_id, (), set())
        return path_index
