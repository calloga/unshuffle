from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Sequence

from ...core.assets import asset_path
from ...core.features import calculate_similarity_distance, normalize_distance_vector, vector_from_blob
from ...core.models import PlanRecord
from ...core.progress import PhaseProgress
from ...core.tags import normalize_tags
from ...core.tokenizer import tokenize


POSSIBLE_DUPLICATE_TAG = "possibleduplicate"
GENRE_TAG_PREFIX = "genre:"
DEFAULT_DUPLICATE_DISTANCE = 0.025
DEFAULT_DURATION_WINDOW_SECONDS = 0.05
METADATA_DIR = asset_path("data", "metadata")
GENRE_RELATIONSHIPS_PATH = METADATA_DIR / "genre_relationships.json"


@dataclass(frozen=True)
class DuplicateMatch:
    left_path: str
    right_path: str
    distance: float


@dataclass(frozen=True)
class TaggingPassResult:
    tags_by_path: dict[str, list[str]] = field(default_factory=dict)
    genres_by_path: dict[str, str] = field(default_factory=dict)
    duplicate_matches: list[DuplicateMatch] = field(default_factory=list)

    @property
    def duplicate_file_count(self) -> int:
        paths = set()
        for match in self.duplicate_matches:
            paths.add(match.left_path)
            paths.add(match.right_path)
        return len(paths)

    @property
    def genre_file_count(self) -> int:
        return len(self.genres_by_path)


@dataclass(frozen=True)
class _GenreCandidate:
    label: str
    tag_value: str
    tokens: frozenset[str]
    padded_phrase: str = ""


def compute_tagging_pass(
    records: Sequence[PlanRecord],
    *,
    genre_metadata_path: Path | None = None,
    include_genres: bool = True,
    duplicate_threshold: float = DEFAULT_DUPLICATE_DISTANCE,
    duration_window_seconds: float = DEFAULT_DURATION_WINDOW_SECONDS,
    progress_callback: Callable[[dict], None] | None = None,
) -> TaggingPassResult:
    """Compute generated secondary tags without mutating classification data."""
    candidates = load_genre_candidates(genre_metadata_path or GENRE_RELATIONSHIPS_PATH) if include_genres else []
    genres = infer_genres(records, candidates) if include_genres else {}
    duplicates = find_possible_duplicates(
        records,
        duplicate_threshold=duplicate_threshold,
        duration_window_seconds=duration_window_seconds,
        progress_callback=progress_callback,
    )

    tags_by_path: dict[str, set[str]] = defaultdict(set)
    for path, genre in genres.items():
        tags_by_path[path].add(f"{GENRE_TAG_PREFIX}{_slug(genre)}")
    for match in duplicates:
        tags_by_path[match.left_path].add(POSSIBLE_DUPLICATE_TAG)
        tags_by_path[match.right_path].add(POSSIBLE_DUPLICATE_TAG)

    return TaggingPassResult(
        tags_by_path={path: sorted(tags) for path, tags in tags_by_path.items()},
        genres_by_path=genres,
        duplicate_matches=duplicates,
    )


