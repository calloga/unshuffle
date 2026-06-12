"""Secondary metadata tagging helpers."""

from .service import (
    GENRE_TAG_PREFIX,
    POSSIBLE_DUPLICATE_TAG,
    DuplicateMatch,
    TaggingPassResult,
    compute_tagging_pass,
    generated_tag_set,
    genre_from_tags,
    merge_generated_tags,
)

__all__ = [
    "GENRE_TAG_PREFIX",
    "POSSIBLE_DUPLICATE_TAG",
    "DuplicateMatch",
    "TaggingPassResult",
    "compute_tagging_pass",
    "generated_tag_set",
    "genre_from_tags",
    "merge_generated_tags",
]
