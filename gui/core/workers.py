import logging
import gc
import hashlib
import os
from functools import wraps
from collections import Counter
from pathlib import Path
from PySide6.QtCore import QThread, Signal
from unshuffle.core.paths import DB_FILE_NAME, SYSTEM_FOLDER_NAME
from unshuffle.core.progress import PhaseProgress
from unshuffle.core.resource_monitor import ResourceMonitor
from unshuffle.diagnostics import write_launcher_event_log
from ..models.library_tree import active_tree_levels_for_sort, build_tree_payload
from . import workflow_build_completion
from .search_engine import SearchEngine
from .workflow_summary import remaining_source_footprint

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


def _streaming_scan_enabled() -> bool:
    value = str(os.getenv("UNSHUFFLE_STREAMING_SCAN", "1") or "1").strip().lower()
    return value not in {"0", "false", "no", "off"}


def _report_scan_timing(engine, timing_summary: dict[str, object]) -> None:
    try:
        log_timing = getattr(engine, "log", None)
        if callable(log_timing):
            log_timing(f"Scan performance timing: {timing_summary}")
    except Exception:
        logging.debug("Could not write scan performance timing to the session log.", exc_info=True)
    write_launcher_event_log("scan-performance-timing", **timing_summary)


def _db_native_scan_available(db_conn, *, append: bool) -> bool:
    return bool(
        _streaming_scan_enabled()
        and db_conn is not None
        and hasattr(db_conn, "iter_classified_scan_session_items")
        and hasattr(db_conn, "classified_scan_session_stats")
        and hasattr(db_conn, "add_staging_records_iter")
        and (not append or hasattr(db_conn, "iter_classified_append_items"))
    )


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