def compute_db_duplicate_tags(
    db,
    session_id: str,
    *,
    duplicate_threshold: float = DEFAULT_DUPLICATE_DISTANCE,
    duration_window_seconds: float = DEFAULT_DURATION_WINDOW_SECONDS,
    progress_callback: Callable[[dict], None] | None = None,
) -> int:
    """Apply possible-duplicate tags using bounded SQLite-backed buckets."""
    conn = db.conn
    conn.execute("DROP TABLE IF EXISTS temp.tagging_candidates")
    conn.execute("DROP TABLE IF EXISTS temp.tagging_matches")
    conn.execute(
        """
        CREATE TEMP TABLE tagging_candidates (
            bucket_duration INTEGER NOT NULL,
            bucket_signature TEXT NOT NULL,
            row_id INTEGER NOT NULL,
            source_path TEXT NOT NULL,
            duration REAL NOT NULL,
            feature_vector BLOB NOT NULL,
            PRIMARY KEY (row_id)
        )
        """
    )
    conn.execute(
        "CREATE INDEX temp.idx_tagging_candidates_bucket "
        "ON tagging_candidates(bucket_duration, bucket_signature, row_id)"
    )
    conn.execute("CREATE TEMP TABLE tagging_matches (row_id INTEGER PRIMARY KEY)")
    total = int(conn.execute(
        "SELECT COUNT(*) FROM staging_records WHERE session_id = ?",
        (session_id,),
    ).fetchone()[0])
    bucket_progress = PhaseProgress(
        progress_callback,
        "Checking Possible Duplicates",
        total=max(1, total),
        message="Preparing possible duplicate groups...",
        update_every=500,
    )
    cursor = conn.execute(
        """
        SELECT row_id, source_path, duration, feature_vector
        FROM staging_records
        WHERE session_id = ?
          AND feature_vector IS NOT NULL
          AND COALESCE(is_preserved, 0) = 0
          AND COALESCE(audio_type, '') NOT IN ('Non-Audio Assets', 'Metadata')
          AND COALESCE(CASE WHEN json_valid(COALESCE(evidence_json, ''))
              THEN json_extract(evidence_json, '$.duplicate_shadow.is_shadow') ELSE 0 END, 0) != 1
        ORDER BY row_id
        """,
        (session_id,),
    )
    prepared = 0
    insert_rows = []
    for row in cursor:
        prepared += 1
        vector = vector_from_blob(row[3])
        if vector:
            duration = _vector_duration(vector, row[2] or 0.0)
            insert_rows.append((
                _duration_bucket(duration, duration_window_seconds),
                json.dumps(_vector_signature(vector), separators=(",", ":")),
                int(row[0]),
                str(row[1] or ""),
                duration,
                row[3],
            ))
        if len(insert_rows) >= 1000:
            conn.executemany("INSERT INTO tagging_candidates VALUES (?, ?, ?, ?, ?, ?)", insert_rows)
            insert_rows.clear()
        bucket_progress.emit(prepared)
    if insert_rows:
        conn.executemany("INSERT INTO tagging_candidates VALUES (?, ?, ?, ?, ?, ?)", insert_rows)
    bucket_progress.emit(max(1, total), force=True)

    bucket_count = int(conn.execute(
        """
        SELECT COUNT(*) FROM (
            SELECT 1 FROM tagging_candidates
            GROUP BY bucket_duration, bucket_signature HAVING COUNT(*) > 1
        )
        """
    ).fetchone()[0])
    compare_progress = PhaseProgress(
        progress_callback,
        "Checking Possible Duplicates",
        total=max(1, bucket_count),
        message=f"Checking {bucket_count} possible duplicate groups...",
        update_every=25,
    )
    buckets = conn.execute(
        """
        SELECT bucket_duration, bucket_signature, COUNT(*)
        FROM tagging_candidates
        GROUP BY bucket_duration, bucket_signature HAVING COUNT(*) > 1
        ORDER BY bucket_duration, bucket_signature
        """
    )
    for bucket_index, (duration_key, signature, count) in enumerate(buckets, 1):
        if int(count) <= 2000:
            entries = list(conn.execute(
                """
                SELECT row_id, source_path, duration, feature_vector
                FROM tagging_candidates
                WHERE bucket_duration = ? AND bucket_signature = ?
                ORDER BY source_path
                """,
                (duration_key, signature),
            ))
            decoded = [(int(row[0]), str(row[1]), float(row[2]), vector_from_blob(row[3])) for row in entries]
            for left_index, left in enumerate(decoded[:-1]):
                for right in decoded[left_index + 1:]:
                    _record_db_duplicate_match(
                        conn,
                        left,
                        right,
                        duplicate_threshold,
                        duration_window_seconds,
                    )
        else:
            _mark_large_bucket_ann(
                conn,
                duration_key,
                signature,
                int(count),
                duplicate_threshold,
                duration_window_seconds,
            )
        compare_progress.emit(bucket_index)
    compare_progress.emit(max(1, bucket_count), force=True)

    with db.write_transaction():
        conn.execute(
            """
            UPDATE staging_records
            SET tags = TRIM(REPLACE(' ' || COALESCE(tags, '') || ' ', ' possibleduplicate ', ' '))
            WHERE session_id = ? AND (' ' || LOWER(COALESCE(tags, '')) || ' ') LIKE '% possibleduplicate %'
            """,
            (session_id,),
        )
        conn.execute(
            """
            UPDATE staging_records
            SET tags = TRIM(COALESCE(tags, '') || ' possibleduplicate')
            WHERE session_id = ? AND row_id IN (SELECT row_id FROM tagging_matches)
            """,
            (session_id,),
        )
    duplicate_count = int(conn.execute("SELECT COUNT(*) FROM tagging_matches").fetchone()[0])
    conn.execute("DROP TABLE temp.tagging_candidates")
    conn.execute("DROP TABLE temp.tagging_matches")
    return duplicate_count


