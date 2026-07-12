from __future__ import annotations

from pathlib import Path

from unshuffle.core.hashing import get_fast_hash, get_file_hash, is_fast_hash
from unshuffle.core.tags import normalize_tags


DUPLICATE_SHADOW_TAG = "duplicate"


def _record_fast_hash(rec):
    return getattr(rec, "fast_hash", None) or (
        getattr(rec, "hash", None) if is_fast_hash(getattr(rec, "hash", None)) else None
    )


def _record_full_hash(rec):
    file_hash = getattr(rec, "hash", None)
    return file_hash if file_hash and not is_fast_hash(file_hash) else None


def _empty_dedupe_index():
    return {
        "full_hashes": set(),
        "full_hash_records": {},
        "fast_hash_records": {},
    }


def _dedupe_index(existing):
    if isinstance(existing, dict) and "full_hashes" in existing and "fast_hash_records" in existing:
        existing.setdefault("full_hash_records", {})
        return existing
    index = _empty_dedupe_index()
    for key in existing or ():
        if isinstance(key, tuple) and len(key) == 2 and key[0] == "hash":
            index["full_hashes"].add(key[1])
    return index


def add_record_to_dedupe_index(index, rec) -> None:
    if is_duplicate_shadow(rec):
        return
    full_hash = _record_full_hash(rec)
    fast_hash = _record_fast_hash(rec)
    if full_hash:
        index["full_hashes"].add(full_hash)
        index.setdefault("full_hash_records", {}).setdefault(full_hash, rec)
    if fast_hash:
        index["fast_hash_records"].setdefault(fast_hash, []).append(rec)


def build_dedupe_index(records=None):
    index = _empty_dedupe_index()
    for record in records or ():
        add_record_to_dedupe_index(index, record)
    return index


def _promote_record_hash(rec):
    full_hash = _record_full_hash(rec)
    if full_hash:
        return full_hash
    try:
        full_hash = get_file_hash(rec.source_path)
    except OSError:
        full_hash = None
    if full_hash:
        rec.hash = full_hash
    return full_hash


def _ensure_record_fast_hash(rec):
    fast_hash = _record_fast_hash(rec)
    if fast_hash:
        return fast_hash
    try:
        fast_hash = get_fast_hash(rec.source_path)
    except OSError:
        fast_hash = None
    if fast_hash:
        rec.fast_hash = fast_hash
    return fast_hash


def _matching_existing_fast_hash_record(rec, candidates):
    incoming_full_hash = None
    for candidate in candidates:
        candidate_full_hash = _record_full_hash(candidate)
        if candidate_full_hash is None:
            candidate_full_hash = _promote_record_hash(candidate)
        if candidate_full_hash is None:
            continue
        if incoming_full_hash is None:
            incoming_full_hash = _promote_record_hash(rec)
        if incoming_full_hash and incoming_full_hash == candidate_full_hash:
            return candidate
    return None


def is_duplicate_shadow(rec) -> bool:
    return getattr(rec, "is_duplicate_shadow", False) is True


def _shadow_hash_for_record(rec) -> str | None:
    return _record_full_hash(rec) or _record_fast_hash(rec) or getattr(rec, "hash", None)


def _set_duplicate_shadow_evidence(rec, canonical_hash: str | None, canonical_path: Path | None) -> None:
    evidence = getattr(rec, "evidence", None)
    if not isinstance(evidence, dict):
        evidence = {}
        rec.evidence = evidence
    evidence["duplicate_shadow"] = {
        "is_shadow": True,
        "duplicate_of_hash": canonical_hash,
        "duplicate_of_path": str(canonical_path) if canonical_path else None,
    }


def _clear_duplicate_shadow_evidence(rec) -> None:
    evidence = getattr(rec, "evidence", None)
    if isinstance(evidence, dict):
        evidence.pop("duplicate_shadow", None)


def _with_duplicate_tag(tags) -> list[str]:
    return normalize_tags([*(tags or []), DUPLICATE_SHADOW_TAG])


def _without_duplicate_tag(tags) -> list[str]:
    return normalize_tags(tag for tag in (tags or []) if str(tag).lower() != DUPLICATE_SHADOW_TAG)


def mark_duplicate_shadow(rec, canonical=None, *, canonical_hash: str | None = None, canonical_path: Path | None = None):
    if canonical is not None:
        canonical_hash = canonical_hash or _shadow_hash_for_record(canonical)
        canonical_path = canonical_path or getattr(canonical, "source_path", None)
        for attr in (
            "pack",
            "category",
            "subcategory",
            "audio_type",
            "confidence",
            "duration",
            "pack_candidates",
            "feature_vector",
            "acoustic_vector",
            "feature_space_version",
            "feature_schema_json",
            "analysis_status",
            "analysis_tags_json",
        ):
            if hasattr(canonical, attr):
                setattr(rec, attr, getattr(canonical, attr))
    rec.is_duplicate_shadow = True
    rec.duplicate_of_hash = canonical_hash
    rec.duplicate_of_path = Path(canonical_path) if canonical_path else None
    rec.tags = _with_duplicate_tag(getattr(rec, "tags", []) or [])
    _set_duplicate_shadow_evidence(rec, rec.duplicate_of_hash, rec.duplicate_of_path)
    return rec


