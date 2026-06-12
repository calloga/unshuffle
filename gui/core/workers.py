import logging
import gc
from functools import wraps
from collections import Counter
from pathlib import Path
from PySide6.QtCore import QThread, Signal
from unshuffle.core.paths import DB_FILE_NAME, SYSTEM_FOLDER_NAME
from ..models.library_tree import active_tree_levels_for_sort, build_tree_payload
from .search_engine import SearchEngine

def safe_gc_run(func):
    @wraps(func)
    def wrapper(self, *args, **kwargs):
        was_enabled = gc.isenabled()
        if was_enabled:
            gc.disable()
        try:
            return func(self, *args, **kwargs)
        finally:
            if was_enabled:
                gc.enable()
    return wrapper


def _staging_session_has_rows(db_conn, session_id: str) -> bool:
    session_id = (session_id or "").strip()
    if not session_id:
        return False
    conn = getattr(db_conn, "conn", None)
    if conn is not None:
        try:
            row = conn.execute(
                "SELECT 1 FROM staging_records WHERE session_id = ? LIMIT 1",
                (session_id,),
            ).fetchone()
            return row is not None
        except Exception:
            logging.debug("Could not check staging row count for restore session.", exc_info=True)
    try:
        return bool(db_conn.get_staging_records(session_id))
    except Exception:
        logging.debug("Could not load staging rows while validating restore session.", exc_info=True)
        return False


def _resolve_restore_session_id(db_conn, target: Path, requested_session_id: str) -> str:
    requested_session_id = (requested_session_id or "").strip()
    if _staging_session_has_rows(db_conn, requested_session_id):
        return requested_session_id
    if hasattr(db_conn, "newest_restorable_staging_session"):
        return str(db_conn.newest_restorable_staging_session(target) or "")
    return ""


def _open_restore_db(target: Path, requested_session_id: str):
    from unshuffle.persistence import get_db, get_local_db

    target = Path(target)
    candidates = []
    if (target / SYSTEM_FOLDER_NAME / DB_FILE_NAME).exists():
        candidates.append(("local", get_local_db(target)))
    candidates.append(("global", get_db(target)))

    fallback = None
    fallback_session_id = ""
    fallback_scope = "global"
    for scope, db_conn in candidates:
        session_id = _resolve_restore_session_id(db_conn, target, requested_session_id)
        if session_id:
            if requested_session_id and session_id == (requested_session_id).strip():
                if fallback is not None:
                    fallback.close()
                return db_conn, session_id, scope
            if fallback is None:
                fallback = db_conn
                fallback_session_id = session_id
                fallback_scope = scope
                continue
        db_conn.close()
    if fallback is not None:
        return fallback, fallback_session_id, fallback_scope
    return get_db(target), "", "global"


class ScanWorker(QThread):
    """Background worker that runs the engine's scan phase."""
    progress = Signal(dict)
    finished = Signal(list, bool, dict)
    error = Signal(str)

    def __init__(self, engine, sources, acoustic_index=False, skip_expensive_hashes=None, min_confidence=None, append=False, existing_hashes=None, lib_hashes=None, current_records=None):
        super().__init__()
        self.engine = engine
        self.sources = sources
        self.acoustic_index = acoustic_index
        self.min_confidence = min_confidence
        self.skip_expensive_hashes = set(skip_expensive_hashes or ())
        self.append = append
        self.existing_hashes = set(existing_hashes or ())
        self.lib_hashes = set(lib_hashes or ())
        self.current_records = list(current_records or ())

    @safe_gc_run
    def run(self):
        try:
            self.engine.progress_callback = lambda d: self.progress.emit(d)
            plan = self.engine.prepare_plan(
                self.sources,
                acoustic_index=self.acoustic_index,
                skip_expensive_hashes=self.skip_expensive_hashes,
                min_confidence=self.min_confidence,
            )
            if getattr(self.engine, "interrupted", False) is True:
                self.finished.emit(
                    [],
                    self.append,
                    {
                        "total_scanned": 0,
                        "added_count": 0,
                        "lib_dupe_count": 0,
                        "session_dupe_count": 0,
                        "total_dupe_count": 0,
                        "cancelled": True,
                    },
                )
                return
            
            from .workflow_records import dedupe_plan_records, scan_duplicate_stats
            from .workflow_controller import scan_category_counts
            
            new_records, lib_dupe_count, session_dupe_count = dedupe_plan_records(
                plan, self.existing_hashes, self.lib_hashes
            )
            stats = scan_duplicate_stats(plan, new_records, lib_dupe_count, session_dupe_count)
            stats["category_counts"] = scan_category_counts(plan)
            
            from unshuffle.persistence import get_db
            db_conn = get_db(self.engine.target_dir)
            try:
                source_dir = self.sources[0] if self.sources else self.engine.target_dir
                db_conn.register_session(
                    self.engine.session_id,
                    source=source_dir,
                    target=self.engine.target_dir,
                    mode="pending"
                )
                db_conn.clear_staging(self.engine.session_id)
                if hasattr(db_conn, "ensure_verified_anchors_for_session"):
                    db_conn.ensure_verified_anchors_for_session(self.engine.session_id)
                from gui.utils.state import build_staging_rows
                all_records = list(self.current_records) + new_records if self.append else new_records
                if hasattr(db_conn, "list_coherence_review_decisions"):
                    from .coherence_review_decisions import apply_target_review_decisions

                    applied_count = apply_target_review_decisions(db_conn, all_records)
                    if applied_count and hasattr(self.engine, "log"):
                        self.engine.log(f"Applied {applied_count} remembered outlier review field change(s).")
                rows = build_staging_rows(all_records)
                if rows:
                    db_conn.add_staging_records_bulk(self.engine.session_id, rows)
                try:
                    if hasattr(db_conn, "prune_ephemeral_state"):
                        db_conn.prune_ephemeral_state({self.engine.session_id}, target_root=self.engine.target_dir)
                except Exception:
                    logging.debug("Post-scan database maintenance skipped.", exc_info=True)
            finally:
                db_conn.close()
            
            self.finished.emit(new_records, self.append, stats)
        except Exception as e:
            logging.exception("ScanWorker encountered an error")
            self.error.emit(str(e))
        finally:
            if getattr(self.engine, "progress_callback", None):
                self.engine.progress_callback = None