def _staging_session_row_count(db_conn, session_id: str) -> int:
    session_id = (session_id or "").strip()
    if not session_id:
        return 0
    conn = getattr(db_conn, "conn", None)
    if conn is not None:
        row = conn.execute(
            "SELECT COUNT(*) FROM staging_records WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        return int(row[0] if row is not None else 0)
    if hasattr(db_conn, "get_staging_records"):
        return len(db_conn.get_staging_records(session_id))
    return -1


def _resolve_restore_session_id(db_conn, target: Path, requested_session_id: str) -> str:
    requested_session_id = (requested_session_id or "").strip()
    if _staging_session_has_rows(db_conn, requested_session_id):
        return requested_session_id
    if hasattr(db_conn, "newest_restorable_staging_session"):
        return str(db_conn.newest_restorable_staging_session(target) or "")
    return ""


def _open_restore_db(
    target: Path,
    requested_session_id: str,
    *,
    local_db=None,
    global_db=None,
):
    from unshuffle.persistence import get_db, get_local_db

    target = Path(target)
    candidates = []
    if (target / SYSTEM_FOLDER_NAME / DB_FILE_NAME).exists():
        candidates.append(("local", local_db, lambda: get_local_db(target)))
    candidates.append(("global", global_db, lambda: get_db(target)))

    fallback = None
    fallback_session_id = ""
    fallback_scope = "global"
    fallback_owned = False
    for scope, existing_db, database_factory in candidates:
        db_conn = existing_db
        owned = False
        if db_conn is None:
            db_conn = database_factory()
            owned = True
        session_id = _resolve_restore_session_id(db_conn, target, requested_session_id)
        if session_id:
            if requested_session_id and session_id == (requested_session_id).strip():
                if fallback is not None and fallback_owned:
                    fallback.close()
                return db_conn, session_id, scope, owned
            if fallback is None:
                fallback = db_conn
                fallback_session_id = session_id
                fallback_scope = scope
                fallback_owned = owned
                continue
        if scope == "global" and fallback is None:
            return db_conn, "", "global", owned
        if owned:
            db_conn.close()
    if fallback is not None:
        return fallback, fallback_session_id, fallback_scope, fallback_owned
    if global_db is not None:
        return global_db, "", "global", False
    return get_db(target), "", "global", True


class ScanWorker(QThread):
    """Background worker that runs the engine's scan phase."""
    progress = Signal(dict)
    finished = Signal(dict)
    error = Signal(str)

    def __init__(
        self,
        engine,
        sources,
        acoustic_index=False,
        skip_expensive_hashes=None,
        min_confidence=None,
        append=False,
        existing_hashes=None,
        lib_hashes=None,
        current_records=None,
        session_phase=None,
    ):
        super().__init__()
        self.engine = engine
        self.sources = sources
        self.acoustic_index = acoustic_index
        self.min_confidence = min_confidence
        self.skip_expensive_hashes = set(skip_expensive_hashes or ())
        self.append = append
        self.session_phase = session_phase or (
            "Updating Session" if append else "Creating Session"
        )
        self.existing_hashes = existing_hashes
        self.lib_hashes = set(lib_hashes or ())
        self.current_records = current_records or ()

    def run(self):
        operation_kind = (
            "refresh"
            if self.session_phase == "Refreshing Session"
            else "append"
            if self.append
            else "fresh"
        )
        monitor = ResourceMonitor(f"scan-{operation_kind}")
        monitor.start()
        try:
            def report_progress(payload):
                monitor.set_phase(payload.get("phase"))
                self.progress.emit(payload)

            self.engine.progress_callback = report_progress
            engine_db = getattr(self.engine, "db", None)
            db_native_scan = _db_native_scan_available(engine_db, append=self.append)
            scan_ids = [
                f"{self.engine.session_id}:"
                f"{hashlib.sha1(str(Path(source).resolve()).encode('utf-8')).hexdigest()[:12]}"
                for source in self.sources
            ]
            plan = self.engine.prepare_plan(
                self.sources,
                acoustic_index=self.acoustic_index,
                skip_expensive_hashes=self.skip_expensive_hashes,
                min_confidence=self.min_confidence,
                collect_records=not db_native_scan,
            )
            if getattr(self.engine, "interrupted", False) is True:
                if engine_db is not None and hasattr(engine_db, "update_session_scan_runs"):
                    engine_db.update_session_scan_runs(self.engine.session_id, state="paused")
                self.finished.emit(
                    {
                        "records": [],
                        "append": self.append,
                        "stats": {
                            "total_scanned": 0,
                            "added_count": 0,
                            "lib_dupe_count": 0,
                            "session_dupe_count": 0,
                            "total_dupe_count": 0,
                            "cancelled": True,
                        },
                    },
                )
                return
            
            from .workflow_records import dedupe_plan_records, scan_duplicate_stats
            from .workflow_controller import scan_category_counts
            duplicate_progress = PhaseProgress(
                self.engine.progress_callback,
                "Finding Duplicates",
                total=max(1, len(plan)),
                message="Finding duplicates...",
            )
            duplicate_progress.emit(0, force=True)
            if db_native_scan:
                if self.append:
                    append_total = 0
                    append_duplicates = 0
                    append_categories = Counter()
                    for batch in engine_db.iter_classified_append_items(
                        self.engine.session_id,
                        scan_ids,
                        batch_size=1000,
                    ):
                        append_total += len(batch)
                        append_duplicates += sum(int(row.get("duplicate_rank") or 1) > 1 for row in batch)
                        append_categories.update(
                            "Duplicates"
                            if int(row.get("duplicate_rank") or 1) > 1
                            else str(row.get("category") or "Uncategorized")
                            for row in batch
                        )
                    persisted_stats = {
                        "total": append_total,
                        "duplicates": append_duplicates,
                        "category_counts": dict(append_categories),
                    }
                else:
                    persisted_stats = engine_db.classified_scan_session_stats(self.engine.session_id)
                new_records = list(plan)
                lib_dupe_count = 0
                session_dupe_count = int(persisted_stats.get("duplicates", 0) or 0)
            else:
                new_records, lib_dupe_count, session_dupe_count = dedupe_plan_records(
                    plan, self.existing_hashes, self.lib_hashes
                )
            duplicate_progress.emit(max(1, len(plan)), force=True)
            if db_native_scan:
                classified_total = int(persisted_stats.get("total", 0) or 0)
                stats = {
                    "total_scanned": classified_total + len(new_records),
                    "added_count": classified_total - session_dupe_count + len(new_records),
                    "lib_dupe_count": 0,
                    "session_dupe_count": session_dupe_count,
                    "total_dupe_count": session_dupe_count,
                    "category_counts": dict(persisted_stats.get("category_counts") or {}),
                }
                for category, count in scan_category_counts(new_records).items():
                    stats["category_counts"][category] = stats["category_counts"].get(category, 0) + count
            else:
                stats = scan_duplicate_stats(plan, new_records, lib_dupe_count, session_dupe_count)
                stats["category_counts"] = scan_category_counts(plan)
            
            db_conn = getattr(self.engine, "db", None)
            owns_db_conn = False
            if db_conn is None:
                from unshuffle.persistence import get_db

                db_conn = get_db(self.engine.target_dir)
                owns_db_conn = True
            try:
                session_progress = PhaseProgress(
                    self.engine.progress_callback,
                    self.session_phase,
                    total=6,
                    message=f"{self.session_phase}...",
                    update_every=1,
                )
                session_progress.emit(0, force=True)
                source_dir = self.sources[0] if self.sources else self.engine.target_dir
                db_conn.register_session(
                    self.engine.session_id,
                    source=source_dir,
                    target=self.engine.target_dir,
                    mode="pending"
                )
                session_progress.emit(1)
                if not self.append:
                    db_conn.clear_staging(self.engine.session_id)
                session_progress.emit(2)
                if not self.append and hasattr(db_conn, "ensure_verified_anchors_for_session"):
                    db_conn.ensure_verified_anchors_for_session(self.engine.session_id)
                session_progress.emit(3)
                from gui.utils.state import iter_scan_item_staging_rows, iter_staging_rows
                all_records = new_records
                if not db_native_scan and hasattr(db_conn, "list_coherence_review_decisions"):
                    from .coherence_review_decisions import apply_target_review_decisions

                    applied_count = apply_target_review_decisions(db_conn, all_records)
                    if applied_count and hasattr(self.engine, "log"):
                        self.engine.log(f"Applied {applied_count} remembered outlier review field change(s).")
                session_progress.emit(4)
                start_index = 0
                if self.append:
                    connection = getattr(db_conn, "conn", None)
                    if connection is not None:
                        row = connection.execute(
                            "SELECT COALESCE(MAX(row_id), -1) FROM staging_records WHERE session_id = ?",
                            (self.engine.session_id,),
                        ).fetchone()
                        start_index = int(row[0] if row is not None else -1) + 1
                    else:
                        start_index = max(
                            (int(getattr(rec, "staging_row_id", -1) or -1) for rec in self.current_records),
                            default=-1,
                        ) + 1
                if db_native_scan:
                    scan_item_batches = (
                        db_conn.iter_classified_append_items(self.engine.session_id, scan_ids)
                        if self.append
                        else db_conn.iter_classified_scan_session_items(self.engine.session_id)
                    )
                    scan_rows = iter_scan_item_staging_rows(
                        scan_item_batches,
                        start_index=start_index,
                    )
                    inserted = db_conn.add_staging_records_iter(self.engine.session_id, scan_rows)
                    if all_records:
                        db_conn.add_staging_records_iter(
                            self.engine.session_id,
                            iter_staging_rows(all_records, start_index=start_index + inserted),
                        )
                    if hasattr(db_conn, "apply_target_review_decisions_to_staging"):
                        applied_count = db_conn.apply_target_review_decisions_to_staging(self.engine.session_id)
                        if applied_count and hasattr(self.engine, "log"):
                            self.engine.log(f"Applied {applied_count} remembered outlier review field change(s).")
                elif hasattr(db_conn, "add_staging_records_iter"):
                    rows = iter_staging_rows(all_records, start_index=start_index)
                    db_conn.add_staging_records_iter(self.engine.session_id, rows)
                else:
                    rows = iter_staging_rows(all_records, start_index=start_index)
                    db_conn.add_staging_records_bulk(self.engine.session_id, list(rows))
                session_progress.emit(5)
                try:
                    if hasattr(db_conn, "prune_ephemeral_state"):
                        db_conn.prune_ephemeral_state({self.engine.session_id}, target_root=self.engine.target_dir)
                except Exception:
                    logging.debug("Post-scan database maintenance skipped.", exc_info=True)
                session_progress.emit(6, force=True)
                if hasattr(db_conn, "update_session_scan_runs"):
                    db_conn.update_session_scan_runs(
                        self.engine.session_id,
                        state="staged",
                        phase="readiness",
                    )
            finally:
                if owns_db_conn:
                    db_conn.close()
            
            # Staging is authoritative from this point. Avoid retaining and
            # queueing the complete DTO population into the GUI thread.
            new_records.clear()
            plan.clear()
            self.finished.emit({"records": [], "append": self.append, "stats": stats})
        except Exception as e:
            logging.exception("ScanWorker encountered an error")
            engine_db = getattr(self.engine, "db", None)
            if engine_db is not None and hasattr(engine_db, "update_session_scan_runs"):
                try:
                    engine_db.update_session_scan_runs(self.engine.session_id, state="failed")
                except Exception:
                    logging.debug("Could not persist failed scan state.", exc_info=True)
            self.error.emit(str(e))
        finally:
            if getattr(self.engine, "progress_callback", None):
                self.engine.progress_callback = None
            timing_summary = monitor.stop()
            _report_scan_timing(self.engine, timing_summary)

class CommitWorker(QThread):
    """Executes the planned file operations (move/copy)."""
    progress = Signal(dict)
    finished = Signal(dict)
    error = Signal(str)

    def __init__(
        self,
        engine,
        plan,
        move,
        dry_run,
        flat,
        no_px,
        skip_confirmed_duplicates=True,
    ):
        super().__init__()
        self.engine = engine
        self.plan = plan
        self.move = move
        self.dry_run = dry_run
        self.flat = flat
        self.no_px = no_px
        self.skip_confirmed_duplicates = bool(skip_confirmed_duplicates)

    @safe_gc_run
    def run(self):
        try:
            self.engine.progress_callback = lambda d: self.progress.emit(d)
            res = self.engine.execute_plan(
                self.plan,
                self.move,
                self.dry_run,
                self.flat,
                self.no_px,
                skip_confirmed_duplicates=self.skip_confirmed_duplicates,
            )
            if isinstance(res, dict) and not res.get("error"):
                self.progress.emit({
                    "phase": "Finalizing Build",
                    "message": "Cleaning up temporary scan data...",
                    "cancellable": False,
                })
                workflow_build_completion.prune_successful_build_state(self.engine)
                if self.move:
                    self.progress.emit({
                        "phase": "Finalizing Build",
                        "message": "Checking remaining source files...",
                        "cancellable": False,
                    })
                    remaining_count, remaining_bytes = remaining_source_footprint(
                        getattr(self.engine, "session_source_roots", []) or []
                    )
                    res["remaining_source_file_count"] = remaining_count
                    res["remaining_source_bytes"] = remaining_bytes
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


class CustomTreeProjectionWorker(QThread):
    """Builds a custom-tree projection without blocking the GUI thread."""

    finished = Signal(dict)
    error = Signal(str)
    progress = Signal(int, int)
    canceled = Signal()

    def __init__(
        self,
        request_id,
        db,
        session_id,
        profile,
        levels,
        confidence_floor,
        confidence_filter_enabled,
    ):
        super().__init__()
        self.request_id = int(request_id)
        self.db = db
        self.session_id = str(session_id)
        self.profile = profile
        self.levels = list(levels)
        self.confidence_floor = float(confidence_floor)
        self.confidence_filter_enabled = bool(confidence_filter_enabled)

    @safe_gc_run
    def run(self):
        try:
            from .staging_session_store import StagingRowsUpdateCanceled, StagingSessionStore

            signature = StagingSessionStore(self.db, self.session_id).ensure_custom_tree_projection(
                self.profile,
                self.levels,
                confidence_floor=self.confidence_floor,
                confidence_filter_enabled=self.confidence_filter_enabled,
                progress_callback=lambda current, total: self.progress.emit(current, total),
                interrupted_check=self.isInterruptionRequested,
            )
            self.finished.emit(
                {
                    "request_id": self.request_id,
                    "profile_id": str(self.profile.id),
                    "signature": signature,
                }
            )
        except StagingRowsUpdateCanceled:
            self.canceled.emit()
        except Exception as exc:
            logging.exception("CustomTreeProjectionWorker encountered an error")
            self.error.emit(str(exc))


class StagingRowsUpdateWorker(QThread):
    """Writes prepared staging-row updates without blocking the GUI thread."""

    progress = Signal(int, int)
    error = Signal(str)
    canceled = Signal()

    def __init__(self, db, session_id, db_updates):
        super().__init__()
        self.db = db
        self.session_id = str(session_id)
        self.db_updates = list(db_updates or [])

    @safe_gc_run
    def run(self):
        try:
            from .staging_session_store import StagingRowsUpdateCanceled, StagingSessionStore

            StagingSessionStore(self.db, self.session_id).update_rows(
                self.db_updates,
                progress_callback=lambda current, total: self.progress.emit(current, total),
                interrupted_check=self.isInterruptionRequested,
            )
        except StagingRowsUpdateCanceled:
            self.canceled.emit()
        except Exception as exc:
            logging.exception("StagingRowsUpdateWorker encountered an error")
            self.error.emit(str(exc))


class SearchWorker(QThread):
    """Executes staging DB searches off the UI thread."""
    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, request_id, bridge=None, query_text="", *, store=None, taxonomy_context=None):
        super().__init__()
        self.request_id = request_id
        self.bridge = bridge
        self.query_text = str(query_text or "")
        self.store = store
        self.taxonomy_context = taxonomy_context

    def _run_effective_taxonomy_query(self):
        from gui.core.tree_filter_options import effective_taxonomy_query_groups

        union_ids: set[int] = set()
        ranked: list[int] = []
        for db_query, predicates in effective_taxonomy_query_groups(self.query_text):
            if db_query:
                base = SearchEngine.run_query(self.bridge, db_query)
                if base is None:
                    base_ids = set(self.store.row_ids())
                    base_ranked = None
                elif isinstance(base, list):
                    base_ids = set(base)
                    base_ranked = [int(row_id) for row_id in base]
                else:
                    base_ids = {int(row_id) for row_id in base}
                    base_ranked = None
            else:
                base_ids = set(self.store.row_ids())
                base_ranked = None
            for placement, value in predicates:
                base_ids.intersection_update(
                    self.store.effective_taxonomy_match_ids(
                        placement,
                        value,
                        self.taxonomy_context,
                    )
                )
            if base_ranked is not None:
                ranked.extend(row_id for row_id in base_ranked if row_id in base_ids and row_id not in ranked)
            union_ids.update(base_ids)
        if ranked:
            return [*ranked, *(row_id for row_id in union_ids if row_id not in set(ranked))]
        return union_ids

    @safe_gc_run
    def run(self):
        try:
            if self.store is not None and self.taxonomy_context is not None:
                matched_ids = self._run_effective_taxonomy_query()
            else:
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
    progress = Signal(dict)
    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, request_id, records=None, *, db=None, session_id: str = ""):
        super().__init__()
        self.request_id = int(request_id)
        self.records = list(records or ())
        self.db = db
        self.session_id = str(session_id or "")

    def run(self):
        try:
            from unshuffle.logic.tagging import compute_db_duplicate_tags, compute_tagging_pass

            if self.db is not None and self.session_id:
                duplicate_count = compute_db_duplicate_tags(
                    self.db,
                    self.session_id,
                    progress_callback=lambda payload: self.progress.emit(payload),
                )
                self.finished.emit({
                    "request_id": self.request_id,
                    "tags_by_path": {},
                    "duplicate_matches": [],
                    "duplicate_file_count": duplicate_count,
                    "db_applied": True,
                })
                return

            result = compute_tagging_pass(
                self.records,
                include_genres=False,
                progress_callback=lambda payload: self.progress.emit(payload),
            )
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
    progress = Signal(dict)
    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, request_id, db, session_id, force=False):
        super().__init__()
        self.request_id = int(request_id)
        self.db = db
        self.session_id = str(session_id or "")
        self.force = bool(force)

    def run(self):
        try:
            from unshuffle.logic.coherence import run_coherence_audit

            summary = run_coherence_audit(
                self.db,
                self.session_id,
                force=self.force,
                progress_callback=lambda payload: self.progress.emit(payload),
            )
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

    def __init__(self, target, session_id, *, local_db=None, global_db=None):
        super().__init__()
        self.target = str(target or "")
        self.session_id = str(session_id or "")
        self.local_db = local_db
        self.global_db = global_db

    @safe_gc_run
    def run(self):
        try:
            from ..utils.history import invalidate_history_cache, load_session_sources

            session_id = self.session_id
            db_scope = "global"
            if self.target:
                db_conn, session_id, db_scope, owned_db = _open_restore_db(
                    Path(self.target),
                    session_id,
                    local_db=self.local_db,
                    global_db=self.global_db,
                )
                try:
                    try:
                        if hasattr(db_conn, "prune_ephemeral_state"):
                            db_conn.prune_ephemeral_state({session_id} if session_id else set(), target_root=Path(self.target))
                            invalidate_history_cache(self.target)
                    except Exception:
                        logging.debug("Session-load database maintenance skipped.", exc_info=True)
                    record_count = _staging_session_row_count(db_conn, session_id)
                    if record_count < 0:
                        from ..utils.history import load_staging_records

                        record_count = len(load_staging_records(self.target, session_id))
                    sources = (
                        db_conn.get_session_sources(session_id)
                        if session_id and hasattr(db_conn, "get_session_sources")
                        else load_session_sources(self.target, session_id) if session_id else []
                    )
                finally:
                    if owned_db:
                        db_conn.close()
            else:
                record_count = 0
                sources = []

            self.finished.emit(
                {
                    "session_id": session_id,
                    "sources": sources,
                    "record_count": record_count,
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
            from ..utils.history import invalidate_history_cache, load_session_sources

            session_id = self.session_id
            db_scope = "global"
            if self.target:
                db_conn, session_id, db_scope, owned_db = _open_restore_db(Path(self.target), session_id)
                try:
                    try:
                        if hasattr(db_conn, "prune_ephemeral_state"):
                            db_conn.prune_ephemeral_state({session_id} if session_id else set(), target_root=Path(self.target))
                            invalidate_history_cache(self.target)
                    except Exception:
                        logging.debug("Startup database maintenance skipped.", exc_info=True)
                    record_count = _staging_session_row_count(db_conn, session_id)
                    if record_count < 0:
                        from ..utils.history import load_staging_records

                        record_count = len(load_staging_records(self.target, session_id))
                    sources = (
                        db_conn.get_session_sources(session_id)
                        if session_id and hasattr(db_conn, "get_session_sources")
                        else load_session_sources(self.target, session_id) if session_id else []
                    )
                finally:
                    if owned_db:
                        db_conn.close()
            else:
                record_count = 0
                sources = []

            self.finished.emit(
                {
                    "session_id": session_id,
                    "target": self.target,
                    "sources": sources,
                    "record_count": record_count,
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
