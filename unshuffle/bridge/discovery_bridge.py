from pathlib import Path
from typing import Optional

from ..logic.discovery import (
    alias_discovery as discovery_aliases,
    analyze_uncategorized_csv,
    discovery_engine,
    find_cross_taxonomy_conflicts,
)


class DiscoveryBridge:
    """Curated facade for discovery workflows."""

    @staticmethod
    def load_alias_table(db):
        return discovery_aliases.load_alias_table(db)

    @staticmethod
    def save_alias_table(db, alias_table, source="discovery"):
        return discovery_aliases.save_alias_table(db, alias_table, source=source)

    @staticmethod
    def run_discovery(
        target_path: Path,
        discover_words: Optional[str] = None,
        category: Optional[str] = None,
        auto_detect: bool = False,
        source_dir: Optional[str] = None,
        output_dir: Optional[Path] = None,
        export_csv: bool = True,
    ):
        return discovery_aliases.run_discovery(
            target_path,
            discover_words,
            category,
            auto_detect,
            source_dir,
            output_dir=output_dir,
            export_csv=export_csv,
        )

    @staticmethod
    def run_import(csv_path: str, category: str, target_path: Path):
        return discovery_aliases.run_import(csv_path, category, target_path)

    @staticmethod
    def show_token_weights(words):
        return discovery_aliases.show_token_weights(words)

    @staticmethod
    def lookup_alias(
        target_path: Path,
        word: str,
        category: str,
        auto_detect: bool = False,
        source_dir: Optional[str] = None,
        output_dir: Optional[Path] = None,
    ):
        results = discovery_aliases.run_discovery(
            target_path,
            discover_words=word,
            category=category,
            auto_detect=auto_detect,
            source_dir=source_dir,
            output_dir=output_dir,
            export_csv=False,
        )
        return results[0] if results else {}

    @staticmethod
    def get_category_tokens(alias_table, category):
        return discovery_engine.get_category_tokens(alias_table, category)

    @staticmethod
    def get_all_weighted_tokens(alias_table):
        return discovery_engine.get_all_weighted_tokens(alias_table)

    @staticmethod
    def scan_library(word, root_path, weighted_tokens):
        return discovery_engine.scan_library(word, root_path, weighted_tokens)

    @staticmethod
    def scan_discovery_data(word, discovery_entries, weighted_tokens):
        return discovery_engine.scan_discovery_data(word, discovery_entries, weighted_tokens)

    @staticmethod
    def generate_combinations(word, category_tokens):
        return discovery_engine.generate_combinations(word, category_tokens)

    @staticmethod
    def find_possible_conflicts(taxonomy_dir: Path):
        return find_cross_taxonomy_conflicts(taxonomy_dir)

    @staticmethod
    def analyze_uncategorized(csv_path: Path, top_folder_limit: int = 10, example_limit: int = 10):
        return analyze_uncategorized_csv(
            csv_path,
            top_folder_limit=top_folder_limit,
            example_limit=example_limit,
        )