class CommitWorker(QThread):
    """Executes the planned file operations (move/copy)."""
    progress = Signal(dict)
    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, engine, plan, move, dry_run, flat, no_px):
        super().__init__()
        self.engine = engine
        self.plan = plan
        self.move = move
        self.dry_run = dry_run
        self.flat = flat
        self.no_px = no_px

    @safe_gc_run
    def run(self):
        try:
            self.engine.progress_callback = lambda d: self.progress.emit(d)
            res = self.engine.execute_plan(self.plan, self.move, self.dry_run, self.flat, self.no_px)
            self.finished.emit(res)
        except Exception as e:
            logging.exception("CommitWorker encountered an error")
            self.error.emit(str(e))
        finally:
            if getattr(self.engine, "progress_callback", None):
                self.engine.progress_callback = None

class UndoWorker(QThread):
    """Reverts a previously committed session."""
    progress = Signal(dict)
    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, engine, session_id, confirm_preserved=False):
        super().__init__()
        self.engine = engine
        self.session_id = session_id
        self.confirm_preserved = confirm_preserved

    @safe_gc_run
    def run(self):
        try:
            self.progress.emit({"message": "Preparing undo...", "current": 0, "total": 0})
            self.engine.progress_callback = lambda d: self.progress.emit(d)
            res = self.engine.undo_session(self.session_id, confirm_preserved=self.confirm_preserved)
            self.finished.emit(res)
        except Exception as e:
            logging.exception("UndoWorker encountered an error")
            self.error.emit(str(e))
        finally:
            if getattr(self.engine, "progress_callback", None):
                self.engine.progress_callback = None


class TreeRebuildWorker(QThread):
    """Builds grouped tree payloads off the UI thread."""
    finished = Signal(dict)
    error = Signal(str)

    def __init__(
        self,
        request_id,
        records,
        skip_fields,
        sort_column,
        confidence_min,
        confidence_max,
        highlight,
    ):
        super().__init__()
        self.request_id = request_id
        self.records = list(records)
        self.skip_fields = set(skip_fields or set())
        self.sort_column = sort_column
        self.confidence_min = confidence_min
        self.confidence_max = confidence_max
        self.highlight = str(highlight or "")

    @safe_gc_run
    def run(self):
        try:
            levels = [
                (field, node_type)
                for field, node_type in active_tree_levels_for_sort(self.sort_column)
                if field not in self.skip_fields
            ]
            payload = build_tree_payload(
                self.records,
                levels,
                self.confidence_min,
                self.confidence_max,
            ) if levels else list(self.records)
            self.finished.emit(
                {
                    "request_id": self.request_id,
                    "levels": levels,
                    "payload": payload,
                    "records": self.records,
                    "highlight": self.highlight,
                }
            )
        except Exception as exc:
            logging.exception("TreeRebuildWorker encountered an error")
            self.error.emit(str(exc))


