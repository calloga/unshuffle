from typing import Any
import copy
import threading
from dataclasses import dataclass
from typing import List

from .config import _CONFIG
from .patterns import BPM_REGEX_PATTERN, KEY_REGEX_PATTERN

APP_NAME = "Unshuffle"
APP_VERSION = "1.0.0"
Version = APP_VERSION

AUDIO_EXTS = {".wav", ".mp3", ".aif", ".aiff", ".flac", ".ogg", ".m4a"}
CACHE_FILE_NAME = ".unshuffle_hashes.json"
MAX_PACK_DEPTH = 3
DEFAULT_CLASSIFICATION_FLOOR = 0.05
TREE_REBUILD_DEBOUNCE_MS = 60
MAX_SYNC_FOLDER_EXPORT_RECORDS = 250

STRONG_THRESHOLD = 0.8
MEDIUM_THRESHOLD = 0.6
WEAK_THRESHOLD = 0.45
MARGIN_THRESHOLD = 0.15
NO_SIGNAL_THRESHOLD = 0.05
SHORT_CIRCUIT_THRESHOLD = 0.95
SIMILARITY_THRESHOLD = 1
GLOBAL_BOOST_CAP = 0.03
TOKEN_ADJUSTMENT_STEP = 0.01
TOKEN_ADJUSTMENT_CAP = 0.10

ANCHOR_MAP = {
    "Kicks": "kick",
    "Snares": "snare",
    "Claps": "clap",
    "Hats & Cymbals": "cymbal",
    "Toms": "tom",
    "Percussion": "perc",
    "Bass": "bass",
    "Melodics": "synth",
    "Vocals": "vox",
    "FX": "fx",
    "Full Drums": "drumloop",
}

FILENAME_WEIGHT = 1.0
CONTEXT_WEIGHT = 0.5
PACK_WEIGHT = 0.3
SUPPRESS_TRIGGER = 0.6
KEY_FALLBACK_CONFIDENCE = 0.40
LOOP_SPECIFICITY_MALUS = 0.3
WEAK_LOOP_PROBABILITY = 0.05
SHORT_DURATION_MALUS = 0.1
LOOP_PROBABILITY_THRESHOLD = 0.5
CONSISTENCY_MIN_FILES = 5
CONSISTENCY_THRESHOLD = 0.60
PACK_CONSISTENCY_THRESHOLD = 0.60
PACK_CONSISTENCY_BONUS = 0.40
PURE_CONTAINER_BONUS = 0.30
PURE_GENERIC_BONUS = 0.15
LARGE_CONTAINER_MALUS = -0.35
LARGE_BRAND_BONUS_MULT = 0.05
CHILD_DUP_BONUS = 0.55
LEAF_IDENTITY_BONUS = 0.20
LEAF_GENERIC_MALUS = -0.20
SHARED_BOOST_THRESHOLD = 0.30
SHARED_BOOST_BASE = 0.075
LEAF_MALUS = -0.10
NEIGHBOR_BOOST_BASE = 0.05
NEIGHBOR_BOOST_MAX = 0.30
LOSER_MALUS_MULT = -0.30
WINNER_BONUS_MULT = 0.30


@dataclass(slots=True)
class RuntimeConfigState:
    alias_table: dict[str,Any]
    category_suppression_rules: dict[str, list[str]]
    loop_indicators: list[str]
    weak_loop_indicators: list[str]
    oneshot_indicators: list[str]
    oneshot_hint_tokens: list[str]
    noise_words: set[str]
    percussive_categories: list[str]
    categories: list[str]
    sorted_aliases: list[str]
    subset_map: dict
    sub_taxonomy_map: dict
    default_sub_map: dict
    reserved_names: frozenset
    ignored_system_artifact_names: frozenset


def _build_categories(alias_table: dict) -> list[str]:
    categories = set()
    for value in alias_table.values():
        if isinstance(value, str):
            if value:
                categories.add(value)
        elif isinstance(value, (list, tuple)) and value and isinstance(value[0], str) and value[0]:
            categories.add(value[0])
    return (
        sorted(categories)
        + ["Uncategorized", "Non-Audio Assets"]
    )


MODEL_NUMBERS = {"808", "909", "303", "101", "707", "606", "808s", "909s"}


