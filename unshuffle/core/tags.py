import json
from typing import Iterable, List

import re

from .patterns import BPM_REGEX_PATTERN, KEY_EXTRACT_REGEX_PATTERN, KEY_TOKEN_REGEX_PATTERN


def normalize_tag(tag: str) -> str:
    """Normalize user-visible tags while preserving compact musical notation."""
    value = (tag or "").strip()
    if not value:
        return ""
    value = re.sub(r"\s+", "", value)
    bpm_match = re.fullmatch(r"(\d+(?:\.\d+)?)bpm", value, re.IGNORECASE)
    if bpm_match:
        bpm = bpm_match.group(1)
        if "." in bpm:
            bpm = bpm.rstrip("0").rstrip(".")
        return f"{bpm}bpm"
    if KEY_TOKEN_REGEX_PATTERN.fullmatch(value):
        return value.lower()
    return value


def normalize_tags(tags: Iterable[str]) -> List[str]:
    """Return de-duplicated normalized tags in source order."""
    result = []
    seen = set()
    for tag in tags or []:
        normalized = normalize_tag(tag)
        key = normalized.lower()
        if normalized and key not in seen:
            result.append(normalized)
            seen.add(key)
    return result


def extract_tags_from_name(name: str) -> List[str]:
    """Extract compact BPM/key tags from a filename or folder fragment."""
    text = (name or "")
    text = re.sub(r"[_-]+", " ", text)
    tags = []
    tags.extend(match.group(1) for match in BPM_REGEX_PATTERN.finditer(text))
    tags.extend(match.group(1) for match in KEY_EXTRACT_REGEX_PATTERN.finditer(text))
    return normalize_tags(tags)


def tags_to_search_text(tags) -> str:
    """Serialize tags as simple FTS-friendly text."""
    if isinstance(tags, str):
        return " ".join(parse_tags(tags))
    return " ".join(normalize_tags(tags))


def parse_tags(value) -> List[str]:
    """Parse tags from list, JSON string, comma string, or whitespace string."""
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return normalize_tags(value)

    text = str(value or "").strip()
    if not text:
        return []

    try:
        decoded = json.loads(text)
    except Exception:
        decoded = None
    if isinstance(decoded, list):
        return normalize_tags(decoded)

    return normalize_tags(part for part in re.split(r"[,\s]+", text) if part)
