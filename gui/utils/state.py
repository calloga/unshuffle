"""Shared state/invariant helpers for ModernApp mutations.

Expected `app` surface:
- engine/model/settings/library_tab
- view_controller, search_controller, and footer helpers
"""

from __future__ import annotations

import json

from unshuffle.core import tags_to_search_text


def build_staging_rows(records):
    rows = []
    for i, rec in enumerate(records):
        if hasattr(rec, "staging_row_id"):
            rec.staging_row_id = i
        tags_clean = tags_to_search_text(rec.tags)
        rows.append(
            (
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
                json.dumps(getattr(rec, "pack_candidates", []) or []),
                json.dumps(getattr(rec, "evidence", {}) or {}, default=str),
                getattr(rec, "feature_vector", None) or getattr(rec, "acoustic_vector", None),
                getattr(rec, "feature_space_version", None),
                getattr(rec, "feature_schema_json", None),
                getattr(rec, "analysis_status", None),
                getattr(rec, "analysis_tags_json", None),
                rec.preserved_root,
                rec.is_preserved,
            )
        )
    return rows


def rewrite_staging_from_model(app):
    if getattr(app, "_skip_db_write", False):
        return
    if not app.engine or not app.engine.db or not app.model:
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
    app.footer.set_count(f"{len(app.model.records)} files ready")
    if app.engine and hasattr(app.library_tab, "set_sources"):
        app.library_tab.set_sources(app.engine.session_source_roots)
    if refresh_search:
        app.search_controller.execute_search()
    else:
        app.view_controller.update_library_views(tree_delay_ms=tree_delay_ms)
