import re
from pathlib import Path
from typing import Iterable


def _prefix_words(value: str) -> list[str]:
    clean = re.sub(r"[^a-zA-Z0-9\s\-_]", "", value or "")
    clean = re.sub(r"[\s\-_]+", "_", clean).strip("_")
    return [word for word in clean.split("_") if word]


def common_filename_tokens_for_packs(rows: Iterable[tuple[str, str]]) -> dict[str, frozenset[str]]:
    common: dict[str, set[str]] = {}
    for pack_name, source_path in rows:
        pack_key = str(pack_name or "").casefold()
        if not pack_key:
            continue
        filename_tokens = {
            token.casefold()
            for token in _prefix_words(Path(str(source_path or "")).stem)
        }
        if pack_key in common:
            common[pack_key].intersection_update(filename_tokens)
        else:
            common[pack_key] = filename_tokens
    return {pack: frozenset(tokens) for pack, tokens in common.items()}


def common_filename_tokens_by_pack(records: Iterable[object]) -> dict[str, frozenset[str]]:
    return common_filename_tokens_for_packs(
        (
            (str(getattr(record, "pack", "") or ""), str(getattr(record, "source_path", "") or ""))
            for record in records
            if not bool(getattr(record, "is_duplicate_shadow", False))
            and not bool(getattr(record, "is_preserved", False))
            and str(getattr(record, "audio_type", "") or "") not in {"Non-Audio Assets", "Utility"}
        )
    )


def get_pack_prefix(
    pack_name: str,
    _category: str = "",
    _audio_type: str = "",
    common_filename_tokens: Iterable[str] | None = None,
) -> str:
    if not pack_name:
        return ""

    shared = {str(token).casefold() for token in (common_filename_tokens or ())}
    words = [word for word in _prefix_words(pack_name) if word.casefold() not in shared]
    clean = "_".join(words)
    if not clean:
        return ""

    if len(clean) <= 30:
        return clean.upper()

    prefix_parts = [words[0][:5].upper()]
    for word in words[1:]:
        if word:
            prefix_parts.append(word[0].upper())

    return "".join(prefix_parts)
