import csv
from pathlib import Path
from typing import Any, Dict, List, Optional

from ...logic.classification import get_scoring_engine
from ...persistence import get_global_system_dir, load_discovery_data
from .discovery_engine import generate_combinations, get_all_weighted_tokens, get_category_tokens, scan_discovery_data, scan_library


def load_alias_table(db) -> Dict:
    return db.get_aliases()


def save_alias_table(db, alias_table: Dict, source="discovery"):
    alias_list = []
    for alias, (category, weight) in alias_table.items():
        alias_list.append((alias, category, weight, source))
    db.seed_aliases_bulk(alias_list)
    return len(alias_list)


def show_token_weights(words: List[str]):
    """Return statistical weights and specificity for tokens across categories."""
    engine = get_scoring_engine()
    results: List[Dict[str, Any]] = []
    for word in words:
        word_clean = word.lower()
        category_hits = []
        for category, token_map in engine.weights.items():
            if word_clean in token_map:
                category_hits.append((category, token_map[word_clean]))

        entry: Dict[str, Any] = {
            "word": word_clean,
            "specificity": engine.specificity.get(word_clean, 0.0),
            "hits": [],
            "found": False,
        }
        if category_hits:
            category_hits.sort(key=lambda item: item[1], reverse=True)
            for category, weight in category_hits:
                entry["hits"].append({"category": category, "weight": weight})
            entry["found"] = True
        results.append(entry)
    return results


def get_discovery_output_dir(output_dir: Optional[Path] = None) -> Path:
    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        return output_dir
    default_dir = get_global_system_dir() / "discovery"
    default_dir.mkdir(parents=True, exist_ok=True)
    return default_dir


def _build_discovery_rows(
    word_clean: str,
    category: str,
    alias_table: Dict[str, Any],
    weighted_tokens,
    auto_detect: bool,
    source_dir: Optional[str],
    discovery_entries: Optional[List[Dict[str, object]]] = None,
) -> List[Dict[str, str]]:
    if auto_detect:
        if discovery_entries is not None:
            results = scan_discovery_data(word_clean, discovery_entries, weighted_tokens)
        else:
            root_input = source_dir or "."
            root_path = Path(root_input)
            results = scan_library(word_clean, root_path, weighted_tokens)
        csv_data = [{"Alias": alias, "Frequency": str(count), "Valid": ""} for alias, count in results]
    else:
        category_tokens = get_category_tokens(alias_table, category)
        results = generate_combinations(word_clean, category_tokens)
        csv_data = [{"Alias": alias, "Frequency": "N/A", "Valid": ""} for alias in results]
    csv_data.insert(0, {"Alias": word_clean, "Frequency": "N/A", "Valid": "x"})
    return csv_data


def write_discovery_csv(word_clean: str, rows: List[Dict[str, str]], output_dir: Optional[Path] = None) -> Path:
    filename = get_discovery_output_dir(output_dir) / f"discovery_{word_clean}.csv"
    with open(filename, "w", newline="", encoding="utf-8") as file_handle:
        writer = csv.DictWriter(file_handle, fieldnames=["Alias", "Frequency", "Valid"])
        writer.writeheader()
        writer.writerows(rows)
    return filename


def run_discovery(
    target_path: Path,
    discover_words: Optional[str] = None,
    category: Optional[str] = None,
    auto_detect: bool = False,
    source_dir: Optional[str] = None,
    output_dir: Optional[Path] = None,
    export_csv: bool = True,
):
    from ...persistence import get_db

    if not discover_words:
        raise ValueError("discover_words is required")
    if not category:
        raise ValueError("category is required")

    db = get_db(target_path)
    alias_table = load_alias_table(db)
    weighted_tokens = get_all_weighted_tokens(alias_table)
    words = [word.strip() for word in discover_words.split(",") if word.strip()]
    results = []
    discovery_entries = None
    if auto_detect and source_dir:
        loaded = load_discovery_data(target_path, Path(source_dir))
        if loaded:
            discovery_entries = list(loaded.get("entries") or [])

    for word in words:
        word_clean = word.lower()
        if word_clean in alias_table:
            results.append(
                {
                    "word": word_clean,
                    "category": category,
                    "status": "already_present",
                    "rows": [],
                    "csv_path": None,
                }
            )
            continue

        csv_data = _build_discovery_rows(
            word_clean,
            category,
            alias_table,
            weighted_tokens,
            auto_detect,
            source_dir,
            discovery_entries=discovery_entries,
        )
        csv_path = write_discovery_csv(word_clean, csv_data, output_dir=output_dir) if export_csv else None
        results.append(
            {
                "word": word_clean,
                "category": category,
                "status": "created",
                "rows": csv_data,
                "csv_path": str(csv_path) if csv_path else None,
                "source": "discovery_data" if discovery_entries is not None else ("filesystem_scan" if auto_detect else "generated"),
            }
        )
    return results


def run_import(csv_path: str, category: str, target_path: Path):
    from ...persistence import get_db

    db = get_db(target_path)
    existing_aliases = load_alias_table(db)
    alias_list = []

    with open(csv_path, "r", encoding="utf-8") as file_handle:
        reader = csv.DictReader(file_handle)
        for row in reader:
            if row["Valid"].strip().lower() == "x":
                alias = row["Alias"].strip()
                if alias and alias not in existing_aliases:
                    alias_list.append((alias, category, 1.0, "discovery"))
                    existing_aliases[alias] = [category, 1.0]

    if alias_list:
        with db.write_transaction():
            db.seed_aliases_bulk(alias_list)
    return {"category": category, "added_count": len(alias_list), "added_aliases": [item[0] for item in alias_list]}
