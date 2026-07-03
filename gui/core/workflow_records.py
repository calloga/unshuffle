from __future__ import annotations

from unshuffle.core.hashing import get_fast_hash, get_file_hash, is_fast_hash


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
        "fast_hash_records": {},
    }


def _dedupe_index(existing):
    if isinstance(existing, dict) and "full_hashes" in existing and "fast_hash_records" in existing:
        return existing
    index = _empty_dedupe_index()
    for key in existing or ():
        if isinstance(key, tuple) and len(key) == 2 and key[0] == "hash":
            index["full_hashes"].add(key[1])
    return index


def add_record_to_dedupe_index(index, rec) -> None:
    full_hash = _record_full_hash(rec)
    fast_hash = _record_fast_hash(rec)
    if full_hash:
        index["full_hashes"].add(full_hash)
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


def _matches_existing_fast_hash(rec, candidates) -> bool:
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
            return True
    return False


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
            session_dupe_count += 1
            continue
        if full_hash and not fast_hash and dedupe_index["fast_hash_records"]:
            fast_hash = _ensure_record_fast_hash(rec)
        fast_hash_candidates = dedupe_index["fast_hash_records"].get(fast_hash, []) if fast_hash else []
        if fast_hash and fast_hash_candidates:
            if _matches_existing_fast_hash(rec, fast_hash_candidates):
                session_dupe_count += 1
                continue
        elif fast_hash and not full_hash and existing_full_hashes:
            full_hash = _promote_record_hash(rec)
            if full_hash and full_hash in existing_full_hashes:
                session_dupe_count += 1
                continue
        if full_hash and full_hash in dedupe_index["full_hashes"]:
            session_dupe_count += 1
            continue
        hash_values = {value for value in (getattr(rec, "hash", None), getattr(rec, "fast_hash", None)) if value}
        if hash_values.intersection(lib_hashes):
            lib_dupe_count += 1
            continue
        new_records.append(rec)
        add_record_to_dedupe_index(dedupe_index, rec)

    return new_records, lib_dupe_count, session_dupe_count


def scan_duplicate_stats(plan, new_records, lib_dupe_count: int, session_dupe_count: int) -> dict:
    return {
        "total_scanned": len(plan),
        "added_count": len(new_records),
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