class SearchWorker(QThread):
    """Executes staging DB searches off the UI thread."""
    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, request_id, bridge=None, query_text=""):
        super().__init__()
        self.request_id = request_id
        self.bridge = bridge
        self.query_text = str(query_text or "")

    @safe_gc_run
    def run(self):
        try:
            matched_ids = SearchEngine.run_query(self.bridge, self.query_text)
            self.finished.emit(
                {
                    "request_id": self.request_id,
                    "query_text": self.query_text,
                    "matched_ids": matched_ids,
                }
            )
        except Exception as exc:
            logging.exception("SearchWorker encountered an error")
            self.error.emit(str(exc))


class SimilarityWorker(QThread):
    """Calculates acoustic similarity distances off the UI thread."""
    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, request_id, anchor_row, anchor_blob, anchor_duration, candidates):
        super().__init__()
        self.request_id = request_id
        self.anchor_row = int(anchor_row)
        self.anchor_blob = anchor_blob
        self.anchor_duration = float(anchor_duration or 0.0)
        self.candidates = list(candidates)

    @safe_gc_run
    def run(self):
        try:
            from unshuffle.audio import SimilarityEngine

            engine = SimilarityEngine()
            anchor_vec = SimilarityEngine.vector_from_blob(self.anchor_blob)
            if not anchor_vec:
                self.finished.emit(
                    {
                        "request_id": self.request_id,
                        "anchor_row": self.anchor_row,
                        "distances": {},
                        "avg_dist": 0.0,
                    }
                )
                return

            distances = {}
            for row, blob, duration in self.candidates:
                vec = SimilarityEngine.vector_from_blob(blob)
                if not vec:
                    continue
                distances[int(row)] = engine.calculate_distance(
                    anchor_vec,
                    vec,
                    d1=self.anchor_duration,
                    d2=float(duration or 0.0),
                )

            all_dists = [dist for row, dist in distances.items() if row != self.anchor_row]
            avg_dist = (sum(all_dists) / len(all_dists)) if all_dists else 0.0
            self.finished.emit(
                {
                    "request_id": self.request_id,
                    "anchor_row": self.anchor_row,
                    "distances": distances,
                    "avg_dist": avg_dist,
                }
            )
        except Exception as exc:
            logging.exception("SimilarityWorker encountered an error")
            self.error.emit(str(exc))


class TaggingWorker(QThread):
    """Computes secondary generated tags without blocking the library UI."""
    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, request_id, records):
        super().__init__()
        self.request_id = int(request_id)
        self.records = list(records)

    @safe_gc_run
    def run(self):
        try:
            from unshuffle.logic.tagging import compute_tagging_pass

            result = compute_tagging_pass(self.records, include_genres=False)
            self.finished.emit(
                {
                    "request_id": self.request_id,
                    "tags_by_path": result.tags_by_path,
                    "duplicate_matches": [
                        {
                            "left_path": match.left_path,
                            "right_path": match.right_path,
                            "distance": match.distance,
                        }
                        for match in result.duplicate_matches
                    ],
                    "duplicate_file_count": result.duplicate_file_count,
                }
            )
        except Exception as exc:
            logging.exception("TaggingWorker encountered an error")
            self.error.emit(str(exc))


class CoherenceWorker(QThread):
    """Runs the post-classification coherence audit without blocking the UI."""
    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, request_id, db, session_id, force=False):
        super().__init__()
        self.request_id = int(request_id)
        self.db = db
        self.session_id = str(session_id or "")
        self.force = bool(force)

    @safe_gc_run
    def run(self):
        try:
            from unshuffle.logic.coherence import run_coherence_audit

            summary = run_coherence_audit(self.db, self.session_id, force=self.force)
            self.finished.emit(
                {
                    "request_id": self.request_id,
                    "ran": summary.ran,
                    "reason": summary.reason,
                    "total_records": summary.total_records,
                    "eligible_records": summary.eligible_records,
                    "valid_vector_records": summary.valid_vector_records,
                    "coverage": summary.coverage,
                    "result_count": summary.result_count,
                    "pending_candidate_count": summary.pending_candidate_count,
                    "auto_staged_candidate_count": summary.auto_staged_candidate_count,
                    "anchor_candidate_count": summary.anchor_candidate_count,
                }
            )
        except Exception as exc:
            logging.exception("CoherenceWorker encountered an error")
            self.error.emit(str(exc))