def _default_reserved_names() -> frozenset:
    return frozenset(
        {
            "prefix_legend.csv",
            "dry_run_report.csv",
            ".unshuffle",
            "unshuffle.log",
            CACHE_FILE_NAME,
            "DO_NOT_DELETE_unshuffle",
        }
    )


def _default_ignored_system_artifact_names(config: dict) -> frozenset:
    hidden_files = {
        str(name)
        for name in config.get("HIDDEN_SYSTEM_FILES", ())
        if isinstance(name, str) and name
    }
    return frozenset({*hidden_files, "__MACOSX"})


def _build_subset_map(sorted_aliases: List[str]) -> dict:
    aliases_by_first_char = {}
    for alias in sorted_aliases:
        first_char = alias[0] if alias else ""
        aliases_by_first_char.setdefault(first_char, []).append(alias)

    mapping = {}
    for alias in sorted_aliases:
        subsets = set()
        candidate_aliases = set()
        for first_char in set(alias):
            candidate_aliases.update(aliases_by_first_char.get(first_char, ()))

        for potential_sub in candidate_aliases:
            if potential_sub == alias:
                continue
            if potential_sub in alias:
                subsets.add(potential_sub)
        mapping[alias] = subsets
    return mapping


def _build_runtime_state(config) -> RuntimeConfigState:
    alias_table: dict[str, Any] = config["ALIAS_TABLE"]
    categories = _build_categories(alias_table)
    sorted_aliases = sorted(alias_table.keys(), key=len, reverse=True)
    return RuntimeConfigState(
        alias_table=alias_table,
        category_suppression_rules=config["CATEGORY_SUPPRESSION_RULES"],
        loop_indicators=config["LOOP_INDICATORS"],
        weak_loop_indicators=config.get("WEAK_LOOP_INDICATORS", []),
        oneshot_indicators=config["ONESHOT_INDICATORS"],
        oneshot_hint_tokens=config["ONESHOT_HINT_TOKENS"],
        noise_words=config["NOISE_WORDS"],
        percussive_categories=config["PERCUSSIVE_CATEGORIES"],
        categories=categories,
        sorted_aliases=sorted_aliases,
        subset_map=_build_subset_map(sorted_aliases),
        sub_taxonomy_map=config.get("SUB_TAXONOMY_MAP", {}),
        default_sub_map=config.get("DEFAULT_SUB_MAP", {}),
        reserved_names=_default_reserved_names(),
        ignored_system_artifact_names=_default_ignored_system_artifact_names(config),
    )


_STATE = _build_runtime_state(_CONFIG)
ALIAS_TABLE = _STATE.alias_table
CATEGORY_SUPPRESSION_RULES = _STATE.category_suppression_rules
LOOP_INDICATORS = _STATE.loop_indicators
WEAK_LOOP_INDICATORS = _STATE.weak_loop_indicators
ONESHOT_INDICATORS = _STATE.oneshot_indicators
ONESHOT_HINT_TOKENS = _STATE.oneshot_hint_tokens
NOISE_WORDS = _STATE.noise_words
PERCUSSIVE_CATEGORIES = _STATE.percussive_categories
CATEGORIES = _STATE.categories
_SORTED_ALIASES = _STATE.sorted_aliases
SUBSET_MAP = _STATE.subset_map
SUB_TAXONOMY_MAP = _STATE.sub_taxonomy_map
RESERVED_NAMES = _STATE.reserved_names
IGNORED_SYSTEM_ARTIFACT_NAMES = _STATE.ignored_system_artifact_names
CONFIG_STATE_LOCK = threading.RLock()


def get_runtime_config_snapshot():
    with CONFIG_STATE_LOCK:
        return {
            "alias_table": copy.deepcopy(_STATE.alias_table),
            "noise_words": set(_STATE.noise_words),
            "loop_indicators": list(_STATE.loop_indicators),
            "weak_loop_indicators": list(_STATE.weak_loop_indicators),
            "oneshot_indicators": list(_STATE.oneshot_indicators),
            "oneshot_hint_tokens": list(_STATE.oneshot_hint_tokens),
            "category_suppression_rules": {
                key: list(values)
                for key, values in _STATE.category_suppression_rules.items()
            },
            "percussive_categories": list(_STATE.percussive_categories),
            "sub_taxonomy_map": copy.deepcopy(_STATE.sub_taxonomy_map),
            "default_sub_map": copy.deepcopy(_STATE.default_sub_map),
            "model_numbers": set(MODEL_NUMBERS),
        }


