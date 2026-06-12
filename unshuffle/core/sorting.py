import json
import logging
import os
from pathlib import Path

from .assets import asset_path

TAXONOMY_DIR = asset_path("data", "taxonomy")


def sort_list(items):
    return sorted(items, key=lambda item: (-len(item), item.lower()))


def process_file(path):
    with open(path, "r", encoding="utf-8") as file_handle:
        data = json.load(file_handle)

    if "taxonomy" not in data or not isinstance(data["taxonomy"], dict):
        return False

    changed = False
    taxonomy = data["taxonomy"]

    def sort_recursive(obj):
        nonlocal changed
        if isinstance(obj, dict):
            for key in obj:
                if isinstance(obj[key], list):
                    original = list(obj[key])
                    obj[key] = sort_list(obj[key])
                    if original != obj[key]:
                        changed = True
                elif isinstance(obj[key], dict):
                    sort_recursive(obj[key])

    sort_recursive(taxonomy)

    if changed:
        tmp_path = Path(path).with_suffix(Path(path).suffix + ".tmp")
        with open(tmp_path, "w", encoding="utf-8") as file_handle:
            json.dump(data, file_handle, indent=4)
        os.replace(tmp_path, path)
        return True
    return False


def sort_all(taxonomy_dir: Path = TAXONOMY_DIR):
    for filename in os.listdir(taxonomy_dir):
        if filename.endswith(".json"):
            path = os.path.join(taxonomy_dir, filename)
            try:
                if process_file(path):
                    print(f"Sorted taxonomy: {filename}")
            except Exception:
                logging.exception("Failed to sort taxonomy file %s", filename)
