from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

from ...core.assets import asset_path
from ...core.features import calculate_similarity_distance, vector_from_blob
from ...core.models import PlanRecord
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
) -> TaggingPassResult:
    """Compute generated secondary tags without mutating classification data."""
    candidates = load_genre_candidates(genre_metadata_path or GENRE_RELATIONSHIPS_PATH) if include_genres else []
    genres = infer_genres(records, candidates) if include_genres else {}
    duplicates = find_possible_duplicates(
        records,
        duplicate_threshold=duplicate_threshold,
        duration_window_seconds=duration_window_seconds,
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
) -> list[DuplicateMatch]:
    buckets: dict[tuple[int, tuple[float, ...]], list[tuple[str, list[float], float]]] = defaultdict(list)
    for rec in records:
        if _is_non_audio(rec):
            continue
        vec = vector_from_blob(getattr(rec, "feature_vector", None) or getattr(rec, "acoustic_vector", None))
        if not vec:
            continue
        duration = _vector_duration(vec, getattr(rec, "duration", 0.0))
        bucket = (_duration_bucket(duration, duration_window_seconds), _vector_signature(vec))
        buckets[bucket].append((_path_key(rec), vec, duration))

    matches: list[DuplicateMatch] = []
    for entries in buckets.values():
        if len(entries) < 2:
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