def refresh_alias_structures(db=None):
    fallback_state = _build_runtime_state(_CONFIG)
    db_aliases = None
    db_noise_words = None
    db_loop_indicators = None
    db_oneshot_indicators = None
    db_oneshot_hint_tokens = None
    db_weak_loop_indicators = None
    db_suppression_rules = None
    db_sub_taxonomy = None
    db_percussive_categories = None

    if db:
        db_aliases = db.get_aliases()
        db_noise_words = db.get_config_list("noise_word")
        db_loop_indicators = db.get_config_list("loop_indicator")
        db_oneshot_indicators = db.get_config_list("oneshot_indicator")
        db_oneshot_hint_tokens = db.get_config_list("oneshot_hint_token")
        db_weak_loop_indicators = db.get_config_list("weak_loop_indicator")
        db_percussive_categories = db.get_config_list("percussive_category")
        db_suppression_rules = db.get_suppression_rules()
        db_sub_taxonomy = db.get_sub_taxonomy()

    with CONFIG_STATE_LOCK:
        if db:
            _STATE.alias_table.clear()
            _STATE.alias_table.update(db_aliases or fallback_state.alias_table)
            _STATE.noise_words.clear()
            _STATE.noise_words.update(db_noise_words or fallback_state.noise_words)
            _STATE.loop_indicators[:] = list(db_loop_indicators or fallback_state.loop_indicators)
            _STATE.oneshot_indicators[:] = list(db_oneshot_indicators or fallback_state.oneshot_indicators)
            _STATE.oneshot_hint_tokens[:] = list(db_oneshot_hint_tokens or fallback_state.oneshot_hint_tokens)
            _STATE.weak_loop_indicators[:] = list(db_weak_loop_indicators or fallback_state.weak_loop_indicators)
            _STATE.percussive_categories[:] = list(db_percussive_categories or fallback_state.percussive_categories)
            _STATE.category_suppression_rules.clear()
            _STATE.category_suppression_rules.update(db_suppression_rules or fallback_state.category_suppression_rules)
            _STATE.sub_taxonomy_map.clear()
            _STATE.sub_taxonomy_map.update(db_sub_taxonomy or fallback_state.sub_taxonomy_map)
            _STATE.default_sub_map.clear()
            _STATE.default_sub_map.update(fallback_state.default_sub_map)
        else:
            _STATE.alias_table.clear()
            _STATE.alias_table.update(fallback_state.alias_table)
            _STATE.noise_words.clear()
            _STATE.noise_words.update(fallback_state.noise_words)
            _STATE.loop_indicators[:] = list(fallback_state.loop_indicators)
            _STATE.oneshot_indicators[:] = list(fallback_state.oneshot_indicators)
            _STATE.oneshot_hint_tokens[:] = list(fallback_state.oneshot_hint_tokens)
            _STATE.weak_loop_indicators[:] = list(fallback_state.weak_loop_indicators)
            _STATE.percussive_categories[:] = list(fallback_state.percussive_categories)
            _STATE.category_suppression_rules.clear()
            _STATE.category_suppression_rules.update(fallback_state.category_suppression_rules)
            _STATE.sub_taxonomy_map.clear()
            _STATE.sub_taxonomy_map.update(fallback_state.sub_taxonomy_map)
            _STATE.default_sub_map.clear()
            _STATE.default_sub_map.update(fallback_state.default_sub_map)

        _STATE.sorted_aliases.clear()
        _STATE.sorted_aliases.extend(sorted(_STATE.alias_table.keys(), key=len, reverse=True))
        _STATE.subset_map.clear()
        _STATE.subset_map.update(_build_subset_map(_STATE.sorted_aliases))
        _STATE.categories.clear()
        _STATE.categories.extend(_build_categories(_STATE.alias_table))
        _STATE.reserved_names = _default_reserved_names()
        _STATE.ignored_system_artifact_names = fallback_state.ignored_system_artifact_names


PRESERVED_MARKER = ".unshuffle_preserved"
