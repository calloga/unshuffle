import json
from collections.abc import Iterator, MutableMapping
from pathlib import Path
from threading import RLock
from typing import Any

from .assets import asset_path
from .logging import logger


ROOT_DIR = Path(__file__).parent.parent.parent
_DEFAULT_ROOT_DIR = ROOT_DIR
CONFIG_FILE_NAME = "data/config.json"

DEFAULT_CONFIG = {
    "ALIAS_TABLE": {},
    "CATEGORY_SUPPRESSION_RULES": {},
    "LOG_LEVEL": "INFO",
    "LOOP_INDICATORS": [],
    "WEAK_LOOP_INDICATORS": [],
    "ONESHOT_INDICATORS": [],
    "ONESHOT_HINT_TOKENS": ["kick", "snare", "hat", "clap", "perc", "tom", "rim", "oneshot"],
    "NOISE_WORDS": ["hard", "soft", "dry", "wet", "stems", "stem", "midi"],
    "HIDDEN_SYSTEM_FILES": [".ds_store", "thumbs.db", "desktop.ini", "__MACOSX"],
    "PERCUSSIVE_CATEGORIES": ["Kicks", "Snares", "Claps", "Hats & Cymbals", "Percussion"],
    "CATEGORY_SUPPRESS_MAP": {},
    "SUB_TAXONOMY_MAP": {},
    "DEFAULT_SUB_MAP": {},
    "STORE_MIGRATION": {}
}

_CONFIG_CACHE: dict[str, Any] | None = None
_CONFIG_LOCK = RLock()


def _config_asset_path(*parts: str) -> Path:
    root_candidate = ROOT_DIR.joinpath(*parts)
    if ROOT_DIR != _DEFAULT_ROOT_DIR or root_candidate.exists():
        return root_candidate
    return asset_path(*parts)


def _warn_invalid_taxonomy(tax_file: Path, reason: str):
    logger.warning("Skipping taxonomy file %s: %s", tax_file.name, reason)


def _normalize_config_shape(config):
    normalized: dict[str, Any] = dict(DEFAULT_CONFIG)
    normalized.update({key: value for key, value in config.items() if key in DEFAULT_CONFIG})

    for key in ("ALIAS_TABLE", "CATEGORY_SUPPRESSION_RULES", "CATEGORY_SUPPRESS_MAP", "SUB_TAXONOMY_MAP", "DEFAULT_SUB_MAP"):
        if not isinstance(normalized.get(key), dict):
            logger.warning("Invalid %s in %s; using empty mapping.", key, CONFIG_FILE_NAME)
            normalized[key] = {}

    if not isinstance(normalized.get("LOG_LEVEL"), str):
        logger.warning("Invalid LOG_LEVEL in %s; using default.", CONFIG_FILE_NAME)
        normalized["LOG_LEVEL"] = DEFAULT_CONFIG["LOG_LEVEL"]

    for key in (
        "LOOP_INDICATORS",
        "WEAK_LOOP_INDICATORS",
        "ONESHOT_INDICATORS",
        "ONESHOT_HINT_TOKENS",
        "NOISE_WORDS",
        "HIDDEN_SYSTEM_FILES",
        "PERCUSSIVE_CATEGORIES",
    ):
        if not isinstance(normalized.get(key), list):
            logger.warning("Invalid %s in %s; using empty list/defaults.", key, CONFIG_FILE_NAME)
            normalized[key] = list(DEFAULT_CONFIG[key])

    normalized["NOISE_WORDS"] = set(normalized["NOISE_WORDS"])
    return normalized


def _merge_config_values(config: dict[str, Any], user_config: dict[str, Any]) -> None:
    for key, value in user_config.items():
        if isinstance(value, dict) and key in config and isinstance(config[key], dict):
            config[key].update(value)
        elif isinstance(value, list) and key in config and isinstance(config[key], list):
            merged: list[Any] = list(config[key])
            for item in value:
                if item not in merged:
                    merged.append(item)
            config[key] = merged
        else:
            config[key] = value