class SessionLoadWorker(QThread):
    """Loads persisted staging-session data off the UI thread."""
    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, target, session_id):
        super().__init__()
        self.target = str(target or "")
        self.session_id = str(session_id or "")

    @safe_gc_run
    def run(self):
        try:
            from unshuffle.persistence import get_db
            from ..utils.history import invalidate_history_cache, load_session_sources, load_staging_records
            from ..utils.session import plan_records_from_staging

            session_id = self.session_id
            db_scope = "global"
            if self.target:
                db_conn, session_id, db_scope = _open_restore_db(Path(self.target), session_id)
                try:
                    try:
                        if hasattr(db_conn, "prune_ephemeral_state"):
                            db_conn.prune_ephemeral_state({session_id} if session_id else set(), target_root=Path(self.target))
                            invalidate_history_cache(self.target)
                    except Exception:
                        logging.debug("Session-load database maintenance skipped.", exc_info=True)
                finally:
                    db_conn.close()

            records = load_staging_records(self.target, session_id) if session_id else []
            sources = load_session_sources(self.target, session_id) if session_id else []
            plan = plan_records_from_staging(records)
            self.finished.emit(
                {
                    "session_id": session_id,
                    "records": records,
                    "sources": sources,
                    "plan": plan,
                    "db_scope": db_scope,
                }
            )
        except Exception as exc:
            logging.exception("SessionLoadWorker encountered an error")
            self.error.emit(str(exc))


class StartupRestoreWorker(QThread):
    """Loads previous session data off the UI thread."""
    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, target, session_id):
        super().__init__()
        self.target = str(target or "")
        self.session_id = str(session_id or "")

    @safe_gc_run
    def run(self):
        try:
            from unshuffle.persistence import get_db
            from ..utils.history import invalidate_history_cache, load_session_sources, load_staging_records
            from ..utils.session import plan_records_from_staging

            session_id = self.session_id
            db_scope = "global"
            if self.target:
                db_conn, session_id, db_scope = _open_restore_db(Path(self.target), session_id)
                try:
                    try:
                        if hasattr(db_conn, "prune_ephemeral_state"):
                            db_conn.prune_ephemeral_state({session_id} if session_id else set(), target_root=Path(self.target))
                            invalidate_history_cache(self.target)
                    except Exception:
                        logging.debug("Startup database maintenance skipped.", exc_info=True)
                finally:
                    db_conn.close()

            records = load_staging_records(self.target, session_id) if session_id else []
            sources = load_session_sources(self.target, session_id) if session_id else []
            plan = plan_records_from_staging(records) if records else []
            self.finished.emit(
                {
                    "session_id": session_id,
                    "target": self.target,
                    "sources": sources,
                    "plan": plan,
                    "db_scope": db_scope,
                }
            )
        except Exception as exc:
            logging.exception("StartupRestoreWorker encountered an error")
            self.error.emit(str(exc))


class DraftImpactWorker(QThread):
    """Calculates draft-impact summary text off the UI thread."""
    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, request_id, originals_snapshot, conflicts):
        super().__init__()
        self.request_id = int(request_id)
        self.originals_snapshot = list(originals_snapshot)
        self.conflicts = int(conflicts or 0)

    @safe_gc_run
    def run(self):
        try:
            from ..utils.constants import StagingColumn

            field_counter = Counter()
            changed_records = set()
            changed_fields = len(self.originals_snapshot)
            for rec_id, col_idx in self.originals_snapshot:
                changed_records.add(rec_id)
                if col_idx == StagingColumn.TYPE:
                    field_counter["type"] += 1
                elif col_idx == StagingColumn.CATEGORY:
                    field_counter["category"] += 1
                elif col_idx == StagingColumn.PACK:
                    field_counter["pack"] += 1
                else:
                    field_counter["other"] += 1

            parts = [
                f"{len(changed_records)} record{'s' if len(changed_records) != 1 else ''}",
                f"{changed_fields} field change{'s' if changed_fields != 1 else ''}",
            ]
            breakdown = ", ".join(
                f"{name}:{count}"
                for name, count in (
                    ("type", field_counter.get("type", 0)),
                    ("category", field_counter.get("category", 0)),
                    ("pack", field_counter.get("pack", 0)),
                )
                if count
            )
            if breakdown:
                parts.append(f"breakdown {breakdown}")
            if self.conflicts:
                parts.append(f"{self.conflicts} new potential collision(s)")

            self.finished.emit(
                {
                    "request_id": self.request_id,
                    "summary": "; ".join(parts) + ".",
                }
            )
        except Exception as exc:
            logging.exception("DraftImpactWorker encountered an error")
            self.error.emit(str(exc))
