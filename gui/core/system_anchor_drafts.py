from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DraftSnapshot:
    actions: list[tuple[str, object]]
    counts: Counter[str]
    action_drafts: dict[str, str]
    update_drafts: dict[str, Any]


def draft_count(controller) -> int:
    return len(controller._anchor_draft_actions) + len(controller._anchor_action_drafts)


def draft_status_message(controller) -> str:
    actions = controller._anchor_draft_count()
    if actions <= 0:
        return "No anchor draft changes"
    labels = []
    promotion_count = (
        sum(1 for action in controller._anchor_action_drafts.values() if action == "promotion")
        + controller._anchor_draft_counts.get("promotion", 0)
    )
    ignore_count = (
        sum(1 for action in controller._anchor_action_drafts.values() if action == "ignore")
        + controller._anchor_draft_counts.get("ignore", 0)
    )
    update_count = (
        sum(1 for action in controller._anchor_action_drafts.values() if action == "update")
        + controller._anchor_draft_counts.get("update", 0)
    )
    other_count = sum(
        count
        for kind, count in controller._anchor_draft_counts.items()
        if kind not in {"promotion", "ignore", "update"}
    )
    if promotion_count:
        labels.append(f"{promotion_count} anchor promotion{'s' if promotion_count != 1 else ''}")
    if ignore_count:
        labels.append(f"{ignore_count} anchor ignore{'s' if ignore_count != 1 else ''}")
    if update_count:
        labels.append(f"{update_count} sound group edit{'s' if update_count != 1 else ''}")
    if other_count:
        labels.append(f"{other_count} anchor action{'s' if other_count != 1 else ''}")
    if not labels:
        labels.append(f"{actions} anchor draft action{'s' if actions != 1 else ''}")
    if len(labels) == 1:
        staged = labels[0]
    else:
        staged = ", ".join(labels[:-1]) + f", and {labels[-1]}"
    if actions == 1:
        return f"Staged {staged}. Save to keep it, or Save and Apply to use it now."
    return f"Staged {staged} across {actions} draft actions. Save to keep them, or Save and Apply to use them now."


def clear_drafts(controller) -> None:
    controller._anchor_draft_actions.clear()
    controller._anchor_draft_counts.clear()
    controller._anchor_action_drafts.clear()
    controller._anchor_update_drafts.clear()


def snapshot(controller) -> DraftSnapshot:
    return DraftSnapshot(
        actions=list(controller._anchor_draft_actions),
        counts=Counter(controller._anchor_draft_counts),
        action_drafts=dict(controller._anchor_action_drafts),
        update_drafts=dict(controller._anchor_update_drafts),
    )


def restore(controller, draft_snapshot: DraftSnapshot) -> None:
    controller._anchor_draft_actions = draft_snapshot.actions
    controller._anchor_draft_counts = draft_snapshot.counts
    controller._anchor_action_drafts = draft_snapshot.action_drafts
    controller._anchor_update_drafts = draft_snapshot.update_drafts