def clear_duplicate_shadow(rec) -> None:
    rec.is_duplicate_shadow = False
    rec.duplicate_of_hash = None
    rec.duplicate_of_path = None
    rec.tags = _without_duplicate_tag(getattr(rec, "tags", []) or [])
    _clear_duplicate_shadow_evidence(rec)


def buildable_records(records):
    store = getattr(records, "store", None)
    if store is not None and hasattr(store, "iter_buildable_records"):
        from .staging_session_store import BuildableDbRecordSequence

        return BuildableDbRecordSequence(store)
    return [rec for rec in records or [] if not is_duplicate_shadow(rec)]


def _path_under_root(path, root: Path) -> bool:
    try:
        resolved = Path(path).resolve()
    except OSError:
        resolved = Path(path)
    try:
        root_resolved = root.resolve()
    except OSError:
        root_resolved = root
    resolved_text = resolved.as_posix().lower()
    root_text = root_resolved.as_posix().lower()
    return resolved_text == root_text or resolved_text.startswith(root_text + "/")


def promote_duplicate_shadows_after_removal(records, removed_root: Path) -> int:
    promoted = 0
    canonical_by_key = {}
    shadows_by_key = {}
    removed_keys = set()

    for rec in records or []:
        rec_hash = _shadow_hash_for_record(rec)
        if rec_hash and not is_duplicate_shadow(rec):
            canonical_by_key[rec_hash] = rec
        if is_duplicate_shadow(rec):
            key = getattr(rec, "duplicate_of_hash", None) or rec_hash
            if key:
                shadows_by_key.setdefault(key, []).append(rec)
                duplicate_path = getattr(rec, "duplicate_of_path", None)
                if duplicate_path and _path_under_root(duplicate_path, removed_root):
                    removed_keys.add(key)

    for key in removed_keys:
        if key in canonical_by_key:
            continue
        shadows = shadows_by_key.get(key) or []
        if not shadows:
            continue
        promoted_rec = shadows[0]
        clear_duplicate_shadow(promoted_rec)
        promoted += 1
        promoted_hash = _shadow_hash_for_record(promoted_rec) or key
        for shadow in shadows[1:]:
            mark_duplicate_shadow(shadow, promoted_rec, canonical_hash=promoted_hash, canonical_path=promoted_rec.source_path)
    return promoted


def record_dedupe_key(rec):
    full_hash = _record_full_hash(rec)
    if full_hash:
        return ("hash", full_hash)
    fast_hash = _record_fast_hash(rec)
    if fast_hash:
        return ("fast_hash", fast_hash)
    try:
        stat = rec.source_path.stat()
        return ("fallback", rec.source_path.name.lower(), int(stat.st_size))
    except OSError:
        return ("path", str(rec.source_path).lower())


def dedupe_plan_records(plan, existing_hashes, lib_hashes):
    dedupe_index = _dedupe_index(existing_hashes)
    existing_full_hashes = set(dedupe_index["full_hashes"])
    new_records = []
    lib_dupe_count = 0
    session_dupe_count = 0

    for rec in plan:
        full_hash = _record_full_hash(rec)
        fast_hash = _record_fast_hash(rec)
        if full_hash and full_hash in dedupe_index["full_hashes"]:
            mark_duplicate_shadow(rec, dedupe_index.get("full_hash_records", {}).get(full_hash), canonical_hash=full_hash)
            new_records.append(rec)
            session_dupe_count += 1
            continue
        if full_hash and not fast_hash and dedupe_index["fast_hash_records"]:
            fast_hash = _ensure_record_fast_hash(rec)
        fast_hash_candidates = dedupe_index["fast_hash_records"].get(fast_hash, []) if fast_hash else []
        if fast_hash and fast_hash_candidates:
            candidate = _matching_existing_fast_hash_record(rec, fast_hash_candidates)
            if candidate is not None:
                mark_duplicate_shadow(rec, candidate)
                new_records.append(rec)
                session_dupe_count += 1
                continue
        elif fast_hash and not full_hash and existing_full_hashes:
            full_hash = _promote_record_hash(rec)
            if full_hash and full_hash in existing_full_hashes:
                mark_duplicate_shadow(rec, dedupe_index.get("full_hash_records", {}).get(full_hash), canonical_hash=full_hash)
                new_records.append(rec)
                session_dupe_count += 1
                continue
        if full_hash and full_hash in dedupe_index["full_hashes"]:
            mark_duplicate_shadow(rec, dedupe_index.get("full_hash_records", {}).get(full_hash), canonical_hash=full_hash)
            new_records.append(rec)
            session_dupe_count += 1
            continue
        hash_values = {value for value in (getattr(rec, "hash", None), getattr(rec, "fast_hash", None)) if value}
        if hash_values.intersection(lib_hashes):
            mark_duplicate_shadow(rec, canonical_hash=next(iter(hash_values.intersection(lib_hashes)), None))
            new_records.append(rec)
            lib_dupe_count += 1
            continue
        new_records.append(rec)
        add_record_to_dedupe_index(dedupe_index, rec)

    return new_records, lib_dupe_count, session_dupe_count


