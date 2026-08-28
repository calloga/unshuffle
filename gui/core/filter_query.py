from pathlib import Path, PureWindowsPath

from .search_engine import SearchEngine
from ..utils.constants import SEARCH_PREFIX_MAP, TREE_SKIP_FIELD_PREFIXES


CONFIDENCE_PREFIXES = {"conf", "confidence"}


def format_query_groups(groups: list[list[str]]) -> str:
    return " OR ".join(" AND ".join(terms) for terms in groups if terms)


def _parenthesized_query(groups: list[list[str]]) -> str:
    text = format_query_groups(groups)
    return f"({text})" if len(groups) > 1 else text


def combine_filter_queries(current: str, query: str, mode: str) -> str:
    current_groups = SearchEngine.parse_query_groups(current)
    query_groups = SearchEngine.parse_query_groups(query)
    if not current_groups:
        return format_query_groups(query_groups)
    if not query_groups:
        return format_query_groups(current_groups)
    if mode == "or":
        return format_query_groups([*current_groups, *query_groups])
    if mode == "and":
        return f"{_parenthesized_query(current_groups)} AND {_parenthesized_query(query_groups)}"
    return format_query_groups(query_groups)


def query_contains_token(current: str, query: str) -> bool:
    needles = [
        SearchEngine.canonicalize_term(term).lower()
        for group in SearchEngine.parse_query_groups(query)
        for term in group
    ]
    if not needles:
        return False
    current_terms = [
        SearchEngine.canonicalize_term(term).lower()
        for group in SearchEngine.parse_query_groups(current)
        for term in group
    ]
    return all(needle in current_terms for needle in needles)


def remove_filter_query(current: str, query: str) -> str:
    needles = {
        SearchEngine.canonicalize_term(term).lower()
        for group in SearchEngine.parse_query_groups(query)
        for term in group
    }
    rebuilt_groups = []
    for group in SearchEngine.parse_query_groups(current):
        terms = [
            term for term in group
            if SearchEngine.canonicalize_term(term).lower() not in needles
        ]
        if terms:
            rebuilt_groups.append(terms)
    return format_query_groups(rebuilt_groups)


def field_filter_values(query: str, prefix: str) -> list[str]:
    canonical_prefix = SEARCH_PREFIX_MAP.get(str(prefix or "").strip().lower(), str(prefix or "").strip().lower())
    values = []
    for group in SearchEngine.parse_query_groups(query):
        for term in group:
            field = SearchEngine._split_field_term(term)
            if not field:
                continue
            term_prefix, value = field
            if SEARCH_PREFIX_MAP.get(term_prefix, term_prefix) != canonical_prefix:
                continue
            normalized = value.strip().strip('"')
            if normalized and normalized not in values:
                values.append(normalized)
    return values


def replace_field_filter(query: str, prefix: str, value: str | None) -> str:
    canonical_prefix = SEARCH_PREFIX_MAP.get(str(prefix or "").strip().lower(), str(prefix or "").strip().lower())
    rebuilt_groups: list[list[str]] = []
    for group in SearchEngine.parse_query_groups(query):
        terms = []
        for term in group:
            field = SearchEngine._split_field_term(term)
            if field and SEARCH_PREFIX_MAP.get(field[0], field[0]) == canonical_prefix:
                continue
            terms.append(term)
        if terms:
            rebuilt_groups.append(terms)

    selected = str(value or "").strip()
    if not selected:
        return format_query_groups(rebuilt_groups)

    token = f'{prefix}:"{selected}"'
    if not rebuilt_groups:
        return token
    return format_query_groups([[*group, token] for group in rebuilt_groups])


def active_saved_filter_queries_for_search(query_text: str, saved_filters: list[dict]) -> set[str]:
    return {
        str(filt.get("query", "")).strip()
        for filt in saved_filters
        if str(filt.get("query", "")).strip()
        and query_contains_token(query_text, str(filt.get("query", "")).strip())
    }


def active_source_filters_for_search(query_text: str) -> set[str]:
    sources = set()
    for group in SearchEngine.parse_query_groups(query_text):
        for term in group:
            canonical = SearchEngine.canonicalize_term(term)
            if not canonical.lower().startswith("source:"):
                continue
            value = canonical.split(":", 1)[1].strip().strip('"')
            if value:
                sources.add(value)
    return sources


def active_categories_for_search(query_text: str) -> set[str]:
    categories = set()
    for group in SearchEngine.parse_query_groups(query_text):
        for term in group:
            canonical = SearchEngine.canonicalize_term(term)
            if canonical.lower().startswith("category:"):
                value = canonical.split(":", 1)[1].strip().strip('"')
                if value:
                    categories.add(value)
    return categories


def active_confidence_range_for_search(query_text: str) -> tuple[float, float] | None:
    for group in SearchEngine.parse_query_groups(query_text):
        for term in group:
            canonical = SearchEngine.canonicalize_term(term)
            if not canonical.lower().startswith("confidence:"):
                continue
            value = canonical.split(":", 1)[1].strip().strip('"')
            parsed = _parse_confidence_range_value(value)
            if parsed is not None:
                return parsed
    return None


def remove_confidence_filters(query: str) -> str:
    rebuilt_groups = []
    for group in SearchEngine.parse_query_groups(query):
        terms = []
        for term in group:
            field = SearchEngine._split_field_term(term)
            if not field:
                terms.append(term)
                continue
            prefix, _value = field
            if prefix.lower() in CONFIDENCE_PREFIXES:
                continue
            canonical = SearchEngine.canonicalize_term(term)
            if canonical.lower().startswith("confidence:"):
                continue
            terms.append(term)
        if terms:
            rebuilt_groups.append(terms)
    return format_query_groups(rebuilt_groups)


def confidence_filter_query(min_val: float, max_val: float) -> str:
    min_pct = round(min_val * 100)
    max_pct = round(max_val * 100)
    return f'confidence:"{min_pct}-{max_pct}"'


def _parse_confidence_range_value(value: str) -> tuple[float, float] | None:
    text = (value or "").strip()
    if not text or "-" not in text:
        return None
    left, right = text.split("-", 1)
    try:
        low = max(0.0, min(1.0, float(left.strip().rstrip("%")) / 100.0))
        high = max(0.0, min(1.0, float(right.strip().rstrip("%")) / 100.0))
    except ValueError:
        return None
    if low > high:
        low, high = high, low
    return (low, high)


def tree_highlight_text(query: str) -> str:
    query = (query or "").strip()
    if not query:
        return ""
    for group in SearchEngine.parse_query_groups(query):
        for term in group:
            canonical = SearchEngine.canonicalize_term(term)
            if canonical.lower().startswith("confidence:"):
                continue
            first = term.strip()
            if ":" in first:
                first = first.split(":", 1)[1].strip()
            return first.strip('"')
    return ""


def tree_skip_fields(query: str) -> set[str]:
    prefixes = SearchEngine.active_prefixes(query or "")
    return {
        field_name
        for prefix, field_name in TREE_SKIP_FIELD_PREFIXES.items()
        if prefix in prefixes
    }


def source_filter_query(path: Path) -> str:
    return f'source:"{normalize_source_path_key(path)}"'


def normalize_source_path_key(path: Path | str) -> str:
    raw = str(path)
    windows_path = PureWindowsPath(raw)
    if windows_path.is_absolute():
        return windows_path.as_posix().lower()
    return Path(raw).resolve().as_posix().lower()
