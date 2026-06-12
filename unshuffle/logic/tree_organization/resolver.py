from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from ...core.path_safety import sanitize_filename
from .filter_evaluator import FilterEvaluator, parse_query_groups, split_field_term
from .models import (
    ProfileValidationIssue,
    ProfileValidationResult,
    TreeOrganizationNode,
    TreeOrganizationProfile,
)
from .routing import TreeRouteBuilder


_SEMANTIC_PREFIXES = {
    "type": "audio_type",
    "cat": "category",
    "category": "category",
    "sub": "subcategory",
    "subcategory": "subcategory",
    "pack": "pack",
    "packname": "pack",
}


@dataclass(frozen=True)
class _CompiledProfile:
    nodes: dict[str, TreeOrganizationNode]
    children: dict[str, list[TreeOrganizationNode]]
    exact_fields: dict[str, dict[str, str]]
    query_groups: dict[str, tuple[tuple[str, ...], ...]]
    parent_fields: dict[str, dict[str, str]]
    safe_segments: dict[str, str]


class TreeOrganizationResolver:
    def __init__(self, evaluator: FilterEvaluator | None = None):
        self.evaluator = evaluator or FilterEvaluator()
        self._route_builder = TreeRouteBuilder(self.evaluator)
        self._compiled_signature = None
        self._compiled_profile: _CompiledProfile | None = None

    def validate_profile(self, profile: TreeOrganizationProfile, records: list) -> ProfileValidationResult:
        issues: list[ProfileValidationIssue] = []
        compiled = self._compile_profile(profile)
        root = compiled.nodes.get(profile.root_node_id)
        if root is None:
            return ProfileValidationResult(False, [ProfileValidationIssue("Profile root node is missing.")])
        if root.parent_id is not None or root.id != "root":
            issues.append(ProfileValidationIssue("Root node is locked and must use id 'root' with no parent.", (root.id,)))
        for node in profile.nodes:
            if not node.id:
                issues.append(ProfileValidationIssue("A tree node is missing an id."))
            if node.id != profile.root_node_id and node.parent_id not in compiled.nodes:
                issues.append(ProfileValidationIssue(f'"{node.name}" has a missing parent.', (node.id,)))
            if node.id == profile.root_node_id and (not node.enabled or node.filter_query):
                issues.append(ProfileValidationIssue("Root cannot be disabled or given a custom filter.", (node.id,)))
            error = self.evaluator.validate(node.filter_query)
            if error:
                issues.append(ProfileValidationIssue(f'"{node.name}" has an invalid filter: {error}', (node.id,)))
            if node.id != profile.root_node_id and not compiled.safe_segments.get(node.id):
                issues.append(ProfileValidationIssue(f'"{node.name}" is not a safe folder name after sanitizing.', (node.id,)))

        issues.extend(self._cycle_issues(profile.nodes, profile.root_node_id))
        for parent_id, siblings in compiled.children.items():
            fallback_count = sum(1 for node in siblings if node.node_type == "fallback" and node.enabled)
            if fallback_count > 1:
                issues.append(ProfileValidationIssue("Only one enabled fallback node is allowed per parent.", (parent_id,)))
            segment_owners: dict[str, TreeOrganizationNode] = {}
            for node in siblings:
                if not node.enabled:
                    continue
                segment = compiled.safe_segments.get(node.id, "")
                if not segment:
                    continue
                existing = segment_owners.get(segment.casefold())
                if existing is not None:
                    issues.append(
                        ProfileValidationIssue(
                            f'"{existing.name}" and "{node.name}" route to the same folder name "{segment}" after sanitizing.',
                            (existing.id, node.id),
                        )
                    )
                else:
                    segment_owners[segment.casefold()] = node

        if not any(issue.blocking for issue in issues):
            issues.extend(self._overlap_issues(profile, records))
        return ProfileValidationResult(not any(issue.blocking for issue in issues), issues)

    def resolve_record(
        self,
        record,
        profile: TreeOrganizationProfile,
        records: list,
        *,
        flat: bool = False,
        append_native: bool = False,
    ) -> Path:
        from .routing import _default_levels
        levels = list(_default_levels())
        if flat:
            levels = [lvl for lvl in levels if lvl[0] != "pack"]

        if append_native:
            routes = self._route_builder.routes_for(
                [record],
                profile=profile,
                levels=levels,
                presentation_mode=False,
            )
            if not routes:
                return Path(".")
            parts = tuple(
                part
                for part in routes[0].parts
                if not (part.kind == "subcategory" and part.residual and part.label == "Other")
            )
        else:
            parts = self._route_builder.resolve_parts(record, profile, records)

        segments = [part.label for part in parts]
        return Path(*segments) if segments else Path(".")

    def resolve_records(
        self,
        records: list,
        profile: TreeOrganizationProfile,
        *,
        flat: bool = False,
        append_native: bool = False,
    ) -> dict[int, Path]:
        from .routing import _default_levels
        levels = list(_default_levels())
        if flat:
            levels = [lvl for lvl in levels if lvl[0] != "pack"]

        if not append_native:
            return {id(record): self.resolve_record(record, profile, records, flat=flat) for record in records}

        resolved: dict[int, Path] = {}
        routes = self._route_builder.routes_for(
            records,
            profile=profile,
            levels=levels,
            presentation_mode=False,
        )
        for route in routes:
            parts = tuple(
                part
                for part in route.parts
                if not (part.kind == "subcategory" and part.residual and part.label == "Other")
            )
            segments = [part.label for part in parts]
            resolved[id(route.record)] = Path(*segments) if segments else Path(".")
        return resolved

    def routed_records(
        self,
        profile: TreeOrganizationProfile,
        records: list,
        levels: list[tuple[str, str]] | None = None,
    ) -> dict[tuple[str, ...], list]:
        return self._route_builder.routed_records(profile, records, levels=levels)

    def _overlap_issues(self, profile: TreeOrganizationProfile, records: list) -> list[ProfileValidationIssue]:
        issues: list[ProfileValidationIssue] = []
        compiled = self._compile_profile(profile)
        children = compiled.children
        parent_sets = self._effective_sets(profile, records)
        for parent_id, siblings in children.items():
            custom_siblings = [node for node in siblings if node.enabled and node.node_type == "custom"]
            if len(custom_siblings) < 2:
                continue
            parent_records = parent_sets.get(parent_id, list(records))
            matched: dict[str, set[str]] = {}
            for node in custom_siblings:
                parent_fields = compiled.parent_fields.get(node.id, {})
                matched[node.id] = {
                    self.evaluator.record_id(record, idx)
                    for idx, record in enumerate(parent_records)
                    if self._matches(record, node, parent_fields, compiled)
                }
            for idx, left in enumerate(custom_siblings):
                for right in custom_siblings[idx + 1 :]:
                    overlap = matched[left.id] & matched[right.id]
                    if overlap:
                        issues.append(
                            ProfileValidationIssue(
                                f'"{left.name}" and "{right.name}" overlap on {len(overlap)} files. Custom sibling filters must be mutually exclusive.',
                                (left.id, right.id),
                                tuple(sorted(overlap)),
                            )
                        )
        return issues

    def _effective_sets(self, profile: TreeOrganizationProfile, records: list) -> dict[str, list]:
        compiled = self._compile_profile(profile)
        children = compiled.children
        sets = {profile.root_node_id: list(records)}

        def walk(parent_id: str, seen: set[str]):
            if parent_id in seen:
                return
            seen = {*seen, parent_id}
            parent_records = sets.get(parent_id, [])
            for node in children.get(parent_id, []):
                if not node.enabled:
                    sets[node.id] = []
                elif node.node_type == "fallback":
                    sets[node.id] = list(parent_records)
                else:
                    parent_fields = compiled.parent_fields.get(node.id, {})
                    sets[node.id] = [
                        record
                        for record in parent_records
                        if self._matches(record, node, parent_fields, compiled)
                    ]
                walk(node.id, seen)

        walk(profile.root_node_id, set())
        return sets

    def _matches(
        self,
        record,
        node: TreeOrganizationNode,
        inherited_fields: dict[str, str] | None = None,
        compiled: _CompiledProfile | None = None,
    ) -> bool:
        if not node.filter_query:
            return True
        inherited = set((inherited_fields or {}).keys())
        groups = compiled.query_groups.get(node.id, ()) if compiled is not None else tuple(parse_query_groups(node.filter_query))
        if not groups:
            return True
        for group in groups:
            active_terms = [term for term in group if self._semantic_field_for_term(term) not in inherited]
            if all(self.evaluator._matches_term(record, term) for term in active_terms):
                return True
        return False

    def _merged_semantic_fields(
        self,
        inherited_fields: dict[str, str],
        node: TreeOrganizationNode,
        compiled: _CompiledProfile | None = None,
    ) -> dict[str, str]:
        fields = dict(inherited_fields)
        exact_fields = (
            compiled.exact_fields.get(node.id, {})
            if compiled is not None
            else self._exact_semantic_fields(node.filter_query)
        )
        for field_name, value in exact_fields.items():
            if field_name not in fields:
                fields[field_name] = value
        return fields

    def _semantic_fields_for_node_parent(self, profile: TreeOrganizationProfile, node_id: str) -> dict[str, str]:
        return dict(self._compile_profile(profile).parent_fields.get(node_id, {}))

    @staticmethod
    def _semantic_field_for_term(term: str) -> str | None:
        split = split_field_term(term)
        if not split:
            return None
        prefix, _value = split
        return _SEMANTIC_PREFIXES.get(prefix.lower())

    @classmethod
    def _exact_semantic_fields(cls, query: str | None) -> dict[str, str]:
        fields: dict[str, str] = {}
        groups = parse_query_groups(query or "")
        if len(groups) != 1:
            return fields
        for term in groups[0]:
            split = split_field_term(term)
            if not split:
                continue
            prefix, raw_value = split
            field_name = _SEMANTIC_PREFIXES.get(prefix.lower())
            if field_name is None:
                continue
            value = cls._strip_quotes(raw_value)
            if value:
                fields[field_name] = value
        return fields

    @staticmethod
    def _strip_quotes(value: str) -> str:
        value = (value or "").strip()
        if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
            return value[1:-1].replace('\\"', '"').replace("\\\\", "\\")
        return value

    @staticmethod
    def _children_by_parent(nodes: list[TreeOrganizationNode]) -> dict[str, list[TreeOrganizationNode]]:
        children: dict[str, list[TreeOrganizationNode]] = defaultdict(list)
        for node in nodes:
            if node.parent_id is not None:
                children[node.parent_id].append(node)
        for siblings in children.values():
            siblings.sort(key=lambda node: (node.sort_order, node.name.lower()))
        return children

    @staticmethod
    def _cycle_issues(nodes: list[TreeOrganizationNode], root_id: str) -> list[ProfileValidationIssue]:
        by_id = {node.id: node for node in nodes}
        issues: list[ProfileValidationIssue] = []
        for node in nodes:
            seen = set()
            current = node
            while current.parent_id is not None:
                if current.id in seen:
                    issues.append(ProfileValidationIssue("Tree profile contains a cycle.", (node.id,)))
                    break
                seen.add(current.id)
                parent = by_id.get(current.parent_id)
                if parent is None:
                    break
                current = parent
        return issues

    @staticmethod
    def _safe_segment(name: str) -> str:
        return sanitize_filename(name or "").strip() or "Folder"

    def _compile_profile(self, profile: TreeOrganizationProfile) -> _CompiledProfile:
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
                    node.hide_subbranches,
                )
                for node in profile.nodes
            ),
        )
        if signature == self._compiled_signature and self._compiled_profile is not None:
            return self._compiled_profile

        nodes = {node.id: node for node in profile.nodes}
        children = self._children_by_parent(profile.nodes)
        query_groups = {
            node.id: tuple(tuple(group) for group in parse_query_groups(node.filter_query or ""))
            for node in profile.nodes
        }
        exact_fields = {node.id: self._exact_semantic_fields(node.filter_query) for node in profile.nodes}
        parent_fields: dict[str, dict[str, str]] = {}

        visiting: set[str] = set()

        def fields_for_parent(node_id: str) -> dict[str, str]:
            if node_id in parent_fields:
                return parent_fields[node_id]
            if node_id in visiting:
                parent_fields[node_id] = {}
                return parent_fields[node_id]
            visiting.add(node_id)
            node = nodes.get(node_id)
            if node is None or node.parent_id is None:
                parent_fields[node_id] = {}
                visiting.discard(node_id)
                return parent_fields[node_id]
            parent = nodes.get(node.parent_id)
            if parent is None or parent.parent_id is None:
                parent_fields[node_id] = {}
                visiting.discard(node_id)
                return parent_fields[node_id]
            inherited = dict(fields_for_parent(parent.id))
            for field_name, value in exact_fields.get(parent.id, {}).items():
                if field_name not in inherited:
                    inherited[field_name] = value
            parent_fields[node_id] = inherited
            visiting.discard(node_id)
            return inherited

        for node in profile.nodes:
            fields_for_parent(node.id)

        compiled = _CompiledProfile(
            nodes=nodes,
            children=children,
            exact_fields=exact_fields,
            query_groups=query_groups,
            parent_fields=parent_fields,
            safe_segments={node.id: self._safe_segment(node.name) for node in profile.nodes},
        )
        self._compiled_signature = signature
        self._compiled_profile = compiled
        return compiled