def scan_duplicate_stats(plan, new_records, lib_dupe_count: int, session_dupe_count: int) -> dict:
    return {
        "total_scanned": len(plan),
        "added_count": len(buildable_records(new_records)),
        "lib_dupe_count": lib_dupe_count,
        "session_dupe_count": session_dupe_count,
        "total_dupe_count": lib_dupe_count + session_dupe_count,
    }


def build_result_summary(result: dict) -> str:
    total = _result_total(result)
    copied = result.get("copied", 0)
    fallback_copies = int(result.get("fallback_copies", 0) or 0)
    duplicates = result.get("duplicates", 0)
    skipped_duplicates = int(result.get("skipped_duplicates", 0) or 0)
    shadow_duplicates = int(result.get("shadow_duplicates", 0) or 0)
    failed = result.get("failed", 0)
    stale = result.get("stale", 0)
    interrupted = result.get("interrupted", 0)
    if result.get("move"):
        moved = int(result.get("display_committed", max(0, copied - fallback_copies)) or 0)
        summary = f"Moved {moved} of {total} files."
        if fallback_copies:
            summary += (
                f" Copied {fallback_copies} hardlinked file(s) instead; "
                "their originals remain in the source."
            )
    else:
        copied_display = int(result.get("display_committed", copied) or 0)
        summary = f"Copied {copied_display} of {total} files."
    if duplicates:
        summary += f" Skipped {duplicates} duplicates."
    if skipped_duplicates:
        summary += (
            f" {skipped_duplicates} duplicate source file(s) were skipped during scan "
            "and left in place; they are not part of this undo session."
        )
    if shadow_duplicates:
        summary += (
            f" {shadow_duplicates} duplicate source file(s) were shown in the session "
            "but excluded from build."
        )
    if failed:
        summary += f" Failed {failed}."
    if stale:
        summary += f" Stale {stale}."
    if interrupted:
        summary += f" Interrupted {interrupted}."
    return summary


def build_result_lines(result: dict) -> list[str]:
    summary = build_result_summary(result)
    raw_lines = [part.strip() for part in summary.split(". ") if part.strip()]
    lines = []
    for line in raw_lines:
        lines.append(line if line.endswith(".") else f"{line}.")
    return lines or [summary]


def _result_total(result: dict) -> int:
    total = int(result.get("display_total", result.get("total", 0)) or 0)
    if total:
        return total
    return (
        int(result.get("copied", 0) or 0)
        + int(result.get("duplicates", 0) or 0)
        + int(result.get("failed", 0) or 0)
        + int(result.get("stale", 0) or 0)
        + int(result.get("interrupted", 0) or 0)
    )


def build_result_compact_lines(result: dict) -> list[str]:
    total = _result_total(result)
    committed = int(result.get("display_committed", result.get("copied", 0)) or 0)
    if result.get("move"):
        committed = int(
            result.get(
                "display_committed",
                max(0, int(result.get("copied", 0) or 0) - int(result.get("fallback_copies", 0) or 0)),
            )
            or 0
        )
        action = "moved"
    else:
        action = "copied"

    lines = [f"{committed}/{total} {action}"]
    fallback_copies = int(result.get("fallback_copies", 0) or 0)
    if fallback_copies:
        lines.append(f"{fallback_copies} hardlinked file(s) copied instead")
    duplicates = int(result.get("duplicates", 0) or 0)
    if duplicates:
        lines.append(f"{duplicates} duplicate(s) skipped")
    skipped_duplicates = int(result.get("skipped_duplicates", 0) or 0)
    if skipped_duplicates:
        lines.append(
            f"{skipped_duplicates} duplicate source file(s) were skipped during scan; "
            "not part of this undo session"
        )
    shadow_duplicates = int(result.get("shadow_duplicates", 0) or 0)
    if shadow_duplicates:
        lines.append(f"{shadow_duplicates} duplicate source file(s) shown but excluded from build")
    failed = int(result.get("failed", 0) or 0)
    if failed:
        lines.append(f"{failed} failed")
    stale = int(result.get("stale", 0) or 0)
    if stale:
        lines.append(f"{stale} stale")
    interrupted = int(result.get("interrupted", 0) or 0)
    if interrupted:
        lines.append(f"{interrupted} interrupted")
    return lines


def undo_result_summary(result: dict) -> str:
    undone = int(result.get("undone", 0) or 0)
    already_undone = int(result.get("already_undone", 0) or 0)
    parts = [f"Undo complete. {undone} item(s) undone."]
    if already_undone:
        parts.append(f"{already_undone} item(s) were already undone.")
    if result.get("sidecar_cleanup_pending"):
        parts.append("Internal folder cleanup is pending because files are still in use.")
    return " ".join(parts)