def _mark_large_bucket_ann(
    conn,
    duration_key: int,
    signature: str,
    count: int,
    duplicate_threshold: float,
    duration_window_seconds: float,
) -> None:
    try:
        import hnswlib
        import numpy as np
    except ModuleNotFoundError:
        _mark_large_bucket_exact(
            conn,
            duration_key,
            signature,
            duplicate_threshold,
            duration_window_seconds,
        )
        return

    dimension = None
    index = None
    cursor = conn.execute(
        """
        SELECT row_id, feature_vector FROM tagging_candidates
        WHERE bucket_duration = ? AND bucket_signature = ? ORDER BY row_id
        """,
        (duration_key, signature),
    )
    while True:
        rows = cursor.fetchmany(1000)
        if not rows:
            break
        labels = []
        vectors = []
        for row in rows:
            vector = vector_from_blob(row[1])
            if not vector:
                continue
            normalized = normalize_distance_vector(vector)
            if dimension is None:
                dimension = len(normalized)
                index = hnswlib.Index(space="l2", dim=dimension)
                index.init_index(max_elements=count, ef_construction=200, M=16)
            labels.append(int(row[0]))
            vectors.append(normalized)
        if vectors and index is not None:
            index.add_items(np.asarray(vectors, dtype=np.float32), np.asarray(labels, dtype=np.int64))
    if index is None or index.get_current_count() < 2:
        return
    index.set_ef(min(250, max(100, int(index.get_current_count()))))

    query_cursor = conn.execute(
        """
        SELECT row_id, source_path, duration, feature_vector FROM tagging_candidates
        WHERE bucket_duration = ? AND bucket_signature = ? ORDER BY row_id
        """,
        (duration_key, signature),
    )
    neighbor_count = min(100, int(index.get_current_count()))
    while True:
        rows = query_cursor.fetchmany(250)
        if not rows:
            break
        query_vectors = []
        valid_rows = []
        for row in rows:
            vector = vector_from_blob(row[3])
            if not vector:
                continue
            query_vectors.append(normalize_distance_vector(vector))
            valid_rows.append((int(row[0]), str(row[1]), float(row[2]), vector))
        if not query_vectors:
            continue
        labels, _distances = index.knn_query(np.asarray(query_vectors, dtype=np.float32), k=neighbor_count)
        for left, candidate_ids in zip(valid_rows, labels):
            ids = [int(value) for value in candidate_ids if int(value) != left[0]]
            if not ids:
                continue
            placeholders = ", ".join("?" for _ in ids)
            candidate_rows = conn.execute(
                f"SELECT row_id, source_path, duration, feature_vector FROM tagging_candidates "
                f"WHERE row_id IN ({placeholders})",
                ids,
            )
            for row in candidate_rows:
                right = (int(row[0]), str(row[1]), float(row[2]), vector_from_blob(row[3]))
                if _record_db_duplicate_match(
                    conn,
                    left,
                    right,
                    duplicate_threshold,
                    duration_window_seconds,
                ):
                    break


def _mark_large_bucket_exact(
    conn,
    duration_key: int,
    signature: str,
    duplicate_threshold: float,
    duration_window_seconds: float,
) -> None:
    left_rows = conn.execute(
        """
        SELECT row_id, source_path, duration, feature_vector FROM tagging_candidates
        WHERE bucket_duration = ? AND bucket_signature = ? ORDER BY source_path
        """,
        (duration_key, signature),
    )
    for left_row in left_rows:
        left = (int(left_row[0]), str(left_row[1]), float(left_row[2]), vector_from_blob(left_row[3]))
        right_rows = conn.execute(
            """
            SELECT row_id, source_path, duration, feature_vector FROM tagging_candidates
            WHERE bucket_duration = ? AND bucket_signature = ? AND source_path > ? ORDER BY source_path
            """,
            (duration_key, signature, left[1]),
        )
        for right_row in right_rows:
            right = (int(right_row[0]), str(right_row[1]), float(right_row[2]), vector_from_blob(right_row[3]))
            _record_db_duplicate_match(
                conn,
                left,
                right,
                duplicate_threshold,
                duration_window_seconds,
            )


