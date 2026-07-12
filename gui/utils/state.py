"""Shared state/invariant helpers for ModernApp mutations.

Expected `app` surface:
- engine/model/settings/library_tab
- view_controller, search_controller, and footer helpers
"""

from __future__ import annotations

import json

from unshuffle.core import parse_tags, tags_to_search_text


def iter_staging_rows(records, *, start_index: int = 0):
    for i, rec in enumerate(records, start=max(0, int(start_index))):
        if hasattr(rec, "staging_row_id"):
            rec.staging_row_id = i
        evidence = dict(getattr(rec, "evidence", {}) or {})
        if getattr(rec, "is_duplicate_shadow", False) is True:
            evidence["duplicate_shadow"] = {
                "is_shadow": True,
                "duplicate_of_hash": getattr(rec, "duplicate_of_hash", None),
                "duplicate_of_path": str(getattr(rec, "duplicate_of_path", "")) if getattr(rec, "duplicate_of_path", None) else None,
            }
        else:
            evidence.pop("duplicate_shadow", None)
        tags_clean = tags_to_search_text(rec.tags)
        yield (
                i,
                str(rec.source_path),
                rec.source_path.name,
                rec.pack,
                rec.category,
                rec.subcategory or "",
                rec.audio_type,
                tags_clean,
                rec.confidence,
                rec.duration,
                rec.hash or "",
                getattr(rec, "fast_hash", None),
                json.dumps(getattr(rec, "pack_candidates", []) or []),
                json.dumps(evidence, default=str),
                getattr(rec, "feature_vector", None) or getattr(rec, "acoustic_vector", None),
                getattr(rec, "feature_space_version", None),
                getattr(rec, "feature_schema_json", None),
                getattr(rec, "analysis_status", None),
                getattr(rec, "analysis_tags_json", None),
                rec.preserved_root,
                rec.is_preserved,
        )


def build_staging_rows(records, *, start_index: int = 0):
    """Compatibility helper for callers that explicitly need a materialized list."""
    return list(iter_staging_rows(records, start_index=start_index))


def iter_scan_item_staging_rows(row_batches, *, start_index: int = 0):
    row_id = max(0, int(start_index))
    for batch in row_batches:
        for item in batch:
            is_shadow = int(item.get("duplicate_rank") or 1) > 1
            try:
                evidence = json.loads(item.get("evidence_json") or "{}")
            except (TypeError, json.JSONDecodeError):
                evidence = {}
            if not isinstance(evidence, dict):
                evidence = {}
            tags = parse_tags(item.get("tags"))
            if is_shadow:
                tags = [*tags, "duplicate"]
                evidence["duplicate_shadow"] = {
                    "is_shadow": True,
                    "duplicate_of_hash": item.get("canonical_hash"),
                    "duplicate_of_path": item.get("canonical_path"),
                }

            def inherited(name, default=None):
                if is_shadow and item.get(f"canonical_{name}") is not None:
                    return item.get(f"canonical_{name}")
                value = item.get(name)
                return default if value is None else value

            yield (
                row_id,
                item["normalized_path"],
                item["sample_name"],
                inherited("pack", ""),
                inherited("category", "Uncategorized"),
                inherited("subcategory", ""),
                inherited("audio_type", "Oneshots"),
                tags_to_search_text(tags),
                inherited("confidence", "0.0"),
                inherited("duration", 0.0),
                item.get("effective_hash") or "",
                item.get("fast_hash"),
                inherited("pack_candidates", "[]"),
                json.dumps(evidence, default=str),
                item.get("feature_vector"),
                item.get("feature_space_version"),
                item.get("feature_schema_json"),
                inherited("analysis_status"),
                inherited("analysis_tags_json", "[]"),
                None,
                False,
            )
            row_id += 1


def rewrite_staging_from_model(app):
    if getattr(app, "_skip_db_write", False):
        return
    if not app.engine or not app.engine.db or not app.model:
        return
    if getattr(app, "session_store", None) is not None and hasattr(app.model, "refresh_index"):
        app.settings.setValue("last_scan_session_id", app.engine.session_id)
        app.settings.setValue("last_target", str(app.engine.target_dir))
        return
    sid = app.engine.session_id
    
    source = app.engine.session_source_roots[0] if app.engine.session_source_roots else app.engine.target_dir
    app.engine.db.register_session(
        sid,
        source=source,
        target=app.engine.target_dir,
        mode="pending"
    )
    
    app.engine.db.clear_staging(sid)
    rows = build_staging_rows(app.model.records)
    if rows:
        app.engine.db.add_staging_records_bulk(sid, rows)
    app.settings.setValue("last_scan_session_id", sid)
    app.settings.setValue("last_target", str(app.engine.target_dir))


def finalize_model_mutation(app, *, resort=False, refresh_search=True, tree_delay_ms=0):
    if not app.model:
        return
    if getattr(app, "tagging_controller", None):
        app.tagging_controller.clear_state()
    if resort:
        app.view_controller.apply_current_sort_state(force=True)
    rewrite_staging_from_model(app)
    app.footer.set_count(f"{app.model.rowCount()} files ready")
    if app.engine and hasattr(app.library_tab, "set_sources"):
        app.library_tab.set_sources(app.engine.session_source_roots)
    if refresh_search:
        app.search_controller.execute_search()
    else:
        app.view_controller.update_library_views(tree_delay_ms=tree_delay_ms)
