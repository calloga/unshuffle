"""Persistence-layer helpers and database entrypoints.

Start here for:
- database handle construction: `get_db`, `get_local_db`, `UnshuffleDB`
- metadata/system paths: `get_system_dir`, `get_global_system_dir`, `get_trash_dir`
- session/staging metadata helpers: `load_json_meta`, `save_json_meta`, `cleanup_session_meta`
"""

import hashlib
import json
import logging
import os
import re
import shutil
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from ..core import paths as core_paths
from ..core.paths import (
    DB_FILE_NAME,
    DIRECTORY_DUMP_FILE,
    DRY_RUN_FOLDER_NAME,
    SYSTEM_FOLDER_NAME,
    get_global_system_dir as core_get_global_system_dir,
    get_local_system_dir as core_get_local_system_dir,
    get_system_dir as core_get_system_dir,
    get_trash_dir as core_get_trash_dir,
)
from .storage import UnshuffleDB
from . import taxonomy_store


def get_db(target_dir: Path, is_dry_run: bool = False):
    """Returns an initialized UnshuffleDB instance."""
    db_path = get_system_dir(target_dir, is_dry_run) / DB_FILE_NAME
    return UnshuffleDB(db_path)


def get_global_system_dir() -> Path:
    return core_get_global_system_dir()


def get_system_dir(target_dir: Path, is_dry_run: bool = False) -> Path:
    if is_dry_run:
        return core_get_system_dir(target_dir, is_dry_run=True)

    folder = get_global_system_dir()
    if not folder.exists():
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logging.warning("Could not create metadata folder %s: %s", folder, exc)
    return folder


def get_local_system_dir(target_dir: Path) -> Path:
    return core_get_local_system_dir(target_dir)


def get_trash_dir(target_dir: Path, session_id: str) -> Path:
    return core_get_trash_dir(target_dir, session_id)


def get_local_db(target_dir: Path):
    """Returns an initialized local UnshuffleDB instance for mirroring."""
    db_path = get_local_system_dir(target_dir) / DB_FILE_NAME
    return UnshuffleDB(db_path)


def load_json_meta(target_dir: Path, filename: str, is_dry_run: bool = False) -> Optional[Any]:
    file_path = get_system_dir(target_dir, is_dry_run) / filename
    if file_path.exists():
        try:
            with open(file_path, "r", encoding="utf-8") as file_handle:
                return json.load(file_handle)
        except (json.JSONDecodeError, OSError) as exc:
            logging.error("Failed to load %s: %s", filename, exc)
    return None


def save_json_meta(target_dir: Path, filename: str, data: Any, is_dry_run: bool = False):
    file_path = get_system_dir(target_dir, is_dry_run) / filename
    tmp_path = file_path.with_suffix(file_path.suffix + ".tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8") as file_handle:
            json.dump(data, file_handle, indent=4)
        os.replace(tmp_path, file_path)
    except OSError as exc:
        logging.error("Failed to save %s: %s", filename, exc)
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass


def get_directory_dump_filename(session_id: str, source_root: Optional[Path] = None) -> str:
    if source_root is None:
        return f"{DIRECTORY_DUMP_FILE}_{session_id}.json"
    source_key = hashlib.md5(str(source_root.resolve()).encode("utf-8")).hexdigest()[:8]
    return f"{DIRECTORY_DUMP_FILE}_{session_id}_{source_key}.json"


def get_discovery_data_filename(source_root: Path) -> str:
    source_key = hashlib.md5(str(source_root.resolve()).encode("utf-8")).hexdigest()[:8]
    return f"discovery_data_{source_key}.json"


def load_discovery_data(target_dir: Path, source_root: Path, is_dry_run: bool = False) -> Optional[Any]:
    return load_json_meta(target_dir, get_discovery_data_filename(source_root), is_dry_run=is_dry_run)


