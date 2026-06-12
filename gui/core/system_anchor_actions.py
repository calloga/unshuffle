from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable, Any

from unshuffle.core.constants import CATEGORIES
from unshuffle.logic.coherence.models import ANCHOR_VERIFIED, AnchorProfile


VALID_CANDIDATE_ACTIONS = {"promotion", "ignore", "update", ""}


@dataclass(frozen=True)
class DraftActionUpdate:
    handled: bool
    needs_sound_group: bool
    had_update: bool


@dataclass(frozen=True)
class AnchorActionPrompt:
    title: str
    message: str
    cancel_status: str


@dataclass(frozen=True)
class SoundGroupDraft:
    anchor: AnchorProfile | None
    status: str | None


def build_sound_group_draft(
    row: dict[str, Any],
    audio_type: str,
    category: str,
    subcategory: str,
    profile_factory: Callable[[dict, str], AnchorProfile | None],
) -> SoundGroupDraft:
    category = str(category or "").strip()
    if category not in CATEGORIES:
        return SoundGroupDraft(None, f"Taxonomy: '{category}' is not a known category.")

    try:
        payload = json.loads(row.get("profile_json") or "{}")
    except json.JSONDecodeError:
        payload = {}
    if not payload:
        return SoundGroupDraft(None, "Taxonomy: could not edit anchor sound group; profile payload is missing.")

    payload["audio_type"] = str(audio_type or "")
    payload["category"] = category
    payload["subcategory"] = str(subcategory or "")
    anchor = profile_factory(payload, str(row.get("state") or ANCHOR_VERIFIED))
    if anchor is None:
        return SoundGroupDraft(None, "Taxonomy: could not edit anchor sound group; profile payload is invalid.")
    return SoundGroupDraft(anchor, None)


def update_candidate_action_draft(
    action_drafts: dict[str, str],
    update_drafts: dict[str, AnchorProfile],
    anchor_id: str,
    action: str,
) -> DraftActionUpdate:
    anchor_id = str(anchor_id or "")
    action = str(action or "")
    if not anchor_id or action not in VALID_CANDIDATE_ACTIONS:
        return DraftActionUpdate(False, False, False)

    had_update = anchor_id in update_drafts
    if action == "update" and anchor_id not in update_drafts:
        return DraftActionUpdate(True, True, had_update)

    if action:
        if action != "update":
            update_drafts.pop(anchor_id, None)
        action_drafts[anchor_id] = action
    else:
        action_drafts.pop(anchor_id, None)
        update_drafts.pop(anchor_id, None)
    return DraftActionUpdate(True, False, had_update)


def stage_candidate_actions(action_drafts: dict[str, str], anchor_ids: list[str], action: str) -> None:
    for anchor_id in anchor_ids:
        action_drafts[str(anchor_id)] = action


def candidate_action_prompt(action: str, count: int) -> AnchorActionPrompt:
    item_word = "candidate" if count == 1 else "candidates"
    if action == "promotion":
        return AnchorActionPrompt(
            title="Promote Anchor Candidates",
            message=(
                f"Promote {count} anchor {item_word}?\n\n"
                "Promoted anchors are saved as verified coherence anchors and removed from this Candidates list."
            ),
            cancel_status="Promotion cancelled.",
        )
    return AnchorActionPrompt(
        title="Ignore Anchor Candidates",
        message=(
            f"Ignore {count} anchor {item_word}?\n\n"
            "Ignored anchors are removed from this Candidates list."
        ),
        cancel_status="Ignore cancelled.",
    )


def verified_removal_message(count: int) -> tuple[str, str, str]:
    item_word = "anchor" if count == 1 else "anchors"
    prompt = (
        f"Remove {count} verified {item_word} from My Anchors?\n\n"
        "Removed anchors will no longer be used as verified coherence anchors."
    )
    status = f"Taxonomy: removed {count} verified {item_word}."
    return item_word, prompt, status


def selected_anchor_rows(all_rows: list[dict[str, Any]], anchor_ids: list[str]) -> list[dict[str, Any]]:
    selected_ids = set(anchor_ids)
    return [row for row in all_rows if not selected_ids or row.get("anchor_id") in selected_ids]
