import collections
from pathlib import Path
from typing import Dict, Iterable, List, Set, Tuple

from ...core.sorting import sort_list
from ...logic.classification import tokenize


def get_category_tokens(alias_table: Dict, category: str) -> Set[str]:
    tokens = set()
    for alias, (cat, _) in alias_table.items():
        if cat.lower() == category.lower():
            tokens.update(tokenize(alias))
    return tokens


def get_all_weighted_tokens(alias_table: Dict) -> Set[str]:
    weighted = set()
    for alias in alias_table:
        weighted.update(tokenize(alias))
    return weighted


def scan_library(word: str, root_path: Path, weighted_tokens: Set[str]) -> List[Tuple[str, int]]:
    """Path A: scans the library for co-occurring weighted tokens."""
    occurrences = collections.Counter()

    for path in root_path.rglob("*"):
        if not path.is_file():
            continue
        if word.lower() in path.name.lower():
            tokens = tokenize(path.name)
            others = [token for token in tokens if token in weighted_tokens and token != word.lower()]

            for other_one in others:
                combo = tuple(sorted([word.lower(), other_one]))
                occurrences[combo] += 1

                for other_two in others:
                    if other_one != other_two:
                        combo_three = tuple(sorted([word.lower(), other_one, other_two]))
                        occurrences[combo_three] += 1

    results = [(" ".join(combo), count) for combo, count in occurrences.items()]
    aliases = [result[0] for result in results]
    sorted_aliases = sort_list(aliases)
    count_map = dict(results)
    return [(alias, count_map[alias]) for alias in sorted_aliases]


def scan_discovery_data(word: str, discovery_entries: Iterable[Dict[str, object]], weighted_tokens: Set[str]) -> List[Tuple[str, int]]:
    """Path A (cached): scans a reusable discovery-data corpus for co-occurring weighted tokens."""
    occurrences = collections.Counter()
    word_lower = word.lower()

    for entry in discovery_entries:
        name = str(entry.get("name", "") or "")
        if word_lower not in name.lower():
            continue

        raw_tokens = entry.get("tokens")
        tokens = {str(token) for token in raw_tokens} if isinstance(raw_tokens, list) else set()
        others = [token for token in tokens if token in weighted_tokens and token != word_lower]

        for other_one in others:
            combo = tuple(sorted([word_lower, other_one]))
            occurrences[combo] += 1

            for other_two in others:
                if other_one != other_two:
                    combo_three = tuple(sorted([word_lower, other_one, other_two]))
                    occurrences[combo_three] += 1

    results = [(" ".join(combo), count) for combo, count in occurrences.items()]
    aliases = [result[0] for result in results]
    sorted_aliases = sort_list(aliases)
    count_map = dict(results)
    return [(alias, count_map[alias]) for alias in sorted_aliases]


def generate_combinations(word: str, category_tokens: Set[str]) -> List[str]:
    """Path B: generates order-agnostic combinations with category vocabulary."""
    combos = set()
    category_list = list(category_tokens)

    for token_one in category_list:
        if token_one != word.lower():
            combos.add(" ".join(sorted([word.lower(), token_one])))

            for token_two in category_list:
                if token_two != word.lower() and token_one != token_two:
                    combos.add(" ".join(sorted([word.lower(), token_one, token_two])))

    return sort_list(list(combos))
