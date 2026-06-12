"""CSV report setup for runtime execution."""

import csv
from pathlib import Path
from typing import TextIO

from ..persistence import get_system_dir


EXECUTION_REPORT_FIELDNAMES = [
    "sample_name",
    "source_filename",
    "audio_type",
    "category",
    "subcategory",
    "pack",
    "source_directory",
    "target_directory",
    "confidence_level",
    "tags",
]


def execution_report_filename(session_id: str, dry_run: bool) -> str:
    return f"dry_run_report_{session_id}.csv" if dry_run else f"report_{session_id}.csv"


def open_execution_report(
    target_dir: Path,
    session_id: str,
    dry_run: bool,
) -> tuple[Path, str, TextIO, csv.DictWriter]:
    report_filename = execution_report_filename(session_id, dry_run)
    csv_path = get_system_dir(target_dir, dry_run) / report_filename
    report_file = open(csv_path, "w", newline="", encoding="utf-8")
    csv_writer = csv.DictWriter(report_file, fieldnames=EXECUTION_REPORT_FIELDNAMES)
    csv_writer.writeheader()
    return csv_path, report_filename, report_file, csv_writer