def cleanup_session_meta(target_dir: Path, session_id: str, db, report_filename: Optional[str] = None):
    """
    Source-aware cleanup: delete old ephemeral metadata only for the current
    source root, keeping history for other sources intact.
    """
    dry_run_dir = target_dir / DRY_RUN_FOLDER_NAME
    if not dry_run_dir.exists():
        return

    current_sources = []
    if hasattr(db, "get_session_sources"):
        current_sources = db.get_session_sources(session_id)

    if not current_sources:
        cursor = db.conn.execute("SELECT source_path FROM sessions WHERE session_id = ?", (session_id,))
        row = cursor.fetchone()
        if not row:
            return
        current_sources = [row["source_path"]]

    placeholders = ", ".join("?" for _ in current_sources)
    cursor = db.conn.execute(
        f"""
        SELECT DISTINCT s.session_id
        FROM sessions s
        LEFT JOIN session_sources ss ON s.session_id = ss.session_id
        WHERE s.session_id != ?
          AND (
              s.source_path IN ({placeholders})
              OR ss.source_path IN ({placeholders})
          )
    """,
        [session_id, *current_sources, *current_sources],
    )
    old_session_ids = [r["session_id"] for r in cursor.fetchall()]
    if not old_session_ids:
        return

    for item in dry_run_dir.iterdir():
        if report_filename and item.name == report_filename:
            continue

        for old_id in old_session_ids:
            if re.search(rf"(^|_)({re.escape(old_id)})(_|\.|$)", item.name):
                try:
                    if item.is_dir():
                        shutil.rmtree(item)
                    else:
                        item.unlink()
                except OSError:
                    pass
                break

    trash_root = get_local_system_dir(target_dir) / "trash"
    if trash_root.exists():
        for session_trash in trash_root.iterdir():
            if session_trash.name in old_session_ids:
                try:
                    shutil.rmtree(session_trash)
                except OSError:
                    pass


def sync_alias_library(db, alias_table: Dict[str, Tuple[str, float]], *, in_transaction: bool = False):
    """Seeds the SQLite database with aliases from the runtime alias table."""
    def _sync():
        db.conn.execute("DELETE FROM aliases WHERE source = 'system'")

        logging.info("Syncing ALIAS_TABLE from config/taxonomies to SQLite...")
        alias_list = []
        for alias, category_data in alias_table.items():
            category = category_data[0] if isinstance(category_data, (list, tuple)) else category_data
            weight = category_data[1] if isinstance(category_data, (list, tuple)) else 1.0
            alias_list.append((alias, category, weight, "system"))

        if alias_list:
            taxonomy_store.seed_aliases_bulk(db.conn, alias_list)
        logging.info("Successfully synced %d system aliases.", len(alias_list))

    if in_transaction:
        _sync()
    else:
        with db.write_transaction():
            _sync()


def sync_full_config(db, config: Dict[str, Any], *, in_transaction: bool = False):
    """Seeds all configuration structures into SQLite."""
    def _sync():
        config_lists = {
            "noise_word": config.get("NOISE_WORDS", []),
            "loop_indicator": config.get("LOOP_INDICATORS", []),
            "oneshot_indicator": config.get("ONESHOT_INDICATORS", []),
            "oneshot_hint_token": config.get("ONESHOT_HINT_TOKENS", []),
            "percussive_category": config.get("PERCUSSIVE_CATEGORIES", []),
            "weak_loop_indicator": config.get("WEAK_LOOP_INDICATORS", []),
        }
        for list_type, values in config_lists.items():
            db.conn.execute("DELETE FROM config_lists WHERE list_type = ?", (list_type,))
            if values:
                taxonomy_store.seed_config_list(db.conn, list_type, values)

        db.conn.execute("DELETE FROM suppression_rules")
        rules = config.get("CATEGORY_SUPPRESSION_RULES", {})
        if rules:
            taxonomy_store.seed_suppression_rules(db.conn, rules)

        db.conn.execute("DELETE FROM sub_taxonomy")
        rows = taxonomy_store.sub_taxonomy_rows(config.get("SUB_TAXONOMY_MAP", {}))
        if rows:
            taxonomy_store.seed_sub_taxonomy(db.conn, rows)

        logging.info("Configuration sync to SQLite complete.")

    if in_transaction:
        _sync()
    else:
        with db.write_transaction():
            _sync()
