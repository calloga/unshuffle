import csv
import json
from pathlib import Path
from typing import Any, Sequence

from ..core import tags_to_search_text


def _iter_taxonomy_documents(taxonomy_dir: Path):
    for tax_file in sorted(taxonomy_dir.glob("*.json")):
        try:
            with open(tax_file, "r", encoding="utf-8") as file_handle:
                data = json.load(file_handle)
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            yield tax_file, data


def build_taxonomy_snapshot(taxonomy_dir: Path) -> dict[str, dict[str, Any]]:
    full_taxonomy: dict[str, dict[str, Any]] = {}
    for tax_file, data in _iter_taxonomy_documents(taxonomy_dir):
        category = data.get("category")
        taxonomy = data.get("taxonomy")
        if not category or not isinstance(taxonomy, dict):
            continue

        all_aliases: list[str] = []
        for bucket in taxonomy.values():
            if isinstance(bucket, list):
                all_aliases.extend(alias for alias in bucket if isinstance(alias, str))

        full_taxonomy[str(category)] = {
            "main_aliases": sorted(all_aliases),
            "sub_taxonomy": taxonomy,
        }
    return full_taxonomy


def export_taxonomy_snapshot(output_path: Path, taxonomy_dir: Path) -> Path:
    snapshot = build_taxonomy_snapshot(taxonomy_dir)
    with open(output_path, "w", encoding="utf-8") as file_handle:
        json.dump(snapshot, file_handle, indent=4)
    return output_path


def build_metadata_backup(db: Any) -> dict[str, Any]:
    return {
        "schema_version": db.get_schema_version(),
        "aliases": db.get_aliases_with_source(),
        "token_adjustments": db.get_token_adjustments(),
        "config_lists": {
            list_type: db.get_config_list(list_type)
            for list_type in sorted(
                {
                    row[0]
                    for row in db.conn.execute(
                        "SELECT DISTINCT list_type FROM config_lists ORDER BY list_type"
                    ).fetchall()
                }
            )
        },
        "suppression_rules": db.get_suppression_rules(),
        "sub_taxonomy": db.get_sub_taxonomy(),
        "exclusions": db.get_exclusions(),
        "sessions": db.get_recent_sessions(limit=100000),
    }


def export_metadata_backup(output_path: Path, db: Any) -> Path:
    backup = build_metadata_backup(db)
    with open(output_path, "w", encoding="utf-8") as file_handle:
        json.dump(backup, file_handle, indent=4)
    return output_path


def export_staging_plan_csv(file_path: Path, records: Sequence[Any]) -> Path:
    with open(file_path, "w", newline="", encoding="utf-8") as file_handle:
        writer = csv.DictWriter(
            file_handle,
            fieldnames=[
                "source_directory",
                "source_filename",
                "pack",
                "category",
                "subcategory",
                "audio_type",
                "tags",
            ],
        )
        writer.writeheader()
        for rec in records:
            writer.writerow(
                {
                    "source_directory": str(rec.source_path.parent),
                    "source_filename": rec.source_path.name,
                    "pack": rec.pack,
                    "category": rec.category,
                    "subcategory": rec.subcategory or "",
                    "audio_type": rec.audio_type,
                    "tags": tags_to_search_text(rec.tags),
                }
            )
    return file_path
