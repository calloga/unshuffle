import importlib
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from ..audio.metadata import get_audio_duration
from ..core.constants import BPM_REGEX_PATTERN, KEY_REGEX_PATTERN
from ..core.logging import logger
from ..logic.execution import ExecutionMixin
from ..persistence import DIRECTORY_DUMP_FILE, UnshuffleDB, cleanup_session_meta, get_db, get_local_db
from ..runtime.cache import CacheMixin
from ..runtime.execution_progress import batch_log_message, low_battery_emergency, progress_payload
from ..runtime.execution_records import (
    ExecutionResultCounts,
    empty_execution_result,
    execution_error_summary,
    execution_result_payload,
    failed_record_payload,
    invalid_tree_profile_result,
    session_record_payload,
)
from ..runtime.execution_reports import execution_report_filename, open_execution_report
from ..runtime.execution_sessions import execution_session_sources, register_execution_session
from ..runtime.execution_validation import tree_profile_error
from ..runtime.undo_cleanup import cleanup_empty_target_folders, remove_prefix_legend
from ..runtime.undo_actions import undo_record_action as apply_undo_record_action
from ..runtime.undo_results import undo_failure_result, undo_success_result
from ..runtime.undo_sessions import (
    delete_local_undo_session,
    finalize_global_undo_session,
    mirror_undo_session_to_global,
    undo_result_sources,
)
from ..runtime.undo_sidecar import remove_disposable_target_sidecar, target_local_db_is_disposable
from ..runtime.undo_support import (
    effective_undo_target_root,
    preserved_undo_confirmation,
    undo_duplicate_trash_path,
    undo_expected_hash,
    undo_record_action,
    undo_source_roots,
    undoable_records,
    validate_undo_records,
)


