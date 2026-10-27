"""Source-folder preflight shared by startup restore and recent-session loading."""
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from unshuffle.persistence import UnshuffleDB
from unshuffle.persistence.stores.session_store import set_session_sources

from .data_manager import (
    paths_refer_to_same_location,
    remap_imported_source_path,
    remap_imported_staging_row,
)


def prepare_saved_session_sources(app, payload: dict) -> dict | None:
    """Prompt only for missing roots, then persist confirmed replacements in place.

    Cancellation happens before any writes. Keep the import preflight's existing
    root-existence policy; this does not inspect or discard missing sample files.
    """
    sources = [str(source) for source in payload.get("sources") or [] if str(source).strip()]
    if not any(not Path(source).exists() for source in sources):
        return payload
    parent = (getattr(app, "_startup_launch_refs", None) or {}).get("splash") or app
    remaps = app.data_manager._prompt_source_remaps(sources, parent_widget=parent)
    if remaps is None:
        return None
    changed = {
        source: Path(replacement)
        for source, replacement in remaps.items()
        if not paths_refer_to_same_location(source, replacement)
    }
    if not changed:
        return payload
    if any(not replacement.is_dir() for replacement in changed.values()):
        raise ValueError("A selected replacement source folder is unavailable.")
    db_path = payload.get("db_path")
    if not db_path or not Path(db_path).is_file():
        raise ValueError("The saved session database is unavailable for relinking.")
    session_id = str(payload.get("session_id") or "")
    remapped_sources = list(dict.fromkeys(
        str(remap_imported_source_path(source, changed)) for source in sources
    ))

    cursor_set = isinstance(QApplication.instance(), QApplication)
    if cursor_set:
        QApplication.setOverrideCursor(Qt.WaitCursor)
    try:
        database = UnshuffleDB(Path(db_path))
        try:
            _relink_staging_sources(database, session_id, changed, remapped_sources)
        finally:
            database.close()
    finally:
        if cursor_set:
            QApplication.restoreOverrideCursor()

    updated: dict = dict(payload)
    updated["sources"] = remapped_sources
    if payload.get("target"):
        updated["target"] = str(remap_imported_source_path(payload["target"], changed))
    # Actual loaders use the DB-backed model. Avoid retaining stale fallback rows.
    if payload.get("plan"):
        updated["plan"] = []
    return updated


def _relink_staging_sources(database, session_id: str, remaps: dict, sources: list[str]) -> None:
    """Change path references only; retain row IDs, analysis, decisions, and history."""
    conn = database.conn
    with database.write_transaction():
        session = conn.execute("SELECT source_path, target_root FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
        if session is None:
            raise ValueError("The saved session no longer exists.")
        cursor = conn.execute(
            "SELECT id, source_path, preserved_root, evidence_json FROM staging_records WHERE session_id = ? ORDER BY id",
            (session_id,),
        )
        while rows := cursor.fetchmany(1000):
            updates = []
            for row in rows:
                original = dict(row)
                mapped = remap_imported_staging_row(original, remaps)
                mapped["source_path"] = Path(mapped["source_path"]).as_posix()
                if mapped.get("preserved_root"):
                    mapped["preserved_root"] = Path(mapped["preserved_root"]).as_posix()
                updates.append((mapped["source_path"], mapped.get("preserved_root"), mapped.get("evidence_json"), row["id"], session_id))
                if mapped["source_path"] != row["source_path"]:
                    # Decisions are shared by path across sessions. Copy to the new
                    # path without deleting the old decision or replacing a newer one.
                    conn.execute(
                        """INSERT OR IGNORE INTO coherence_review_decisions (
                            source_path, file_hash, decision_type, current_audio_type, current_category,
                            current_subcategory, target_audio_type, target_category, target_subcategory,
                            created_session_id, created_at, updated_at)
                        SELECT ?, file_hash, decision_type, current_audio_type, current_category,
                               current_subcategory, target_audio_type, target_category, target_subcategory,
                               created_session_id, created_at, updated_at
                        FROM coherence_review_decisions WHERE source_path = ?""",
                        (Path(mapped["source_path"]).as_posix(), Path(row["source_path"]).as_posix()),
                    )
            conn.executemany(
                "UPDATE staging_records SET source_path = ?, preserved_root = ?, evidence_json = ? WHERE id = ? AND session_id = ?",
                updates,
            )
        conn.execute(
            "UPDATE sessions SET source_path = ?, target_root = ? WHERE session_id = ?",
            tuple(str(remap_imported_source_path(value, remaps)) if value else value for value in session) + (session_id,),
        )
        set_session_sources(conn, session_id, [Path(source) for source in sources])
