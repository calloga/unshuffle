from __future__ import annotations

from pathlib import Path


def retryable_failed_records(res: dict, records: list) -> list:
    failed_records = res.get("failed_records") or []
    failed_paths = {str(row.get("source_path") or "") for row in failed_records if row.get("source_path")}
    if not failed_paths:
        return []
    by_path = {}
    for record in records:
        try:
            key = str(Path(getattr(record, "source_path")).resolve())
        except (TypeError, OSError):
            key = str(getattr(record, "source_path", ""))
        by_path[key] = record
        by_path[str(getattr(record, "source_path", ""))] = record
    retry = []
    for failed_path in failed_paths:
        try:
            key = str(Path(failed_path).resolve())
        except OSError:
            key = failed_path
        record = by_path.get(key) or by_path.get(failed_path)
        if record is not None and Path(getattr(record, "source_path")).exists():
            retry.append(record)
    return retry


def build_error_message(res: dict, summary_lines: list[str], error: str, retry_records: list) -> str:
    message_parts = ["Build completed with errors.", "", *summary_lines, "", error]
    failed_records = res.get("failed_records") or []
    if failed_records:
        failed_count = int(res.get("failed_record_count") or len(failed_records) or 0)
        message_parts.extend(["", f"Failed file{'s' if failed_count != 1 else ''}:"])
        for row in failed_records[:10]:
            source = str(row.get("source_path") or row.get("file_name") or "").strip()
            target = str(row.get("target_path") or "").strip()
            if target:
                message_parts.append(f"- {source}\n  -> {target}")
            elif source:
                message_parts.append(f"- {source}")
        remaining = failed_count - min(failed_count, 10)
        if remaining > 0:
            message_parts.append(f"...and {remaining} more.")
    if retry_records:
        message_parts.extend(["", f"You can retry {len(retry_records)} failed file{'s' if len(retry_records) != 1 else ''} now."])
    return "\n".join(message_parts)

