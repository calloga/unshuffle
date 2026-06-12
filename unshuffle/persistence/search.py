import logging
import re
from collections.abc import Callable
from typing import List, NoReturn, Set

from ..core.features import calculate_similarity_distance, vector_from_blob
from ..core.constants import SIMILARITY_THRESHOLD


_FTS_TERM_RE = re.compile(r"\w+", re.UNICODE)
_FTS_RESERVED = {"AND", "OR", "NOT", "NEAR"}


class SearchExecutionError(RuntimeError):
    """Raised when a staging search cannot be executed safely."""


def _raise_search_error(label: str, query: str, exc: Exception) -> NoReturn:
    logging.error("%s: %s | Query: %s", label, exc, query)
    raise SearchExecutionError(f"{label}: {exc}") from exc


def _split_search_parts(query_text: str) -> List[str]:
    parts = []
    current = []
    in_quote = False
    for ch in query_text:
        if ch == '"':
            in_quote = not in_quote
            current.append(ch)
        elif ch == "," and not in_quote:
            part = "".join(current).strip()
            if part:
                parts.append(part)
            current = []
        else:
            current.append(ch)
    part = "".join(current).strip()
    if part:
        parts.append(part)
    return parts


def _fts_value_query(prefix: str, value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    terms = _extract_fts_terms(value)
    if not terms:
        return ""
    if len(terms) == 1:
        return f"{prefix} : {_fts_term_query(terms[0])}"
    joined = " AND ".join(_fts_term_query(term) for term in terms)
    return f"{prefix} : ( {joined} )"


def _fts_standard_query(value: str) -> str:
    terms = _extract_fts_terms(value)
    return " AND ".join(_fts_term_query(term) for term in terms)


def _extract_fts_terms(value: str) -> List[str]:
    return [term for term in _FTS_TERM_RE.findall(value) if term]


def _fts_term_query(term: str) -> str:
    escaped = term.replace('"', '""')
    quoted = f'"{escaped}"'
    if term.upper() in _FTS_RESERVED:
        return quoted
    return f"{quoted}*"


def _literal_like_prefix(value: str) -> tuple[str, str]:
    escaped = (
        value.replace("\\", "/")
        .rstrip("/")
        .replace("!", "!!")
        .replace("%", "!%")
        .replace("_", "!_")
    )
    return escaped + "%", "!"


def search_similar_records(
    conn,
    session_id: str,
    target_id: int,
    limit: int = 50,
    candidate_ids: Set[int] | None = None,
) -> List[int]:
    cursor = conn.execute(
        "SELECT feature_vector, duration FROM staging_records WHERE session_id = ? AND row_id = ?",
        (session_id, target_id),
    )
    row = cursor.fetchone()
    if not row or not row["feature_vector"]:
        return []

    target_vec = vector_from_blob(row["feature_vector"])
    if not target_vec:
        return []

    target_duration = float(row["duration"] or 0.0)
    query = """
        SELECT row_id, feature_vector, duration
        FROM staging_records
        WHERE session_id = ?
          AND feature_vector IS NOT NULL
          AND audio_type NOT IN ('Non-Audio Assets', 'Metadata')
    """
    params: list[object] = [session_id]
    if candidate_ids is not None:
        if not candidate_ids:
            return []
        placeholders = ", ".join("?" for _ in candidate_ids)
        query += f" AND row_id IN ({placeholders})"
        params.extend(sorted(candidate_ids))

    cursor = conn.execute(query, params)
    results = []
    for record in cursor.fetchall():
        other_vec = vector_from_blob(record["feature_vector"])
        if not other_vec:
            continue
        dist = calculate_similarity_distance(
            target_vec,
            other_vec,
            d1=target_duration,
            d2=float(record["duration"] or 0.0),
        )
        results.append((record["row_id"], dist))
    results.sort(key=lambda item: item[1])
    return [item[0] for item in results[:limit] if item[1] < SIMILARITY_THRESHOLD]


def _resolve_search_part(
    conn,
    session_id: str,
    part_text: str,
    similar_lookup: Callable[..., List[int] | Set[int]],
    candidate_ids: Set[int] | None = None,
) -> List[int] | Set[int]:
    if part_text.startswith("similar:"):
        val = part_text.split(":", 1)[1]
        try:
            return similar_lookup(session_id, int(val), candidate_ids=candidate_ids)
        except (ValueError, IndexError):
            cursor = conn.execute(
                "SELECT row_id FROM staging_records WHERE session_id = ? AND sample_name = ? LIMIT 1",
                (session_id, val),
            )
            row = cursor.fetchone()
            if row:
                return similar_lookup(session_id, row[0], candidate_ids=candidate_ids)
            return set()

    if ":" in part_text:
        prefix, val = part_text.split(":", 1)
        prefix = prefix.strip()
        val = val.strip().strip('"')
        if prefix in {"path", "source", "source_path"}:
            pattern, escape = _literal_like_prefix(val)
            try:
                cursor = conn.execute(
                    """
                    SELECT row_id
                    FROM staging_records
                    WHERE session_id = ?
                      AND REPLACE(source_path, '\\', '/') LIKE ? ESCAPE ?
                """,
                    (session_id, pattern, escape),
                )
                return {row[0] for row in cursor.fetchall()}
            except Exception as exc:
                _raise_search_error("Path search failed", pattern, exc)

        f_query = _fts_value_query(prefix, val)
        if not f_query:
            return set()
        try:
            cursor = conn.execute(
                """
                SELECT s.row_id
                FROM staging_fts f
                JOIN staging_records s ON s.id = f.rowid
                WHERE f.session_id = ? AND f.staging_fts MATCH ?
                """,
                (session_id, f_query),
            )
            return {row[0] for row in cursor.fetchall()}
        except Exception as exc:
            _raise_search_error("FTS column search failed", f_query, exc)

    clean_query = _fts_standard_query(part_text)
    if not clean_query:
        return set()
    try:
        cursor = conn.execute(
            """
            SELECT s.row_id
            FROM staging_fts f
            JOIN staging_records s ON s.id = f.rowid
            WHERE f.session_id = ? AND f.staging_fts MATCH ?
            """,
            (session_id, clean_query),
        )
        return {row[0] for row in cursor.fetchall()}
    except Exception as exc:
        _raise_search_error("FTS standard search failed", clean_query, exc)


def search_staging(
    conn,
    session_id: str,
    query_text: str,
    similar_lookup: Callable[..., List[int] | Set[int]],
) -> List[int] | Set[int]:
    if not query_text:
        return set()

    parts = _split_search_parts(query_text)
    non_similarity_sets: list[Set[int]] = []
    for part_text in parts:
        if part_text.startswith("similar:"):
            continue
        resolved = _resolve_search_part(conn, session_id, part_text, similar_lookup)
        resolved_set = set(resolved)
        if not resolved_set:
            return set()
        non_similarity_sets.append(resolved_set)

    candidate_ids: Set[int] | None = None
    if non_similarity_sets:
        candidate_ids = set(non_similarity_sets[0])
        for subset in non_similarity_sets[1:]:
            candidate_ids &= set(subset)
        if not candidate_ids:
            return set()

    result_sets = []
    non_similarity_index = 0

    for part_text in parts:
        if part_text.startswith("similar:"):
            current_set = _resolve_search_part(
                conn,
                session_id,
                part_text,
                similar_lookup,
                candidate_ids=candidate_ids,
            )
        else:
            current_set = non_similarity_sets[non_similarity_index]
            non_similarity_index += 1

        if current_set:
            result_sets.append(current_set)
        else:
            return set()

    if not result_sets:
        return set()

    first_set = result_sets[0]
    other_sets = result_sets[1:]
    if isinstance(first_set, list):
        return [item_id for item_id in first_set if all(item_id in set(subset) for subset in other_sets)]

    result = set(first_set)
    for subset in other_sets:
        result &= set(subset)
    return result
