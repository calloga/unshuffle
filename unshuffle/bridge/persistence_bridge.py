import json
from pathlib import Path

from ..core.constants import refresh_alias_structures
from ..core.logging import logger
from ..core.tags import parse_tags
from ..logic.classification import reset_scoring_engine
from ..logic.discovery import sync_taxonomy_to_db
from ..persistence.exports import build_metadata_backup, build_taxonomy_snapshot, export_metadata_backup, export_taxonomy_snapshot


def _int_result(value: object, default: int = 0) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, (float, str, bytes, bytearray)):
        try:
            return int(value)
        except (TypeError, ValueError):
            return default
    return default


class PersistenceBridge:
    """Bridge facade for staging/session persistence used by GUI controllers."""

    def __init__(self, workflow=None):
        self.workflow = workflow

    def _get_workflow(self):
        return self.workflow

    def _get_db(self):
        workflow = self._get_workflow()
        return getattr(workflow, "db", None) if workflow else None

    @property
    def engine(self):
        workflow = self._get_workflow()
        if not workflow:
            return None
        return getattr(workflow, "engine", workflow)

    @property
    def session_id(self):
        workflow = self._get_workflow()
        return workflow.session_id if workflow else None

    def has_session(self) -> bool:
        workflow = self._get_workflow()
        return bool(workflow and self._get_db() and workflow.session_id)

    def _warn_missing_session(self, action: str):
        logger.warning("PersistenceBridge.%s skipped because no active session is available.", action)

    def update_staging_record(self, row_id, record):
        if not self.has_session():
            self._warn_missing_session("update_staging_record")
            return
        tags_json = json.dumps(parse_tags(record.tags))
        self._get_db().update_staging_record(
            self.session_id,
            row_id,
            {
                "sample_name": record.source_path.name,
                "pack": record.pack,
                "category": record.category,
                "subcategory": record.subcategory or "",
                "tags": tags_json,
                "audio_type": record.audio_type,
            },
        )

    def get_committed_hashes(self):
        if not self.has_session():
            return set()
        return self._get_db().get_committed_hashes()

    def add_exclusion(self, path: str):
        if not self.has_session():
            self._warn_missing_session("add_exclusion")
            return
        self._get_db().add_exclusion(path)

    def get_session_sources(self, session_id):
        db = self._get_db()
        if not db:
            return []
        return db.get_session_sources(session_id)

    def get_staging_records(self, session_id):
        db = self._get_db()
        if not db:
            return []
        return db.get_staging_records(session_id)

    def get_token_adjustments(self):
        if not self.has_session():
            return {}
        return self._get_db().get_token_adjustments()

    def prune_unweighted_token_adjustments(self) -> int:
        if not self.has_session():
            self._warn_missing_session("prune_unweighted_token_adjustments")
            return 0
        prune = getattr(self._get_db(), "prune_unweighted_token_adjustments", None)
        return _int_result(prune() if callable(prune) else 0)

    def list_token_adjustments(self) -> list[tuple[str, str, float]]:
        adjustments = self.get_token_adjustments()
        rows: list[tuple[str, str, float]] = []
        for token, category_map in sorted(adjustments.items()):
            for category, offset in sorted(category_map.items()):
                rows.append((str(token), str(category), float(offset)))
        return rows

    def remove_token_adjustments(self, adjustment_keys: list[tuple[str, str]]) -> int:
        if not self.has_session():
            self._warn_missing_session("remove_token_adjustments")
            return 0
        db = self._get_db()
        return int(db.delete_token_adjustments(adjustment_keys))

    def update_token_adjustments_bulk(self, adjustment_list: list[tuple[str, str, float]]) -> int:
        if not self.has_session():
            self._warn_missing_session("update_token_adjustments_bulk")
            return 0
        rows = [
            (token, category, delta)
            for token, category, delta in adjustment_list or []
            if (token or "").strip() and (category or "").strip()
        ]
        if not rows:
            return 0
        self._get_db().update_token_adjustments_bulk(rows)
        return len(rows)

    def update_token_adjustments_from_events(self, event_list: list[tuple]) -> int:
        if not self.has_session():
            self._warn_missing_session("update_token_adjustments_from_events")
            return 0
        update = getattr(self._get_db(), "update_token_adjustments_from_events", None)
        return _int_result(update(event_list or []) if callable(update) else 0)

    def reset_adjustments(self):
        if not self.has_session():
            self._warn_missing_session("reset_adjustments")
            return
        self._get_db().reset_adjustments()

    def get_aliases_with_source(self):
        if not self.has_session():
            return {}
        return self._get_db().get_aliases_with_source()

    def get_aliases_by_source(self, source: str):
        if not self.has_session():
            return {}
        return self._get_db().get_aliases_by_source(source)

    def get_config_list(self, list_type: str):
        if not self.has_session():
            return []
        return self._get_db().get_config_list(list_type)

    def get_suppression_rules(self):
        if not self.has_session():
            return {}
        return self._get_db().get_suppression_rules()

    def get_sub_taxonomy(self):
        if not self.has_session():
            return {}
        return self._get_db().get_sub_taxonomy()

    def build_taxonomy_snapshot(self, taxonomy_dir):
        return build_taxonomy_snapshot(Path(taxonomy_dir))

    def export_taxonomy_snapshot(self, output_path, taxonomy_dir):
        return export_taxonomy_snapshot(Path(output_path), Path(taxonomy_dir))

    def build_metadata_backup(self):
        db = self._get_db()
        if db is None:
            return {}
        return build_metadata_backup(db)

    def export_metadata_backup(self, output_path):
        db = self._get_db()
        if db is None:
            self._warn_missing_session("export_metadata_backup")
            return None
        return export_metadata_backup(Path(output_path), db)

    def add_alias(self, alias: str, category: str, weight: float = 1.0, source: str = "discovery"):
        if not self.has_session():
            self._warn_missing_session("add_alias")
            return False
        alias_norm = (alias or "").strip().lower()
        if not alias_norm or not category:
            return False
        db = self._get_db()
        with db.write_transaction():
            db.seed_aliases_bulk([(alias_norm, category, weight, source)])
        self._refresh_runtime_aliases()
        return True

    def add_aliases_bulk(self, aliases: list[str], category: str, source: str = "user") -> int:
        if not self.has_session():
            self._warn_missing_session("add_aliases_bulk")
            return 0
        category_norm = (category or "").strip()
        rows = []
        seen = set()
        for alias in aliases:
            alias_norm = (alias or "").strip().lower()
            if not alias_norm or alias_norm in seen:
                continue
            seen.add(alias_norm)
            rows.append((alias_norm, category_norm, 1.0, source))
        if not rows or not category_norm:
            return 0
        db = self._get_db()
        with db.write_transaction():
            db.seed_aliases_bulk(rows)
        self._refresh_runtime_aliases()
        return len(rows)

    def get_user_additions(self) -> list[tuple[str, str, str]]:
        alias_map = self.get_aliases_by_source("user")
        return [(alias, category, "user") for alias, (category, _weight) in sorted(alias_map.items())]

    def remove_alias_if_source_allowed(self, alias: str, allowed_sources=("discovery", "user")) -> int:
        if not self.has_session():
            self._warn_missing_session("remove_alias_if_source_allowed")
            return 0
        db = self._get_db()
        sources = [str(source) for source in allowed_sources if source]
        if not sources:
            return 0
        placeholders = ", ".join("?" for _ in sources)
        with db.write_transaction():
            cursor = db.conn.execute(
                f"DELETE FROM aliases WHERE alias = ? AND source IN ({placeholders})",
                (alias, *sources),
            )
            removed = int(cursor.rowcount or 0)
        if removed:
            self._refresh_runtime_aliases()
        return removed

    def remove_aliases_if_source_allowed(self, aliases: list[str], allowed_sources=("user",)) -> int:
        removed = 0
        for alias in aliases:
            removed += self.remove_alias_if_source_allowed(alias, allowed_sources=allowed_sources)
        return removed

    def sync_taxonomy_from_files(self):
        if not self.has_session():
            self._warn_missing_session("sync_taxonomy_from_files")
            return {}
        db = self._get_db()
        return sync_taxonomy_to_db(db)

    def _refresh_runtime_aliases(self) -> None:
        db = self._get_db()
        if db is None:
            return
        refresh_alias_structures(db=db)
        reset_scoring_engine()
