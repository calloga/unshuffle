"""Alias discovery and discovery-support queries.

Start here for:
- guided alias discovery: `run_discovery`
- CSV import of approved aliases: `run_import`
- token/category exploration helpers: `show_token_weights`, `get_category_tokens`
- taxonomy sync / review helpers: `sync_taxonomy_to_db`, `find_cross_taxonomy_conflicts`
- uncategorized report analysis: `analyze_uncategorized_csv`, `analyze_uncategorized_rows`
"""

from .alias_discovery import load_alias_table, run_discovery, run_import, save_alias_table, show_token_weights
from .discovery_engine import (
    generate_combinations,
    get_all_weighted_tokens,
    get_category_tokens,
    scan_discovery_data,
    scan_library,
)
from .taxonomy_conflicts import find_cross_taxonomy_conflicts
from .taxonomy_sync import build_taxonomy_sync_payload, sync_taxonomy_to_db
from .uncategorized_analysis import analyze_uncategorized_csv, analyze_uncategorized_rows, load_uncategorized_csv

__all__ = [
    "analyze_uncategorized_csv",
    "analyze_uncategorized_rows",
    "build_taxonomy_sync_payload",
    "find_cross_taxonomy_conflicts",
    "generate_combinations",
    "get_all_weighted_tokens",
    "get_category_tokens",
    "load_alias_table",
    "load_uncategorized_csv",
    "run_discovery",
    "run_import",
    "save_alias_table",
    "scan_discovery_data",
    "scan_library",
    "show_token_weights",
    "sync_taxonomy_to_db",
]