class RuntimeUnshuffler(CacheMixin, ExecutionMixin):
    """
    Runtime engine implementation with patch-sensitive dependency hooks for tests.
    """
    lock_path: Optional[Path] = None

    def __init__(
        self,
        target_dir: Path,
        progress_callback=None,
        logger_instance=None,
        session_id: Optional[str] = None,
        bootstrapper=None,
    ):
        self.target_dir = target_dir.resolve()
        self.logger = logger_instance or logger
        self.bootstrapper = bootstrapper or getattr(self, "bootstrapper", None)
        self.seen_hashes = {}
        self.prefix_map = {}
        self.moved_preserved_roots = set()
        self.progress_callback = progress_callback
        self.interrupted = False
        import uuid

        self.session_id = session_id or str(uuid.uuid4())
        self.session_source_root = None
        self.session_source_roots = []

        self._init_db_and_hashes()
        self._acquire_lock()

    def _setup_logging(self, is_dry_run: bool) -> None:
        if self.bootstrapper:
            self.bootstrapper.setup_logging_fn(self.target_dir, is_dry_run, self.session_id)
            return
        raise NotImplementedError

    def _get_local_db(self):
        if self.bootstrapper:
            return self.bootstrapper.get_local_db_fn(self.target_dir)
        raise NotImplementedError

    def _run_plan(
        self,
        source_dir: Path,
        token_adjustments,
        acoustic_index: bool,
        skip_expensive_hashes,
        min_confidence,
    ):
        if self.bootstrapper:
            return self.bootstrapper.run_plan_fn(
                source_dir,
                self.target_dir,
                session_id=self.session_id,
                progress_callback=self.progress_callback,
                token_adjustments=token_adjustments,
                db=self.db,
                acoustic_index=acoustic_index,
                is_interrupted=lambda: self.interrupted,
                skip_expensive_hashes=skip_expensive_hashes,
                min_confidence=min_confidence,
            )
        raise NotImplementedError

    def _acquire_lock(self):
        from ..runtime.locking import acquire_lock
        import json

        try:
            self.lock_path = acquire_lock(self.target_dir, self.session_id, self.log)
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            raise RuntimeError(
                "Library lock exists but lock metadata is invalid. "
                "Refusing automatic takeover in safety-first mode. "
                "Close other instances or set UNSHUFFLE_FORCE_LOCK_TAKEOVER=1."
            ) from exc

    def _release_lock(self, *, log_release: bool = True):
        from ..runtime.locking import release_lock

        release_lock(getattr(self, "lock_path", None), self.log if log_release else None)
        self.lock_path = None

    def _init_db_and_hashes(self):
        self.load_cache()
        self.local_db = self._get_local_db()

    def _active_databases(self, *, reopen: bool = True):
        databases = []
        seen = set()
        for database in (getattr(self, "db", None), getattr(self, "local_db", None)):
            if database is None:
                continue
            key = id(database)
            if key in seen:
                continue
            seen.add(key)
            databases.append(database)
        if not databases and reopen:
            self._init_db_and_hashes()
            return self._active_databases(reopen=False)
        return databases

    def _primary_database(self):
        databases = self._active_databases()
        return databases[0] if databases else None

    def log(self, message: str, level=logging.INFO):
        self.logger.log(level, message)
        if self.progress_callback:
            self.progress_callback({"message": message, "level": level})

    def _get_psutil(self):
        try:
            return importlib.import_module("psutil")
        except ImportError:
            return None

    def prepare_plan(
        self,
        sources: List[Path],
        pack_name_override: Optional[str] = None,
        acoustic_index: bool = False,
        skip_expensive_hashes: Optional[set[str]] = None,
        min_confidence: Optional[float] = None,
    ):
        self.interrupted = False
        valid_sources = []
        for source in sources:
            source_path = Path(source).resolve()
            if source_path.exists() and source_path.is_dir():
                valid_sources.append(source_path)

        if not valid_sources:
            return []

        all_sources = list(self.session_source_roots) + valid_sources
        unique_sources = []
        for source in sorted(all_sources, key=lambda path: len(str(path))):
            if not any(str(source).startswith(str(other) + os.sep) or source == other for other in unique_sources):
                unique_sources.append(source)

        scan_sources = valid_sources

        if not self.session_source_root and unique_sources:
            self.session_source_root = unique_sources[0]

        self.session_source_roots = unique_sources

        self.log(f"Session ID: {self.session_id}")

        self.db.register_session(
            session_id=self.session_id,
            source=valid_sources[0],
            target=self.target_dir,
            mode="pending",
        )
        self.db.set_session_sources(self.session_id, unique_sources)

        self.log("Phase 0: Analyzing library structure...")
        token_adjustments = self.db.get_token_adjustments()

        plan = []
        for source_dir in scan_sources:
            plan.extend(
                self._run_plan(
                    source_dir,
                    token_adjustments=token_adjustments,
                    acoustic_index=acoustic_index,
                    skip_expensive_hashes=skip_expensive_hashes,
                    min_confidence=min_confidence,
                )
            )

        exclusions = [Path(path).resolve() for path in self.db.get_exclusions()]
        if exclusions:
            before_count = len(plan)
            exclusion_roots = tuple(exclusions)
            exclusion_prefixes = tuple(f"{str(root).lower()}{os.sep}" for root in exclusion_roots)
            exclusion_exact = {str(root).lower() for root in exclusion_roots}

            def is_excluded(record):
                try:
                    record_path = record.source_path.resolve()
                    return any(record_path == root or root in record_path.parents for root in exclusion_roots)
                except OSError:
                    record_path_str = str(record.source_path).lower()
                    return record_path_str in exclusion_exact or any(
                        record_path_str.startswith(prefix) for prefix in exclusion_prefixes
                    )

            plan = [record for record in plan if not is_excluded(record)]
            skipped_count = before_count - len(plan)
            if skipped_count:
                self.log(f"Skipped {skipped_count} files from excluded folders.")

        self.log(f"Scan complete. {len(plan)} files identified.")
        cleanup_session_meta(self.target_dir, self.session_id, self.db)
        return plan

    def execute_plan(self, plan: List, move: bool = False, dry_run: bool = False, flat: bool = False, no_prefix: bool = False):
        self.interrupted = False
        self.moved_preserved_roots = set()
        self._execution_records = list(plan)
        tree_error = tree_profile_error(getattr(self, "active_tree_profile", None), self._execution_records)
        if tree_error is not None:
            self.log(f"Custom tree organization is invalid. Build blocked.\n{tree_error}", level=logging.ERROR)
            return invalid_tree_profile_result(len(plan), dry_run, tree_error)
        if not dry_run:
            self.seen_hashes = {}
            self.refresh_seen_hashes()

        self._setup_logging(dry_run)

        total_files = len(plan)
        if total_files == 0:
            return empty_execution_result(dry_run=dry_run)

        self.log(f"Phase 2: Executing {total_files} operations...")

        effective_primary_source, effective_sources = execution_session_sources(
            plan,
            self.session_source_root,
            self.session_source_roots,
            self.target_dir,
        )

        active_databases = self._active_databases()

        if not dry_run:
            register_execution_session(
                active_databases,
                session_id=self.session_id,
                source=effective_primary_source,
                sources=effective_sources,
                target=self.target_dir,
                move=move,
                flat=flat,
            )

        counts = ExecutionResultCounts()
        failed_records = []
        csv_writer = None
        dry_run_file = None
        csv_path = None
        report_filename = execution_report_filename(self.session_id, dry_run)

        session_records = []

        execution_error = None

        try:
            csv_path, report_filename, dry_run_file, csv_writer = open_execution_report(
                self.target_dir,
                self.session_id,
                dry_run,
            )

            for index, record in enumerate(plan, start=1):
                if self.interrupted:
                    counts.interrupted += total_files - index + 1
                    break

                try:
                    psutil_module = self._get_psutil()
                    if low_battery_emergency(psutil_module):
                        self.log("CRITICAL: Battery below 5%. Suspending session to prevent data loss.", level=logging.ERROR)
                        execution_error = "Low Battery Emergency"
                        break
                except Exception:
                    pass

                batch_message = batch_log_message(index, total_files, record.source_path.name)
                if batch_message is not None:
                    self.log(batch_message)

                payload = progress_payload(index, total_files)
                if self.progress_callback and payload is not None:
                    self.progress_callback(payload)

                self._last_record_hash = ""
                self._last_effective_action = "move" if move else "copy"
                self._last_duplicate_trash_path = None
                result, actual_dest = self._process_single_record(record, move, dry_run, flat, no_prefix, csv_writer)
                duplicate_trash_path = getattr(self, "_last_duplicate_trash_path", None)
                record_hash = getattr(self, "_last_record_hash", None) or record.hash
                record_action = getattr(self, "_last_effective_action", None) or ("move" if move else "copy")

                if counts.record(result, move=move, record_action=record_action):
                    failed_records.append(failed_record_payload(record, actual_dest))

                if not dry_run:
                    session_records.append(
                        session_record_payload(
                            record,
                            actual_dest,
                            result,
                            record_hash,
                            record_action,
                            duplicate_trash_path,
                        )
                    )

        except Exception as exc:
            execution_error = str(exc)
            self.log(f"Execution error: {exc}", level=logging.ERROR)
        finally:
            if session_records:
                for database in active_databases:
                    database.add_records_bulk(self.session_id, session_records)
            if dry_run_file:
                dry_run_file.close()
            if not dry_run:
                self.save_cache()
                self.save_legend()
            else:
                for handler in self.logger.handlers[:]:
                    if isinstance(handler, logging.FileHandler):
                        handler.close()
                        self.logger.removeHandler(handler)

            primary_database = self._primary_database()
            if primary_database is not None:
                cleanup_session_meta(self.target_dir, self.session_id, primary_database, report_filename)

        if execution_error is None:
            execution_error = execution_error_summary(counts)

        return execution_result_payload(
            total_files=total_files,
            counts=counts,
            failed_records=failed_records,
            dry_run=dry_run,
            move=move,
            session_id=self.session_id,
            report_path=csv_path,
            error=execution_error,
        )

    def close(self, *, log_release: bool = True):
        self.progress_callback = None
        self._release_lock(log_release=log_release)
        if hasattr(self, "db") and self.db:
            self.db.close()
            self.db = None
        if hasattr(self, "local_db") and self.local_db:
            self.local_db.close()
            self.local_db = None

    def __del__(self):
        try:
            self.close(log_release=False)
        except Exception:
            pass

    def _target_local_db_is_disposable(self) -> bool:
        database = getattr(self, "local_db", None) or getattr(self, "db", None)
        return target_local_db_is_disposable(database)

    def _remove_disposable_target_sidecar(self) -> bool:
        return remove_disposable_target_sidecar(self.target_dir)

    def undo_session(self, session_id: str, confirm_preserved: bool = False):
        if not hasattr(self, "db") or not self.db:
            self.db = get_db(self.target_dir)

        session_db, records = self._undo_session_record_source(session_id)
        if not records:
            return {"session_id": session_id, "error": "No records"}

        session = session_db.get_session(session_id)
        mode = session["mode"] if session else "copy"
        undo_records = self._undoable_records(records)
        skipped_records = len(records) - len(undo_records)
        if not undo_records:
            return {"session_id": session_id, "error": "No committed records to undo"}
        self._reanchor_undo_target(session, undo_records)

        preserved_confirmation = self._preserved_undo_confirmation(session_id, undo_records, mode)
        if preserved_confirmation and not confirm_preserved:
            return preserved_confirmation

        validation_error = self._validate_undo_records(session_db, session_id, session, undo_records, mode, confirm_preserved)
        if validation_error:
            return {"session_id": session_id, "error": validation_error}

        undone = 0
        already_undone = 0
        completed_records = 0
        failed = False
        interrupted = False
        failed_deletes = []
        cleanup_failures = []
        undone_relative_paths = []
        target_folders = set()

        for index, record in enumerate(undo_records, start=1):
            tgt = Path(record["target_path"])
            if self.interrupted:
                interrupted = True
                break
            if self.progress_callback:
                self.progress_callback({"current": index, "total": len(undo_records), "message": f"Undoing: {tgt.name}"})

            try:
                outcome = apply_undo_record_action(
                    record=record,
                    mode=mode,
                    session_id=session_id,
                    target_dir=self.target_dir,
                    record_action_fn=self._undo_record_action,
                    duplicate_trash_path_fn=self._undo_duplicate_trash_path,
                    log=self.log,
                )
                undone += outcome.undone
                already_undone += outcome.already_undone
                completed_records += outcome.completed
                failed = failed or outcome.failed
                undone_relative_paths.extend(outcome.relative_paths)
                target_folders.update(outcome.target_folders)
            except Exception as exc:
                failed = True
                if record.get("status") != "duplicate" and self._undo_record_action(record, mode) == "copy":
                    failed_deletes.append(str(tgt))
                self.log(f"  ! Undo Error for {tgt.name}: {exc}", level=logging.ERROR)

        for database in [self.db, self.local_db]:
            database.remove_from_cache_by_paths(undone_relative_paths)

        remove_prefix_legend(self.target_dir, self.log)
        cleanup_failures.extend(cleanup_empty_target_folders(self.target_dir, target_folders, self.log))

        self.seen_hashes = self.db.get_all_hashes()

        if failed or interrupted or cleanup_failures or completed_records != len(undo_records):
            return undo_failure_result(
                session_id=session_id,
                undone=undone,
                already_undone=already_undone,
                failed_deletes=failed_deletes,
                cleanup_failures=cleanup_failures,
                interrupted=interrupted,
            )

        sources = undo_result_sources(session_db, session_id, session)
        mirror_undo_session_to_global(self.db, session_db, session_id, session, records)
        finalize_global_undo_session(self.db, session_id)
        if getattr(self, "local_db", None) is not None and self.local_db is not self.db:
            delete_local_undo_session(self.local_db, session_id)

        sidecar_removed = False
        if self._target_local_db_is_disposable():
            self._release_lock()
            if hasattr(self, "db") and self.db:
                self.db.close()
                self.db = None
            if hasattr(self, "local_db") and self.local_db:
                self.local_db.close()
                self.local_db = None
            sidecar_removed = self._remove_disposable_target_sidecar()

        return undo_success_result(
            session_id=session_id,
            target_dir=self.target_dir,
            undone=undone,
            sources=sources,
            skipped_records=skipped_records,
            already_undone=already_undone,
            sidecar_removed=sidecar_removed,
            sidecar_cleanup_pending=self.db is None and self.local_db is None,
        )

    def _undo_session_record_source(self, session_id: str):
        local_db = getattr(self, "local_db", None)
        global_db = getattr(self, "db", None)

        if local_db is not None:
            try:
                local_records = local_db.get_session_records(session_id)
            except Exception:
                logging.debug("Could not load local undo records for %s.", session_id, exc_info=True)
            else:
                if isinstance(local_records, list) and local_records:
                    return local_db, local_records

        if global_db is not None:
            try:
                global_records = global_db.get_session_records(session_id)
            except Exception:
                logging.debug("Could not load global undo records for %s.", session_id, exc_info=True)
            else:
                if isinstance(global_records, list) and global_records:
                    return global_db, global_records

        return global_db or local_db, []

    def _reanchor_undo_target(self, session, records: list | None = None) -> None:
        if not session:
            return
        raw_target = session.get("target_root") or session.get("target_path")
        if not raw_target:
            return
        try:
            session_target = Path(raw_target).resolve()
        except OSError:
            session_target = Path(raw_target)
        try:
            current_target = self.target_dir.resolve()
        except OSError:
            current_target = self.target_dir
        session_target = self._effective_undo_target_root(session_target, records or [])
        if session_target == current_target:
            return

        self.log(
            f"Undo target restored from session history: {session_target}",
            level=logging.INFO,
        )
        self.target_dir = session_target

        current_local_db = getattr(self, "local_db", None)
        current_db = getattr(self, "db", None)
        if current_local_db is current_db:
            return
        if current_local_db is not None and current_local_db is not current_db:
            try:
                current_local_db.close()
            except Exception:
                pass
        try:
            if getattr(self, "bootstrapper", None):
                self.local_db = self._get_local_db()
            else:
                self.local_db = get_local_db(self.target_dir)
        except Exception:
            logging.debug("Could not re-open local undo database for %s", self.target_dir, exc_info=True)

    def _effective_undo_target_root(self, session_target: Path, records: list) -> Path:
        return effective_undo_target_root(session_target, records)

    def _undoable_records(self, records: list) -> list:
        return undoable_records(records)

    def _validate_undo_records(
        self,
        session_db,
        session_id: str,
        session,
        records: list,
        mode: str,
        allow_preserved: bool = False,
    ) -> str | None:
        return validate_undo_records(
            session_db=session_db,
            session_id=session_id,
            session=session,
            records=records,
            mode=mode,
            target_dir=self.target_dir,
            allow_preserved=allow_preserved,
        )

    def _preserved_undo_confirmation(self, session_id: str, records: list, mode: str) -> dict | None:
        return preserved_undo_confirmation(session_id, records, mode, self.target_dir)

    def _undo_record_action(self, record: dict, session_mode: str) -> str:
        return undo_record_action(record, session_mode)

    def _undo_expected_hash(self, record: dict) -> str | None:
        return undo_expected_hash(record)

    def _undo_duplicate_trash_path(self, record: dict, trash_dir: Path) -> Path:
        return undo_duplicate_trash_path(record, trash_dir)

    def _undo_source_roots(self, session_db, session_id: str, session) -> list[Path]:
        return undo_source_roots(session_db, session_id, session)
