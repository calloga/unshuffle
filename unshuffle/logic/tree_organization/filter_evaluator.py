from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Iterable

_TERM_RE = re.compile(r"\w+", re.UNICODE)
SEARCH_PREFIX_MAP = {
    "cat": "category",
    "category": "category",
    "sub": "subcategory",
    "subcategory": "subcategory",
    "pack": "pack",
    "packname": "pack",
    "name": "sample_name",
    "file": "sample_name",
    "filename": "sample_name",
    "tag": "tags",
    "tags": "tags",
    "type": "audio_type",
    "conf": "confidence",
    "confidence": "confidence",
    "root": "source",
    "source": "source",
    "path": "path",
    "source_path": "source_path",
}


class FilterEvaluator:
    """In-memory evaluator for Unshuffle saved-filter query strings."""

    def matches(self, record, query: str) -> bool:
        query = (query or "").strip()
        if not query:
            return True
        groups = parse_query_groups(query)
        if not groups:
            return True
        for group in groups:
            if all(self._matches_term(record, term) for term in group):
                return True
        return False

    def matching_record_ids(self, records: Iterable, query: str) -> set[str]:
        return {self.record_id(record, idx) for idx, record in enumerate(records) if self.matches(record, query)}

    def validate(self, query: str | None) -> str | None:
        text = (query or "").strip()
        if not text:
            return None
        if text.count('"') % 2:
            return "Unclosed quote in filter query."
        try:
            parse_query_groups(text)
        except Exception as exc:
            return f"Invalid filter query: {exc}"
        return None

    @staticmethod
    def record_id(record, fallback: int | str = "") -> str:
        value = getattr(record, "staging_row_id", None)
        if value is None:
            value = getattr(record, "row_id", None)
        if value is None:
            value = getattr(record, "id", None)
        if value is None:
            value = fallback if fallback != "" else getattr(record, "source_path", "")
        return str(value)

    def _matches_term(self, record, term: str) -> bool:
        term = (term or "").strip()
        if not term:
            return True
        field = split_field_term(term)
        if field:
            prefix, value = field
            mapped = SEARCH_PREFIX_MAP.get(prefix.lower(), prefix.lower())
            return self._matches_field(record, mapped, self._strip_quotes(value))
        value = self._strip_quotes(term)
        haystack = " ".join(
            [
                str(getattr(record, "source_path", "") or ""),
                Path((getattr(record, "source_path", ""))).name,
                str(getattr(record, "pack", "") or ""),
                str(getattr(record, "category", "") or ""),
                str(getattr(record, "subcategory", "") or ""),
                str(getattr(record, "audio_type", "") or ""),
                " ".join(str(tag) for tag in (getattr(record, "tags", []) or [])),
            ]
        )
        return self._token_prefix_match(haystack, value)

    def _matches_field(self, record, field: str, value: str) -> bool:
        if field in {"source", "source_path", "path"}:
            source = str(getattr(record, "source_path", "") or "").replace("\\", "/").lower()
            needle = value.replace("\\", "/").rstrip("/").lower()
            return source == needle or source.startswith(needle + "/")
        if field == "sample_name":
            text = Path(str(getattr(record, "source_path", ""))).name
        elif field == "tags":
            text = " ".join(str(tag) for tag in (getattr(record, "tags", []) or []))
        elif field == "confidence":
            return self._matches_confidence(record, value)
        else:
            text = str(getattr(record, field, "") or "")
        return self._token_prefix_match(text, value)

    def _matches_confidence(self, record, value: str) -> bool:
        try:
            conf = float(getattr(record, "confidence", 0.0) or 0.0)
        except (TypeError, ValueError):
            return False
        value = value.strip()
        if "-" in value:
            left, right = value.split("-", 1)
            try:
                return float(left) <= conf <= float(right)
            except ValueError:
                return False
        try:
            return conf >= float(value)
        except ValueError:
            return False

    @staticmethod
    def _strip_quotes(value: str) -> str:
        value = (value or "").strip()
        if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
            return value[1:-1]
        return value

    @staticmethod
    def _token_prefix_match(text: str, query: str) -> bool:
        terms = _query_terms(query or "")
        if not terms:
            return True
        hay_terms = _text_terms(text or "")
        return all(any(candidate.startswith(term) for candidate in hay_terms) for term in terms)


@lru_cache(maxsize=2048)
def _query_terms(query: str) -> tuple[str, ...]:
    return tuple(term.lower() for term in _TERM_RE.findall(query or ""))


@lru_cache(maxsize=8192)
def _text_terms(text: str) -> tuple[str, ...]:
    return tuple(term.lower() for term in _TERM_RE.findall(text or ""))


def parse_query_groups(query_text: str) -> list[list[str]]:
    return [list(group) for group in _parse_query_groups_cached(query_text or "")]


@lru_cache(maxsize=512)
def _parse_query_groups_cached(query_text: str) -> tuple[tuple[str, ...], ...]:
    tokens = _split_query_tokens_cached(query_text or "")
    groups = [[]]
    current = []
    for token in tokens:
        marker = token.lower()
        if marker in {"or", "|"}:
            if current:
                groups[-1].append(" ".join(current).strip())
                current = []
            if groups[-1]:
                groups.append([])
            continue
        if marker in {"and", ",", "&"}:
            if current:
                groups[-1].append(" ".join(current).strip())
                current = []
            continue
        current.append(token)
    if current:
        groups[-1].append(" ".join(current).strip())
    return tuple(tuple(group) for group in groups if group)


def split_field_term(term: str):
    return _split_field_term_cached(term or "")


@lru_cache(maxsize=2048)
def _split_field_term_cached(term: str):
    in_quote = False
    for idx, ch in enumerate(term):
        if ch == '"':
            in_quote = not in_quote
        elif not in_quote and ch in {":", "="}:
            prefix = term[:idx].strip().lower()
            if prefix:
                return prefix, term[idx + 1 :]
    return None


def split_query_tokens(query_text: str) -> list[str]:
    return list(_split_query_tokens_cached(query_text or ""))


@lru_cache(maxsize=512)
def _split_query_tokens_cached(query_text: str) -> tuple[str, ...]:
    tokens = []
    current = []
    in_quote = False
    for ch in (query_text or ""):
        if ch == '"':
            in_quote = not in_quote
            current.append(ch)
            continue
        if not in_quote and ch in {",", "|", "&"}:
            if current:
                tokens.append("".join(current).strip())
                current = []
            tokens.append(ch)
            continue
        if not in_quote and ch.isspace():
            if current:
                tokens.append("".join(current).strip())
                current = []
            continue
        current.append(ch)
    if current:
        tokens.append("".join(current).strip())
    return tuple(token for token in tokens if token)