def load_config() -> dict[str, Any]:
    config_path = _config_asset_path("data", "config.json")
    config = dict(DEFAULT_CONFIG)

    if config_path.exists():
        try:
            with open(config_path, "r", encoding="utf-8") as file_handle:
                user_config = json.load(file_handle)
                _merge_config_values(config, user_config)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Could not load %s, using defaults: %s", CONFIG_FILE_NAME, exc)

    config = _normalize_config_shape(config)

    alias_table = config.get("ALIAS_TABLE", {})
    sub_taxonomy_map = config.get("SUB_TAXONOMY_MAP", {})
    default_sub_map = config.get("DEFAULT_SUB_MAP", {})
    taxonomy_dir = _config_asset_path("data", "taxonomy")
    if taxonomy_dir.exists():
        for tax_file in taxonomy_dir.glob("*.json"):
            if tax_file.name == "config.json":
                continue

            try:
                with open(tax_file, "r", encoding="utf-8") as file_handle:
                    data = json.load(file_handle)

                cat_name = data.get("category")
                default_sub = data.get("default_sub")
                taxonomy = data.get("taxonomy")

                if not isinstance(cat_name, str) or not cat_name.strip() or not isinstance(taxonomy, dict):
                    _warn_invalid_taxonomy(tax_file, "expected object with 'category' and dict 'taxonomy'")
                    continue

                cat_name = cat_name.strip()
                if default_sub and isinstance(default_sub, str):
                    default_sub_map[cat_name] = default_sub
                elif default_sub:
                    _warn_invalid_taxonomy(tax_file, "'default_sub' must be a string when present")

                if cat_name not in sub_taxonomy_map:
                    sub_taxonomy_map[cat_name] = {}

                def process_level(obj, target_sub_map, parent_cat, trail):
                    if isinstance(obj, dict):
                        for bucket_name, content in obj.items():
                            if isinstance(content, list):
                                for alias in content:
                                    if isinstance(alias, str):
                                        alias_lower = alias.lower()
                                        alias_table[alias_lower] = parent_cat
                                        target_sub_map[alias_lower] = bucket_name
                                    else:
                                        _warn_invalid_taxonomy(
                                            tax_file,
                                            f"non-string alias under {'/'.join(trail + [bucket_name])}",
                                        )
                            elif isinstance(content, dict):
                                process_level(content, target_sub_map, parent_cat, trail + [bucket_name])
                            else:
                                _warn_invalid_taxonomy(
                                    tax_file,
                                    f"bucket {'/'.join(trail + [bucket_name])} must contain a list or object",
                                )

                process_level(taxonomy, sub_taxonomy_map[cat_name], cat_name, [])
            except (json.JSONDecodeError, OSError) as exc:
                _warn_invalid_taxonomy(tax_file, str(exc))

    config["ALIAS_TABLE"] = alias_table
    config["SUB_TAXONOMY_MAP"] = sub_taxonomy_map
    config["DEFAULT_SUB_MAP"] = default_sub_map
    return config


def ensure_default_config() -> Path:
    config_path = _config_asset_path("data", "config.json")
    if config_path.exists():
        return config_path

    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "w", encoding="utf-8") as file_handle:
        json.dump(DEFAULT_CONFIG, file_handle, indent=4)
    logger.info("Created default configuration at %s", config_path)
    return config_path


def get_config(reload: bool = False) -> dict[str, Any]:
    global _CONFIG_CACHE
    with _CONFIG_LOCK:
        if reload or _CONFIG_CACHE is None:
            _CONFIG_CACHE = load_config()
        return _CONFIG_CACHE


def reset_config_cache() -> None:
    global _CONFIG_CACHE
    with _CONFIG_LOCK:
        _CONFIG_CACHE = None


class _ConfigProxy(MutableMapping[str, Any]):
    def _data(self) -> dict[str, Any]:
        return get_config()

    def __getitem__(self, key: str) -> Any:
        return self._data()[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self._data()[key] = value

    def __delitem__(self, key: str) -> None:
        del self._data()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._data())

    def __len__(self) -> int:
        return len(self._data())

    def __repr__(self) -> str:
        return repr(self._data())


_CONFIG = _ConfigProxy()
