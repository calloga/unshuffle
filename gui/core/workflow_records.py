from __future__ import annotations


def record_dedupe_key(rec):
    if getattr(rec, "hash", None):
        return ("hash", rec.hash)
    try:
        stat = rec.source_path.stat()
        return ("fallback", rec.source_path.name.lower(), int(stat.st_size))
    except OSError:
        return ("path", str(rec.source_path).lower())


def dedupe_plan_records(plan, existing_hashes, lib_hashes):
    new_records = []
    lib_dupe_count = 0
    session_dupe_count = 0

    for rec in plan:
        key = record_dedupe_key(rec)
        if key in existing_hashes:
            session_dupe_count += 1
            continue
        if rec.hash and rec.hash in lib_hashes:
            lib_dupe_count += 1
            continue
        new_records.append(rec)
        existing_hashes.add(key)

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
    total = result.get("total", 0)
    copied = result.get("copied", 0)
    fallback_copies = int(result.get("fallback_copies", 0) or 0)
    duplicates = result.get("duplicates", 0)
    skipped_duplicates = int(result.get("skipped_duplicates", 0) or 0)
    failed = result.get("failed", 0)
    stale = result.get("stale", 0)
    interrupted = result.get("interrupted", 0)
    if result.get("move"):
        moved = max(0, copied - fallback_copies)
        summary = f"Moved {moved} of {total} files."
        if fallback_copies:
            summary += (
                f" Copied {fallback_copies} hardlinked file(s) instead; "
                "their originals remain in the source."
            )
    else:
        summary = f"Copied {copied} of {total} files."
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


def undo_result_summary(result: dict) -> str:
    undone = int(result.get("undone", 0) or 0)
    already_undone = int(result.get("already_undone", 0) or 0)
    parts = [f"Undo complete. {undone} item(s) undone."]
    if already_undone:
        parts.append(f"{already_undone} item(s) were already undone.")
    if result.get("sidecar_cleanup_pending"):
        parts.append("Internal folder cleanup is pending because files are still in use.")
    return " ".join(parts)
