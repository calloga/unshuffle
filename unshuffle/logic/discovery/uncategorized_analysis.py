import csv
from collections import Counter
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any


def load_uncategorized_csv(csv_path: Path) -> list[dict[str, str]]:
    with open(csv_path, "r", encoding="utf-8", newline="") as file_handle:
        return [dict(row) for row in csv.DictReader(file_handle)]


def analyze_uncategorized_rows(
    rows: Iterable[Mapping[str, Any]],
    top_folder_limit: int = 10,
    example_limit: int = 10,
) -> dict[str, Any]:
    uncategorized = [dict(row) for row in rows if str(row.get("category", "")).strip() == "Uncategorized"]
    folder_counts = Counter(str(row.get("source_directory", "")).strip() for row in uncategorized)

    examples = []
    for row in uncategorized[:example_limit]:
        examples.append(
            {
                "sample_name": str(row.get("sample_name") or row.get("source_filename") or "").strip(),
                "source_directory": str(row.get("source_directory", "")).strip(),
                "confidence_level": str(row.get("confidence_level") or row.get("confidence") or "").strip(),
            }
        )

    return {
        "total_uncategorized": len(uncategorized),
        "top_folders": folder_counts.most_common(top_folder_limit),
        "examples": examples,
    }


def analyze_uncategorized_csv(
    csv_path: Path,
    top_folder_limit: int = 10,
    example_limit: int = 10,
) -> dict[str, Any]:
    rows = load_uncategorized_csv(csv_path)
    return analyze_uncategorized_rows(rows, top_folder_limit=top_folder_limit, example_limit=example_limit)
