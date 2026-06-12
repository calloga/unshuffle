from __future__ import annotations

import json
from typing import Any

from gui.core.system_io import anchor_profile_from_payload
from unshuffle.core.constants import CATEGORIES
from unshuffle.logic.coherence.anchor_profiles import validate_anchor_payload
from unshuffle.logic.coherence.models import ANCHOR_VERIFIED, AnchorProfile


def exportable_anchor_payloads(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    payloads = []
    for row in rows:
        try:
            payload = json.loads(row.get("profile_json") or "{}")
        except json.JSONDecodeError:
            continue
        ok, _reason = validate_anchor_payload(payload, set(CATEGORIES))
        if ok:
            payloads.append(payload)
    return payloads


def imported_anchor_profiles(raw: Any) -> tuple[list[AnchorProfile] | None, list[tuple[int, str]], bool]:
    payloads = raw.get("anchors") if isinstance(raw, dict) else raw
    if not isinstance(payloads, list):
        return None, [], False

    anchors: list[AnchorProfile] = []
    rejected: list[tuple[int, str]] = []
    for index, payload in enumerate(payloads, start=1):
        ok, reason = validate_anchor_payload(payload, set(CATEGORIES))
        if not ok:
            rejected.append((index, reason))
            continue
        anchor = anchor_profile_from_payload(payload, ANCHOR_VERIFIED)
        if anchor is None:
            rejected.append((index, "could not build anchor profile"))
            continue
        anchors.append(anchor)
    return anchors, rejected, True

