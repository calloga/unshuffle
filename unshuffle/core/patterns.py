"""Authoritative regex patterns shared across classification and tags."""

import re


BPM_REGEX_PATTERN = re.compile(r"(\d+(?:\.\d+)?\s?bpm)", re.IGNORECASE)
KEY_REGEX_PATTERN = re.compile(
    r"(?:^|[\s_\-\.])([A-G][#b]?\s?(?:maj|min|m|major|minor|Amin|Cmaj)?)(?:\.wav|\.aif|\.flac|[\s_\-\.]|$)",
    re.IGNORECASE,
)
KEY_TOKEN_REGEX_PATTERN = re.compile(r"^[A-G](?:#|b)?(?:maj|min|m|major|minor)?$", re.IGNORECASE)
KEY_EXTRACT_REGEX_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])([A-G](?:#|b)?(?:\s?(?:maj|min|m|major|minor))?)(?![A-Za-z0-9])",
    re.IGNORECASE,
)
