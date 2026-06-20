import logging
from pathlib import Path
from collections.abc import Iterable
from PySide6.QtCore import QObject, Signal, QTimer
from PySide6.QtWidgets import QWidget
import shiboken6
from unshuffle.bridge.workflow_bridge import WorkflowBridge, create_workflow_bridge
from . import workflow_handover
from . import workflow_build_errors
from . import workflow_build_completion
from . import workflow_destructive_actions
from . import workflow_model_cleanup
from . import workflow_preservation
from . import workflow_scan_cancellation
from . import workflow_scan_finalization
from . import workflow_scan_start
from . import workflow_session_persistence
from . import workflow_undo_completion
from .workflow_records import build_result_compact_lines, build_result_lines, build_result_summary, dedupe_plan_records, record_dedupe_key, scan_duplicate_stats, undo_result_summary
from .workflow_restore import restore_previous_session
from .workflow_summary import (
    chart_segments as _chart_segments,
    scan_category_counts,
    scan_summary_chart_pixmap,
    scan_summary_text,
    show_scan_summary_dialog,
)


def _qt_object_alive(obj) -> bool:
    if obj is None:
        return False
    try:
        return shiboken6.isValid(obj)
    except RuntimeError:
        return False
    except TypeError:
        return True


class WorkflowController(QObject):
    """
    Orchestrates complex business workflows (Scan -> Build -> Reorg).
    """
    scanStarted = Signal(list, bool)
    scanDataReady = Signal(list, bool, dict)
    scanFinished = Signal(dict)
    engineChanged = Signal(object)
    exclusionAdded = Signal(str, int)
    progressUpdated = Signal(int, str)
    restoreFinished = Signal(bool)
    
    def __init__(self, engine, worker_manager, undo_stack, parent=None):
        super().__init__(parent)
        self._engine = engine
        self.worker_manager = worker_manager
        self.undo_stack = undo_stack
        self.app = parent
        self._pending_finalize_options = {}

    def _emit_restore_finished(self, restored: bool) -> None:
        if not _qt_object_alive(self):
            return
        try:
            self.restoreFinished.emit(restored)
        except RuntimeError:
            logging.warning("Restore finished signal skipped because the workflow controller was deleted.")

    def _surface_workflow_error(self, message: str) -> None:
        app = self.app
        if _qt_object_alive(app):
            try:
                if hasattr(app, "set_search_status"):
                    app.set_search_status(message)
                else:
                    footer = getattr(app, "footer", None)
                    if _qt_object_alive(footer):
                        footer.set_status(message)
            except RuntimeError:
                logging.warning("Workflow error status skipped because the target widget was deleted.")
        error_signal = getattr(self.worker_manager, "error", None)
        emit = getattr(error_signal, "emit", None)
        if callable(emit):
            try:
                emit(message)
            except RuntimeError:
                logging.warning("Workflow error signal skipped because the worker manager was deleted.")

    def restore_session(self, *, frontload: bool = False):
        restore_previous_session(self, frontload=frontload, bridge_factory=create_workflow_bridge)


    def handle_tree_exclude(self, items):
        """Adds source paths to suppression list."""
        if items is None:
            return
        if isinstance(items, (str, Path)) or hasattr(items, "source_path"):
            items = [items]
        elif not isinstance(items, Iterable):
            items = [items]

        paths = []
        for item in items:
            if hasattr(item, "source_path"):
                paths.append(item.source_path)
            elif isinstance(item, Path):
                paths.append(item)
            elif isinstance(item, str):
                paths.append(Path(item))

        for path in paths:
            self.exclude_path(path, model=self.app.model)

    def on_exclusion_added(self, path, count):
        name = Path(path).name or path
        self.app.footer.log(f"<b>Suppressed:</b> {name} ({count} staged files removed).")
        self.app.search_controller.execute_search()

    @property
    def engine(self):
        return self._engine

    def set_engine(self, engine):
        if self._engine is engine:
            return
        self._engine = engine
        if not _qt_object_alive(self):
            return
        try:
            self.engineChanged.emit(engine)
        except RuntimeError:
            logging.warning("Engine change signal skipped because the workflow controller was deleted.")

    def _parent_widget(self) -> QWidget | None:
        return self.app if isinstance(self.app, QWidget) else None

    def start_scan(
        self,
        sources: list[str] | list[Path],
        append: bool = False,
        last_target: str | None = None,
        *,
        require_clear_draft: bool = True,
        finalize_options: dict | None = None,
    ):
        self.app._scan_finalizing = True
        self._pending_finalize_options = dict(finalize_options or {})
        if require_clear_draft:
            drafting = getattr(self.app, "drafting_controller", None)
            if drafting is not None and not drafting.confirm_clear_pending_draft(
                "expand this session" if append else "start a new session"
            ):
                self._pending_finalize_options = {}
                return False
        target = last_target or (sources[0] if sources else None)
        if not target:
            self._pending_finalize_options = {}
            return False
        self.clear_build_handover_state()
        if getattr(self.app, "tagging_controller", None):
            self.app.tagging_controller.clear_state()
        if getattr(self.app, "coherence_controller", None):
            self.app.coherence_controller.clear_state()

        if not append or not self._engine:
            if not append and getattr(self.app, "tree_organization_controller", None):
                self.app.tree_organization_controller.disable_profile(refresh=False)
            if self._engine:
                try:
                    self._engine.close()
                except Exception:
                    logging.exception("Failed to close previous engine.")
            
            try:
                self.set_engine(create_workflow_bridge(Path(target)))
            except Exception as e:
                logging.error(f"Failed to initialize engine: {e}")
                self._surface_workflow_error(f"Failed to initialize engine: {e}")
                self._pending_finalize_options = {}
                return False
            
            self.undo_stack.clear()

        sources_paths = [Path(s) for s in sources]
        self.scanStarted.emit([str(s) for s in sources_paths], append)
        current_records = self.app.model.records if append and self.app.model else None
        skip_expensive_hashes = self._known_duplicate_hashes_for_scan(current_records, append=append)

        lib_hashes = set()
        existing_hashes = workflow_scan_start.existing_dedupe_keys(current_records, append=append)
        started = self.worker_manager.start_scan(
            self._engine, 
            sources_paths, 
            acoustic_index=True, 
            append=append,
            skip_expensive_hashes=skip_expensive_hashes,
            min_confidence=0.0,
            existing_hashes=existing_hashes,
            lib_hashes=lib_hashes,
            current_records=current_records,
        )
        if not started:
            self._pending_finalize_options = {}
        return bool(started)

    def start_refresh(self, roots: list[Path]):
        drafting = getattr(self.app, "drafting_controller", None)
        if drafting is not None and not drafting.confirm_clear_pending_draft("rescan the library"):
            return
        if not self._engine:
            return
        self.clear_build_handover_state()
        
        if not roots and hasattr(self._engine, "session_source_roots"):
            roots = self._engine.session_source_roots
            
        if not roots:
            return
            
        self.start_scan(
            [str(root) for root in roots],
            append=False,
            require_clear_draft=False,
            finalize_options={"restore_previous_session_on_cancel": True},
        )

    def detach_source_root(self, root: Path) -> list[Path]:
        if not self._engine or not getattr(self._engine, "db", None):
            return []
        self.clear_build_handover_state()
        return workflow_scan_start.detach_source_root(self._engine, root)

    def start_undo(self, session_id: str, confirm_preserved: bool = False):
        self.clear_build_handover_state()
        self.worker_manager.start_undo(session_id, confirm_preserved=confirm_preserved)

    def _record_dedupe_key(self, rec):
        return record_dedupe_key(rec)

    def _dedupe_plan_records(self, plan, existing_hashes, lib_hashes):
        return dedupe_plan_records(plan, existing_hashes, lib_hashes)

    def _known_duplicate_hashes_for_scan(self, current_records=None, append=False):
        return workflow_scan_start.known_duplicate_hashes_for_scan(current_records, append=append)

    def handle_scan_finished(self, new_records: list, is_append: bool, stats: dict):
        stats = dict(stats or {})
        if stats.get("cancelled"):
            from ..utils.ui_helpers import set_ui_busy

            options = dict(self._pending_finalize_options or {})
            self._pending_finalize_options = {}
            if options.get("restore_previous_session_on_cancel"):
                if getattr(self.app, "footer", None) is not None:
                    self.app.footer.set_status("Refresh canceled. Restoring previous session...")
                self.app._scan_finalizing = False
                self.scanFinished.emit(stats)
                self.restore_session()
                return
            if not is_append:
                self._clear_workbench_for_cancelled_scan()
            if getattr(self.app, "footer", None) is not None:
                self.app.footer.set_status("Scan canceled.")
            self.app._scan_finalizing = False
            set_ui_busy(self.app, False)
            self.scanFinished.emit(stats)
            return
        self.scanDataReady.emit(new_records, is_append, stats)

    def _clear_workbench_for_cancelled_scan(self) -> None:
        workflow_scan_cancellation.clear_workbench_for_cancelled_scan(getattr(self, "app", None))

    def finalize_scan_data_from_signal(self, new_records, is_append, stats):
        options = self._pending_finalize_options or {}
        self._pending_finalize_options = {}
        options.pop("restore_previous_session_on_cancel", None)
        options.setdefault("persist_staging", False)
        self.finalize_scan_data(new_records, is_append, stats, **options)

    def show_active_scan_summary(self) -> None:
        stats = getattr(self, "_last_scan_stats", None)
        if stats:
            show_scan_summary_dialog(self._parent_widget(), stats)

    def start_commit(self, records, target_dir, move=True, flat=False, no_px=False, display_context: dict | None = None):
        if not self._engine:
            return

        resolved_target = self._confirm_category_target_root(Path(target_dir).resolve())
        try:
            from ..widgets.build_page import target_source_overlap_message

            source_roots = [Path(root) for root in getattr(self._engine, "session_source_roots", []) or []]
            overlap_message = target_source_overlap_message(resolved_target, source_roots)
        except Exception:
            logging.exception("Failed to validate build target/source overlap.")
            overlap_message = ""
        if overlap_message:
            logging.warning("Build blocked: %s Target=%s", overlap_message, resolved_target)
            app = self.app
            if _qt_object_alive(app):
                try:
                    if hasattr(app, "set_search_status"):
                        app.set_search_status(overlap_message)
                    else:
                        footer = getattr(app, "footer", None)
                        if _qt_object_alive(footer):
                            footer.set_status(overlap_message)
                except RuntimeError:
                    logging.warning("Build target validation status skipped because the target widget was deleted.")
            return
        self.clear_build_handover_state()

        if str(self._engine.target_dir) != str(resolved_target):
            self._engine.target_dir = resolved_target
            self._engine._init_db_and_hashes()

        self._last_build_options = {
            "target": str(resolved_target),
            "move": bool(move),
            "flat": bool(flat),
            "no_px": bool(no_px),
        }
        if display_context:
            self._last_build_options.update(display_context)
        stats = getattr(self, "_last_scan_stats", {}) or {}
        self._pending_build_skipped_duplicates = {
            "skipped_duplicates": int(stats.get("total_dupe_count") or 0),
            "skipped_session_duplicates": int(stats.get("session_dupe_count") or 0),
            "skipped_library_duplicates": int(stats.get("lib_dupe_count") or 0),
        }
        audio_controller = getattr(self.app, "audio_controller", None)
        preview_player = getattr(audio_controller, "player", None)
        if preview_player is not None and hasattr(preview_player, "release"):
            preview_player.release()
        self.worker_manager.start_commit(records, move, False, flat, no_px)

    def clear_build_handover_state(self) -> None:
        workflow_handover.clear_build_handover_state(self)

    def restore_build_handover_state(self) -> bool:
        return workflow_handover.restore_build_handover_state(self)

    def open_build_handover_target(self) -> None:
        self._open_build_handover_path("target_path")

    def open_build_handover_source(self) -> None:
        workflow_handover.open_build_handover_source(self)

    def undo_build_handover(self) -> None:
        workflow_handover.undo_build_handover(self)

    def _open_build_handover_path(self, state_key: str | None = None, *, path: str | None = None) -> None:
        workflow_handover.open_build_handover_path(self, state_key, path=path)

    def _enter_build_handover_state(self, res: dict, summary: str) -> None:
        workflow_handover.enter_build_handover_state(self, res, summary)

    def _clear_workbench_after_move_handover(self) -> None:
        workflow_handover.clear_workbench_after_move_handover(self)

    def _confirm_category_target_root(self, target: Path) -> Path:
        return workflow_handover.confirm_category_target_root(self, target)

    def finalize_scan_data(
        self,
        new_records,
        is_append,
        stats,
        show_summary=True,
        persist_staging=True,
        defer_background_work=False,
        schedule_background_work=True,
        on_ready=None,
        on_background_work_start=None,
        status_callback=None,
        summary_callback=None,
    ):
        from ..models import StagingTableModel
        from ..utils.state import finalize_model_mutation
        from ..utils import ui_helpers
        from PySide6.QtCore import QTimer

        if not new_records and is_append:
             self.app.footer.log("<b>Notice:</b> No new files found to add.")
             return

        self.app._scan_finalizing = True
        self.app.footer.set_busy_state(True)
        self.app.footer.progress_bar.setRange(0, 0)
        if is_append and self.app.model:
            self.app.model.beginResetModel()
            self.app.model.records.extend(new_records)
            if hasattr(self.app.model, "_rebuild_row_and_color_caches"):
                self.app.model._rebuild_row_and_color_caches()
            else:
                self.app.model._precalculate_colors()
            self.app.model.endResetModel()
        else:
            model = StagingTableModel(new_records, self.app.undo_stack, sync_callback=self.app.data_manager.sync_record_to_db)
            self.app.set_runtime_context(model=model)
            self.app.proxy_model.setSourceModel(self.app.model)

            workflow_scan_finalization.reset_library_search(
                self.app,
                defer_background_work=defer_background_work,
            )

        workflow_scan_finalization.refresh_library_sources_and_suggestions(self.app)
            
        stats = workflow_scan_finalization.normalized_scan_stats(stats, new_records, scan_category_counts)
        self._last_scan_stats = stats
        self.app.footer.set_count(f"{len(self.app.model.records)} files ready")

        workflow_scan_finalization.update_corrupt_filter_state(self.app)

        self.app.footer.set_status("Finalizing scan...")
        if callable(summary_callback):
            summary_callback(stats)
        self.app.library_tab._refresh_search_button_state()

        def _ready() -> None:
            self.app._scan_finalizing = False
            from ..utils.ui_helpers import set_ui_busy
            set_ui_busy(self.app, False)
            self.app.footer.set_status("Ready")
            if show_summary:
                if hasattr(self.app.footer, "show_scan_summary_button"):
                    self.app.footer.show_scan_summary_button()
            if callable(on_ready):
                on_ready()

        def _finish_scan_ui():
            try:
                if not defer_background_work or not getattr(self.app, "_view_headers_initialized", False):
                    ui_helpers.setup_view_headers(self.app)
                if persist_staging:
                    finalize_model_mutation(self.app, resort=True, refresh_search=False, tree_delay_ms=60)
                else:
                    self.app.view_controller.apply_current_sort_state()
                    self.app.footer.set_count(f"{len(self.app.model.records)} files ready")
                    if self.app.engine and hasattr(self.app.library_tab, "set_sources"):
                        self.app.library_tab.set_sources(self.app.engine.session_source_roots)
                    self.app.view_controller.update_library_views(tree_delay_ms=60)
                polish_delay_ms = 120 if defer_background_work else 0
                QTimer.singleShot(polish_delay_ms, self.app.library_tab._capture_column_width_ratios)
                QTimer.singleShot(polish_delay_ms, self.app.library_tab._apply_proportional_column_widths)
                QTimer.singleShot(polish_delay_ms + 80, self.app.library_tab._apply_proportional_column_widths)

                if self.app.engine:
                    try:
                        workflow_session_persistence.persist_scan_session(self.app.settings, self.app.engine)
                    except Exception:
                        logging.exception("Failed to persist launcher last choice after scan.")

                if schedule_background_work:
                    if callable(on_background_work_start):
                        on_background_work_start()
                    self._prepare_scan_metadata_and_views(
                        defer_background_work=defer_background_work,
                        on_ready=_ready,
                        status_callback=status_callback,
                    )
                else:
                    self.app._scan_finalizing = False
                    _ready()
            except Exception:
                self.app._scan_finalizing = False
                raise

        QTimer.singleShot(0, _finish_scan_ui)

    def _prepare_scan_metadata_and_views(
        self,
        *,
        defer_background_work: bool = False,
        on_ready=None,
        status_callback=None,
    ) -> None:
        from PySide6.QtCore import QTimer

        tagging = getattr(self.app, "tagging_controller", None)
        coherence = getattr(self.app, "coherence_controller", None)
        view_controller = getattr(self.app, "view_controller", None)
        should_auto_check = getattr(self.app, "_should_auto_check_coherence_on_start", lambda: False)

        def _status(text: str) -> None:
            self.app.footer.set_status(text)
            if callable(status_callback):
                status_callback(text)

        def _prewarm_views() -> None:
            _status("Preparing enabled views...")
            if view_controller is not None:
                if getattr(self.app, "_is_library_map_enabled", lambda: True)():
                    view_controller.prewarm_library_map(delay_ms=500)
                view_controller.prewarm_library_tree(delay_ms=700 if defer_background_work else 350)
            self.app._scan_finalizing = False
            if callable(on_ready):
                on_ready()

        def _run_coherence() -> None:
            if coherence is None or not should_auto_check():
                _prewarm_views()
                return
            _status("Checking library coherence...")

            def _after_coherence() -> None:
                try:
                    coherence.coherenceFinished.disconnect(_after_coherence)
                except (RuntimeError, TypeError):
                    pass
                _prewarm_views()

            coherence.coherenceFinished.connect(_after_coherence)
            if getattr(coherence, "_running_workers", None):
                return
            coherence.start_coherence_audit(mode="background")

        def _run_tagging() -> None:
            if tagging is None:
                _run_coherence()
                return
            _status("Checking possible duplicates...")

            def _after_tagging() -> None:
                try:
                    tagging.taggingFinished.disconnect(_after_tagging)
                except (RuntimeError, TypeError):
                    pass
                _run_coherence()

            tagging.taggingFinished.connect(_after_tagging)
            tagging.start_tagging_pass(schedule_coherence=False)

        delay_ms = 500 if defer_background_work else 0
        QTimer.singleShot(delay_ms, _run_tagging)

    def handle_commit_finished(self, res):
        from PySide6.QtWidgets import QMessageBox
        if isinstance(res, dict):
            skipped = getattr(self, "_pending_build_skipped_duplicates", None) or {}
            workflow_build_completion.merge_pending_skipped_duplicates(res, skipped)
            self._pending_build_skipped_duplicates = {}
            opts = getattr(self, "_last_build_options", {}) or {}
            workflow_build_completion.apply_default_move_flag(res, opts)
            workflow_build_completion.apply_retry_display_counts(res, opts)
        error = res.get("error") if isinstance(res, dict) else None
        summary = build_result_summary(res) if isinstance(res, dict) else ""
        summary_lines = build_result_compact_lines(res) if isinstance(res, dict) and error else build_result_lines(res) if isinstance(res, dict) else [summary]
        committed_count = 0
        if isinstance(res, dict):
            committed_count = workflow_build_completion.committed_record_count(res)

        if self._engine and getattr(self._engine, "db", None):
            if not error:
                workflow_build_completion.prune_successful_build_state(self._engine)

            session_id = workflow_build_completion.build_session_id(res, self._engine)
            workflow_build_completion.persist_build_session(self.app.settings, self._engine, session_id)

            workflow_build_completion.invalidate_build_history_cache(self._engine, committed_count)

        if hasattr(self.app, "history_page") and self._engine:
            self.app.history_page.refresh_from_target(str(self._engine.target_dir))

        if isinstance(res, dict) and res.get("cancelled"):
            self._handle_cancelled_commit(res, summary_lines)
            return res

        if error:
            message = self._build_error_message(res, summary_lines, str(error)) if isinstance(res, dict) else str(error)
            retry_records = self._retryable_failed_records(res) if isinstance(res, dict) else []
            if retry_records:
                dialog = QMessageBox(self._parent_widget())
                dialog.setIcon(QMessageBox.Warning)
                dialog.setWindowTitle("Build Needs Attention")
                dialog.setText(message)
                retry_button = dialog.addButton(
                    f"Retry {len(retry_records)} Failed",
                    QMessageBox.ActionRole,
                )
                dialog.addButton(QMessageBox.Close)
                dialog.exec()
                if dialog.clickedButton() is retry_button:
                    opts = getattr(self, "_last_build_options", {}) or {}
                    retry_context = workflow_build_completion.retry_display_context(res, opts)
                    self.start_commit(
                        retry_records,
                        opts.get("target") or str(getattr(self._engine, "target_dir", "")),
                        move=bool(opts.get("move", True)),
                        flat=bool(opts.get("flat", False)),
                        no_px=bool(opts.get("no_px", False)),
                        display_context=retry_context,
                    )
            else:
                QMessageBox.warning(self._parent_widget(), "Build Needs Attention", message)
            return res
        
        self._enter_build_handover_state(res, "\n".join(summary_lines))
        QMessageBox.information(self._parent_widget(), "Build Complete", "Build complete.")
        return res

    def _handle_cancelled_commit(self, res: dict, summary_lines: list[str]) -> None:
        from PySide6.QtWidgets import QMessageBox

        session_id = str(res.get("session_id") or getattr(self._engine, "session_id", "") or "")
        committed_count = int(res.get("copied", 0) or 0) + int(res.get("duplicates", 0) or 0)
        if committed_count > 0 and session_id:
            self._cancelled_build_rollback = {
                "session_id": session_id,
                "summary": "\n".join(summary_lines),
                "committed_count": committed_count,
            }
            if getattr(self.app, "footer", None) is not None:
                self.app.footer.set_status("Undoing canceled build...")
            if self._engine is not None:
                self._engine.interrupted = False
            self.start_undo(session_id)
            return

        message = "Build canceled.\n\nNo files were changed."
        if getattr(self.app, "footer", None) is not None:
            self.app.footer.set_status("Build canceled.")
        QMessageBox.information(self._parent_widget(), "Build Canceled", message)

    def _prompt_open_library_session(
        self,
        *,
        title: str,
        summary: str,
        prompt: str,
        path: str,
        action_label: str,
    ) -> bool:
        from PySide6.QtWidgets import QMessageBox

        path = str(path or "").strip()
        if not path:
            QMessageBox.information(self._parent_widget(), title, summary)
            return False

        dialog = QMessageBox(self._parent_widget())
        dialog.setIcon(QMessageBox.Information)
        dialog.setWindowTitle(title)
        dialog.setText(summary + "\n\n" + prompt + "\n" + path)
        open_button = dialog.addButton(action_label, QMessageBox.ActionRole)
        dialog.addButton("Stay Here", QMessageBox.RejectRole)
        dialog.exec()
        return dialog.clickedButton() is open_button

    def _build_error_message(self, res: dict, summary_lines: list[str], error: str) -> str:
        retry_records = self._retryable_failed_records(res)
        return workflow_build_errors.build_error_message(res, summary_lines, error, retry_records)

    def _retryable_failed_records(self, res: dict) -> list:
        model = getattr(self.app, "model", None)
        records = list(getattr(model, "records", []) or [])
        return workflow_build_errors.retryable_failed_records(res, records)

    def handle_undo_finished(self, res):
        from PySide6.QtWidgets import QMessageBox
        if isinstance(res, dict) and res.get("requires_preserved_confirmation"):
            items = res.get("items") or []
            details = []
            for item in items[:5]:
                action = item.get("action")
                source = item.get("source_path", "")
                target = item.get("target_path", "")
                if action == "restore_to_source":
                    details.append(f"Put back in source:\n{target}\n-> {source}")
                else:
                    details.append(f"Delete copied folder from target:\n{target}")
            if len(items) > 5:
                details.append(f"...and {len(items) - 5} more preserved folder(s).")
            message = (
                "This undo includes preserved folder-level actions.\n\n"
                + "\n\n".join(details)
                + "\n\nContinue?"
            )
            if QMessageBox.warning(
                self._parent_widget(),
                "Confirm Preserved Folder Undo",
                message,
                QMessageBox.Yes | QMessageBox.No,
            ) == QMessageBox.Yes:
                self.start_undo(res.get("session_id", ""), confirm_preserved=True)
            return res

        error = res.get("error") if isinstance(res, dict) else None
        rollback = getattr(self, "_cancelled_build_rollback", None)
        rollback_matches = workflow_undo_completion.rollback_matches_result(rollback, res)
        if error:
            history_page = getattr(self.app, "history_page", None)
            workflow_undo_completion.mark_undo_retryable(history_page, str(res.get("session_id") or ""))
            message = workflow_undo_completion.undo_error_message(res, error)
            retry_session_id = str(res.get("session_id") or "") if isinstance(res, dict) else ""
            if retry_session_id:
                dialog = QMessageBox(self._parent_widget())
                dialog.setIcon(QMessageBox.Warning)
                dialog.setWindowTitle("Build Cancel Needs Attention" if rollback_matches else "Undo Needs Attention")
                dialog.setText(message)
                retry_button = dialog.addButton("Retry Undo", QMessageBox.ActionRole)
                dialog.addButton(QMessageBox.Close)
                dialog.exec()
                if dialog.clickedButton() is retry_button:
                    self.start_undo(retry_session_id)
            else:
                QMessageBox.warning(
                    self._parent_widget(),
                    "Build Cancel Needs Attention" if rollback_matches else "Undo Needs Attention",
                    message,
                )
            return res

        summary = undo_result_summary(res)
        if isinstance(res, dict):
            session_id = str(res.get("session_id") or "")
            target_root = str(res.get("target_root") or self.app.settings.value("last_target", "") or "")
            history_page = getattr(self.app, "history_page", None)
            workflow_undo_completion.refresh_undo_history(history_page, target_root, session_id)

            if rollback_matches:
                self._cancelled_build_rollback = None
                message = workflow_undo_completion.cancelled_build_rollback_message(rollback)
                if getattr(self.app, "footer", None) is not None:
                    self.app.footer.set_status("Build canceled. Changes were undone.")
                QMessageBox.information(self._parent_widget(), "Build Canceled", message)
                return res

            sources = [str(source).strip() for source in (res.get("sources") or []) if str(source).strip()]
            if sources:
                self._persist_restored_sources_after_undo(sources, session_id=session_id)
            if sources and self._prompt_scan_restored_sources(summary, sources):
                return res

        QMessageBox.information(self._parent_widget(), "Undo Complete", summary)
        return res

    def _persist_restored_sources_after_undo(self, sources: list[str], *, session_id: str = "") -> None:
        sources = [str(source).strip() for source in (sources or []) if str(source).strip()]
        if not sources:
            return

        try:
            workflow_session_persistence.persist_restored_sources(self.app.settings, sources, session_id=session_id)
        except Exception:
            logging.debug("Undo restored-source startup choice persistence skipped.", exc_info=True)

    def _persist_restored_source_after_undo(self, source: str, *, session_id: str = "") -> None:
        self._persist_restored_sources_after_undo([source], session_id=session_id)

    def _prompt_scan_restored_sources(self, summary: str, sources: list[str]) -> bool:
        sources = [str(source).strip() for source in (sources or []) if str(source).strip()]
        if not sources:
            return False

        display_path = "\n".join(sources)
        action_label = "Scan Sources" if len(sources) > 1 else "Scan Source"
        prompt = "Scan the restored source libraries now?" if len(sources) > 1 else "Scan the restored source library now?"

        if not self._prompt_open_library_session(
            title="Undo Complete",
            summary=summary,
            prompt=prompt,
            path=display_path,
            action_label=action_label,
        ):
            return False

        workflow_session_persistence.persist_restored_sources_scan_target(self.app.settings, sources)
        self.start_scan(sources, append=False, last_target=sources[0], require_clear_draft=False)
        return True

    def _prompt_scan_restored_source(self, summary: str, source: str) -> bool:
        return self._prompt_scan_restored_sources(summary, [source])

    def handle_preserve_request(self, path: Path):
        from PySide6.QtWidgets import QMessageBox
        from ..dialogs.preserved import PreservedDialog
        
        dialog = PreservedDialog(path, parent=self._parent_widget(), source_roots=self._active_source_roots())
        if dialog.exec() == PreservedDialog.Accepted:
            target_path = Path(dialog.get_path())
            try:
                workflow_preservation.create_preserved_marker(target_path)
                self.start_refresh([])
            except Exception as e:
                QMessageBox.warning(self._parent_widget(), "Preserve Failed", f"Could not create marker file: {e}")

    def handle_unpreserve_request(self, path: Path):
        from PySide6.QtWidgets import QMessageBox
        
        try:
            if workflow_preservation.remove_preserved_marker(Path(path)):
                self.start_refresh([])
        except Exception as e:
            QMessageBox.warning(self._parent_widget(), "Un-preserve Failed", f"Could not remove marker file: {e}")

    def exclude_path(self, path: str, model=None):
        if not self._engine or not self._engine.db:
            return
            
        import os
        exclude_path = Path(path).resolve()
        self._engine.db.add_exclusion(str(exclude_path))
        
        if model:
            removed_count = workflow_model_cleanup.remove_excluded_prefix(model, exclude_path)
        else:
            removed_count = 0
            
        self.exclusionAdded.emit(str(exclude_path), removed_count)

    def delete_records_physically(self, records):
        if not records or not self._engine:
            return
            
        from PySide6.QtWidgets import QMessageBox
        count = len(records)
        reply = QMessageBox.question(
            self._parent_widget(),
            "Delete from Disk",
            f"Permanently delete {count} selected file{'s' if count != 1 else ''} from your hard drive?\n\nThis will physically delete the files from your computer. This action cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
            
        delete_result = workflow_destructive_actions.physically_delete_records(
            records,
            self._active_source_roots(),
        )
        deleted_paths = delete_result.deleted_paths
        failed_paths = delete_result.failed_paths

        workflow_destructive_actions.remove_deleted_staging_rows(
            self._engine.db,
            self._engine.session_id,
            deleted_paths,
        )
                
        model = self.app.model
        if model and hasattr(model, "records"):
            workflow_model_cleanup.remove_deleted_paths(model, deleted_paths)
      
        self.app.search_controller.execute_search()
        
        self.app.footer.log(workflow_destructive_actions.physical_delete_status_message(delete_result))
        
        if failed_paths:
            from PySide6.QtWidgets import QMessageBox
            details = workflow_destructive_actions.physical_delete_error_details(failed_paths)
            QMessageBox.warning(
                self._parent_widget(),
                "Deletion Errors",
                f"Some files could not be deleted from disk (they may be in use or write-protected):\n\n{details}"
            )

    def _active_source_roots(self) -> list[Path]:
        return workflow_destructive_actions.active_source_roots(self._engine)
