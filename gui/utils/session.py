from pathlib import Path

from unshuffle.core import PlanRecord, plan_records_from_staging_rows, parse_tags
from unshuffle.core.constants import IGNORED_SYSTEM_ARTIFACT_NAMES, RESERVED_NAMES


def _is_reserved_staging_path(row: dict) -> bool:
    source_path = str(row.get("source_path") or "")
    if not source_path:
        return False
    reserved = {str(name).casefold() for name in RESERVED_NAMES}
    reserved.update(str(name).casefold() for name in IGNORED_SYSTEM_ARTIFACT_NAMES)
    return any(part.casefold() in reserved for part in Path(source_path).parts)


def plan_records_from_staging(rows: list[dict]) -> list[PlanRecord]:
    visible_rows = [row for row in rows if not _is_reserved_staging_path(row)]
    return plan_records_from_staging_rows(visible_rows, parse_tags)