def _record_db_duplicate_match(conn, left, right, threshold: float, duration_window: float) -> bool:
    left_id, _left_path, left_duration, left_vector = left
    right_id, _right_path, right_duration, right_vector = right
    if left_vector is None or right_vector is None:
        return False
    if abs(left_duration - right_duration) > max(duration_window, 0.001):
        return False
    distance = calculate_similarity_distance(
        left_vector,
        right_vector,
        d1=left_duration,
        d2=right_duration,
    )
    if math.isfinite(distance) and distance <= threshold:
        conn.execute("INSERT OR IGNORE INTO tagging_matches(row_id) VALUES (?), (?)", (left_id, right_id))
        return True
    return False


def load_genre_candidates(path: Path) -> list[_GenreCandidate]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []

    candidates: dict[str, _GenreCandidate] = {}
    for label in _iter_genre_labels(payload):
        tokens = frozenset(tokenize(label))
        if not tokens:
            continue
        key = _slug(label)
        candidates[key] = _GenreCandidate(
            label=_display_label(label),
            tag_value=key,
            tokens=tokens,
            padded_phrase=f" {key.replace('_', ' ')} "
        )
    return sorted(candidates.values(), key=lambda item: (len(item.tokens), item.label), reverse=True)


def infer_genres(records: Sequence[PlanRecord], candidates: Sequence[_GenreCandidate]) -> dict[str, str]:
    if not candidates:
        return {}
    result: dict[str, str] = {}
    for rec in records:
        if _is_non_audio(rec):
            continue
        text = _record_genre_text(rec)
        record_tokens = set(tokenize(text))
        if not record_tokens:
            continue
        normalized_text = f" {_slug(text).replace('_', ' ')} "
        best: tuple[float, int, _GenreCandidate] | None = None
        for candidate in candidates:
            overlap = record_tokens & candidate.tokens
            if not overlap:
                continue
            if len(candidate.tokens) == 1 and not _allow_single_token_genre(candidate, normalized_text):
                continue
            coverage = len(overlap) / len(candidate.tokens)
            phrase_bonus = 1.0 if candidate.padded_phrase in normalized_text else 0.0
            score = (len(overlap) * 2.0) + coverage + phrase_bonus
            current = (score, len(candidate.tokens), candidate)
            if best is None or current[:2] > best[:2]:
                best = current
        if best is not None and best[0] >= 2.5:
            result[_path_key(rec)] = best[2].label
    return result


def find_possible_duplicates(
    records: Sequence[PlanRecord],
    *,
    duplicate_threshold: float = DEFAULT_DUPLICATE_DISTANCE,
    duration_window_seconds: float = DEFAULT_DURATION_WINDOW_SECONDS,
    progress_callback: Callable[[dict], None] | None = None,
) -> list[DuplicateMatch]:
    buckets: dict[tuple[int, tuple[float, ...]], list[tuple[str, list[float], float]]] = defaultdict(list)
    bucket_progress = PhaseProgress(
        progress_callback,
        "Checking Possible Duplicates",
        total=len(records),
        message="Preparing possible duplicate groups...",
        update_every=500,
    )
    bucket_progress.emit(0, force=True)
    for index, rec in enumerate(records, 1):
        if _is_non_audio(rec):
            bucket_progress.emit(index)
            continue
        vec = vector_from_blob(getattr(rec, "feature_vector", None) or getattr(rec, "acoustic_vector", None))
        if not vec:
            bucket_progress.emit(index)
            continue
        duration = _vector_duration(vec, getattr(rec, "duration", 0.0))
        bucket = (_duration_bucket(duration, duration_window_seconds), _vector_signature(vec))
        buckets[bucket].append((_path_key(rec), vec, duration))
        bucket_progress.emit(index)
    bucket_progress.emit(len(records), force=True)

    matches: list[DuplicateMatch] = []
    compare_progress = PhaseProgress(
        progress_callback,
        "Checking Possible Duplicates",
        total=max(1, len(buckets)),
        message=f"Checking {len(buckets)} possible duplicate groups...",
        update_every=100,
    )
    compare_progress.emit(0, force=True)
    for bucket_index, entries in enumerate(buckets.values(), 1):
        if len(entries) < 2:
            compare_progress.emit(bucket_index)
            continue
        entries = sorted(entries, key=lambda item: item[0])
        for left_index, (left_path, left_vec, left_duration) in enumerate(entries[:-1]):
            for right_path, right_vec, right_duration in entries[left_index + 1:]:
                if abs(left_duration - right_duration) > max(duration_window_seconds, 0.001):
                    continue
                distance = calculate_similarity_distance(
                    left_vec,
                    right_vec,
                    d1=left_duration,
                    d2=right_duration,
                )
                if math.isfinite(distance) and distance <= duplicate_threshold:
                    matches.append(DuplicateMatch(left_path, right_path, round(distance, 6)))
        compare_progress.emit(bucket_index)
    compare_progress.emit(max(1, len(buckets)), force=True)
    return sorted(matches, key=lambda item: (item.left_path, item.right_path))


