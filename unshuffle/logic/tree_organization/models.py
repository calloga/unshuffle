from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal


TreeNodeType = Literal["system", "custom", "fallback"]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class TreeOrganizationNode:
    id: str
    parent_id: str | None
    name: str
    filter_query: str | None
    node_type: TreeNodeType
    sort_order: int
    enabled: bool = True
    hide_subbranches: bool = False

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "parent_id": self.parent_id,
            "name": self.name,
            "filter_query": self.filter_query,
            "node_type": self.node_type,
            "sort_order": self.sort_order,
            "enabled": self.enabled,
            "hide_subbranches": self.hide_subbranches,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "TreeOrganizationNode":
        node_type = str(payload.get("node_type") or "custom")
        if node_type not in {"system", "custom", "fallback"}:
            node_type = "custom"
        return cls(
            id=str(payload.get("id") or ""),
            parent_id=None if payload.get("parent_id") is None else str(payload.get("parent_id")),
            name=str(payload.get("name") or ""),
            filter_query=payload.get("filter_query") if payload.get("filter_query") is not None else None,
            node_type=node_type,  # type: ignore[arg-type]
            sort_order=int(payload.get("sort_order") or 0),
            enabled=bool(payload.get("enabled", True)),
            hide_subbranches=bool(payload.get("hide_subbranches", False)),
        )


@dataclass(frozen=True)
class TreeOrganizationProfile:
    id: str
    name: str
    root_node_id: str
    nodes: list[TreeOrganizationNode] = field(default_factory=list)
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "root_node_id": self.root_node_id,
            "nodes": [node.to_dict() for node in self.nodes],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "TreeOrganizationProfile":
        nodes = [TreeOrganizationNode.from_dict(item) for item in payload.get("nodes", []) if isinstance(item, dict)]
        return cls(
            id=str(payload.get("id") or ""),
            name=str(payload.get("name") or "Untitled Tree"),
            root_node_id=str(payload.get("root_node_id") or "root"),
            nodes=nodes,
            created_at=str(payload.get("created_at") or utc_now_iso()),
            updated_at=str(payload.get("updated_at") or utc_now_iso()),
        )


@dataclass(frozen=True)
class DestinationResolution:
    dest_path: Path
    dest_folder: Path
    final_name: str
    relative_path: Path
    used_custom_tree: bool = False


@dataclass(frozen=True)
class ProfileValidationIssue:
    message: str
    node_ids: tuple[str, ...] = ()
    record_ids: tuple[str, ...] = ()
    blocking: bool = True


@dataclass(frozen=True)
class ProfileValidationResult:
    valid: bool
    issues: list[ProfileValidationIssue] = field(default_factory=list)

    @property
    def blocking_messages(self) -> list[str]:
        return [issue.message for issue in self.issues if issue.blocking]


def make_empty_profile(profile_id: str, name: str) -> TreeOrganizationProfile:
    now = utc_now_iso()
    root = TreeOrganizationNode(
        id="root",
        parent_id=None,
        name="Root",
        filter_query=None,
        node_type="system",
        sort_order=0,
        enabled=True,
    )
    return TreeOrganizationProfile(
        id=profile_id,
        name=name,
        root_node_id="root",
        nodes=[root],
        created_at=now,
        updated_at=now,
    )
