from collections.abc import Mapping
from typing import Any

from ...core import load_config
from ...core.constants import refresh_alias_structures
from ...logic.classification import reset_scoring_engine
from ...persistence import sync_alias_library, sync_full_config


def build_taxonomy_sync_payload(config: Mapping[str, Any]) -> dict[str, Any]:
    alias_table = dict(config.get("ALIAS_TABLE", {}))
    return {
        "alias_table": alias_table,
        "alias_count": len(alias_table),
        "noise_words": list(config.get("NOISE_WORDS", [])),
        "loop_indicators": list(config.get("LOOP_INDICATORS", [])),
        "oneshot_indicators": list(config.get("ONESHOT_INDICATORS", [])),
        "weak_loop_indicators": list(config.get("WEAK_LOOP_INDICATORS", [])),
        "suppression_rules": dict(config.get("CATEGORY_SUPPRESSION_RULES", {})),
        "sub_taxonomy_map": dict(config.get("SUB_TAXONOMY_MAP", {})),
    }


def sync_taxonomy_to_db(db, config: Mapping[str, Any] | None = None) -> dict[str, int]:
    active_config = load_config() if config is None else dict(config)
    payload = build_taxonomy_sync_payload(active_config)

    if hasattr(db, "write_transaction"):
        with db.write_transaction():
            sync_alias_library(db, payload["alias_table"], in_transaction=True)
            sync_full_config(db, active_config, in_transaction=True)
    else:
        sync_alias_library(db, payload["alias_table"])
        sync_full_config(db, active_config)
    refresh_alias_structures(db)
    reset_scoring_engine()

    return {
        "alias_count": int(payload["alias_count"]),
        "noise_word_count": len(payload["noise_words"]),
        "sub_taxonomy_category_count": len(payload["sub_taxonomy_map"]),
    }