def generated_tag_set(tags: Iterable[str]) -> set[str]:
    generated = set()
    for tag in tags or []:
        value = (tag or "").strip()
        key = value.lower()
        if key == POSSIBLE_DUPLICATE_TAG or key.startswith(GENRE_TAG_PREFIX):
            generated.add(value)
    return generated


def merge_generated_tags(existing_tags: Iterable[str], generated_tags: Iterable[str]) -> list[str]:
    kept = [
        tag
        for tag in normalize_tags(existing_tags or [])
        if tag.lower() != POSSIBLE_DUPLICATE_TAG and not tag.lower().startswith(GENRE_TAG_PREFIX)
    ]
    return normalize_tags([*kept, *generated_tags])


def genre_from_tags(tags: Iterable[str]) -> str:
    for tag in tags or []:
        value = (tag or "").strip()
        if value.lower().startswith(GENRE_TAG_PREFIX):
            return _display_label(value.split(":", 1)[1])
    return ""


def _iter_genre_labels(value) -> Iterable[str]:
    ignored_keys = {"music", "metadata_schema", "fields"}
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key or "")
            if key_text not in ignored_keys:
                yield _display_label(key_text)
            yield from _iter_genre_labels(child)
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, str):
                yield _display_label(item)
            else:
                yield from _iter_genre_labels(item)
    elif isinstance(value, str):
        yield _display_label(value)


def _record_genre_text(rec: PlanRecord) -> str:
    path = getattr(rec, "source_path", None)
    if not isinstance(path, Path):
        path = Path(str(path or ""))
    pack = str(getattr(rec, "pack", "") or "")
    parts = [part for part in path.parts if part and part not in {path.anchor}]
    pack_key = pack.lower()
    start = 0
    for idx, part in enumerate(parts):
        if pack_key and part.lower() == pack_key:
            start = idx
            break
    else:
        start = max(0, len(parts) - 6)
    scoped_parts = parts[start:]
    return " ".join([pack, *scoped_parts, path.stem])


def _allow_single_token_genre(candidate: _GenreCandidate, normalized_text: str) -> bool:
    token = next(iter(candidate.tokens), "")
    return len(token) >= 4 and f" {token} " in normalized_text


def _vector_duration(vec: Sequence[float], fallback: float) -> float:
    if len(vec) >= 18:
        try:
            value = vec[17]
            if value > 0:
                return value
        except (TypeError, ValueError):
            pass
    try:
        return (fallback or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _duration_bucket(duration: float, window: float) -> int:
    window = max((window or DEFAULT_DURATION_WINDOW_SECONDS), 0.001)
    return round((duration or 0.0) / window)


def _vector_signature(vec: Sequence[float]) -> tuple[float, ...]:
    return tuple(round(value, 2) for value in vec)


def _path_key(rec: PlanRecord) -> str:
    path = getattr(rec, "source_path", "")
    return str(path).replace("\\", "/")


def _slug(value: str) -> str:
    tokens = tokenize((value or ""), flatten=False)
    return "_".join(tokens)


def _display_label(value: str) -> str:
    text = re.sub(r"[_-]+", " ", (value or "")).strip()
    text = re.sub(r"\s+", " ", text)
    return text.title()


def _is_non_audio(rec: PlanRecord) -> bool:
    return str(getattr(rec, "audio_type", "") or "") in {"Non-Audio Assets", "Metadata"}
