from __future__ import annotations

import logging

from PySide6.QtCore import QObject, QTimer, Qt, Signal
from pathlib import Path

from PySide6.QtWidgets import QDialog, QMessageBox

from gui.core.filter_query import source_filter_query
from gui.core import coherence_anchor_promotions, coherence_navigation, coherence_refinement_updates
from gui.core.coherence_presenters import (
    cached_review_state_message,
    coherence_complete_text,
    ignored_strong_outlier_ids,
    initial_refinement_action,
    nearest_adjacency_from_summary,
    neighbor_summary,
    outlier_candidate_id,
    real_refinement_candidate_ids,
    remaining_review_message,
    summary_float,
)
from gui.core import coherence_review_rows
from gui.core.coherence_review_session import (
    json_safe_review_row,
    load_manual_review_session,
    review_session_settings_key,
    review_session_state_key,
    store_manual_review_session,
)
from gui.core.coherence_review_decisions import (
    ignored_outlier_ids,
    persisted_review_decisions,
    remember_ignored_outliers,
    review_decisions_for_records,
)


class CoherenceController(QObject):
    """Coordinates background coherence audits and staged refinement review."""

    coherenceFinished = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.app = parent
        self._request_id = 0
        self._active_worker = None
        self._running_workers = set()
        self._auto_applied_candidate_ids: set[str] = set()
        self._check_modes: dict[int, str] = {}
        self._manual_review_session_rows: list[dict] = []

    def clear_state(self) -> None:
        self._request_id += 1
        self._auto_applied_candidate_ids.clear()
        self._clear_manual_review_session()
        if getattr(self.app, "footer", None):
            self.app.footer.set_coherence_state("", False)

    def schedule_after_render(self, *, mode: str = "background") -> None:
        if not getattr(self.app, "engine", None) or not getattr(self.app.engine, "db", None):
            self.coherenceFinished.emit()
            return
        if not getattr(self.app, "model", None):
            self.coherenceFinished.emit()
            return
        QTimer.singleShot(250, lambda: self.start_coherence_audit(mode=mode))

    def start_coherence_audit(self, *, force: bool = False, mode: str = "background") -> None:
        engine = getattr(self.app, "engine", None)
        if not engine or not getattr(engine, "db", None) or not getattr(engine, "session_id", None):
            self.coherenceFinished.emit()
            return
        if self._running_workers:
            if getattr(self.app, "footer", None):
                self.app.footer.set_status("Coherence check is already running.")
            self.coherenceFinished.emit()
            return
        if not force and self._use_cached_coherence_state(engine):
            return
        self._clear_manual_review_session()
        self._request_id += 1
        request_id = self._request_id
        self._check_modes[request_id] = mode
        self.app.footer.set_coherence_state("Checking library coherence...", True, can_review=False)

        from .workers import CoherenceWorker

        worker = CoherenceWorker(request_id, engine.db, engine.session_id, force=force)
        self._active_worker = worker
        self._running_workers.add(worker)

        def _finished(payload: dict) -> None:
            self._running_workers.discard(worker)
            if payload.get("request_id") != self._request_id:
                self.coherenceFinished.emit()
                return
            self.apply_coherence_result(payload, mode=self._check_modes.pop(request_id, mode))

        def _error(message: str) -> None:
            self._running_workers.discard(worker)
            if request_id != self._request_id:
                self.coherenceFinished.emit()
                return
            self._check_modes.pop(request_id, None)
            self.app.footer.set_coherence_state("", False)
            self.app.footer.set_status(f"Coherence audit failed: {message}")
            self.coherenceFinished.emit()

        worker.finished.connect(_finished)
        worker.finished.connect(worker.deleteLater)
        worker.error.connect(_error)
        worker.error.connect(worker.deleteLater)
        worker.start()

    def start_continuous_refinement(self) -> None:
        if self._running_workers:
            if getattr(self.app, "footer", None):
                self.app.footer.set_status("Coherence check is already running.")
            return
        self.start_coherence_audit(force=True, mode="continuous")

    def apply_coherence_result(self, payload: dict, *, mode: str = "background") -> None:
        if not payload.get("ran"):
            self.app.footer.set_coherence_state(str(payload.get("reason") or ""), False)
            self.coherenceFinished.emit()
            return
        auto_count = int(payload.get("auto_staged_candidate_count") or 0)
        anchor_count = int(payload.get("anchor_candidate_count") or 0)
        if getattr(self.app, "system_controller", None):
            self.app.system_controller.refresh_anchor_candidates()
        if auto_count:
            self.apply_auto_staged_refinements()
        review_rows = self._review_rows()
        review_count = len(review_rows)
        if review_count:
            self._set_manual_review_session(review_rows)
            self.app.footer.set_coherence_state(
                f"{review_count} acoustic outlier{'s' if review_count != 1 else ''} to review.",
                True,
                can_review=True,
            )
            if mode == "continuous":
                QTimer.singleShot(0, lambda: self.review_refinements(continuous=True))
        else:
            self._handle_coherence_ready(mode, fallback_text=coherence_complete_text(auto_count, anchor_count))
        self._refresh_analyzer_page()
        if getattr(self.app, "view_controller", None):
            self.app.view_controller.prewarm_library_map(delay_ms=250)
        self.coherenceFinished.emit()

    def review_refinements(self, *, continuous: bool = False) -> None:
        engine = getattr(self.app, "engine", None)
        if not engine or not getattr(engine, "db", None):
            return
        rows = self._review_session_rows()
        if not rows:
            if continuous:
                self._handle_coherence_ready("continuous")
            else:
                self._verify_coherence_before_ready()
            return
        from gui.widgets.refinement_popup import RefinementReviewDialog

        dialog = RefinementReviewDialog(rows, self.app)
        dialog.audioPreviewRequested.connect(self.preview_audio_path)
        if dialog.exec() != QDialog.Accepted:
            return
        accepted = dialog.accepted_candidate_ids()
        ignored = dialog.ignored_candidate_ids()
        anchor_record_ids = dialog.anchor_confirmed_record_ids()
        promoted_anchor_count, promoted_anchor_record_ids = self._promote_matching_anchors_for_records(anchor_record_ids)
 
        ignored_outlier_ids = ignored_strong_outlier_ids(rows, set(ignored), promoted_anchor_record_ids)
        if ignored_outlier_ids:
            self._remember_ignored_outliers(ignored_outlier_ids)
        applied = 0
        accepted_refinement_rows = []
        if accepted:
            accepted_refinement_rows = dialog.accepted_refinement_rows()
            applied = self.apply_refinements(accepted_refinement_rows, notify=not continuous)
            accepted_real_ids = real_refinement_candidate_ids(accepted)
            if accepted_real_ids:
                try:
                    engine.db.set_refinement_candidate_state(engine.session_id, accepted_real_ids, "accepted")
                except Exception:
                    logging.exception("Failed to mark accepted refinement candidates.")
                    if getattr(self.app, "footer", None):
                        self.app.footer.set_status("Warning: could not save accepted refinement state.")
        if ignored:
            ignored_real_ids = real_refinement_candidate_ids(ignored)
            if ignored_real_ids:
                try:
                    engine.db.set_refinement_candidate_state(engine.session_id, ignored_real_ids, "ignored")
                except Exception:
                    logging.exception("Failed to mark ignored refinement candidates.")
                    if getattr(self.app, "footer", None):
                        self.app.footer.set_status("Warning: could not save ignored refinement state.")
        remembered_decision_count = self._remember_review_decisions(rows, accepted_refinement_rows, ignored_outlier_ids)
        self._remove_processed_review_rows(
            accepted_candidate_ids=accepted,
            ignored_candidate_ids=ignored,
            promoted_record_ids=promoted_anchor_record_ids,
        )
       
        if not continuous:
            remaining_rows = self._manual_review_session_rows
            remaining_count = len(remaining_rows)
            if remaining_count:
                self.app.footer.set_coherence_state(
                    remaining_review_message(remaining_count, promoted_anchor_count),
                    True,
                    can_review=True,
                )
            else:
                self._clear_manual_review_session()
                self.app.footer.set_coherence_state("", False)
                self._verify_coherence_before_ready()
        if continuous:
            if applied or promoted_anchor_count:
                self._clear_manual_review_session()
                self.start_coherence_audit(force=True, mode="continuous")
            else:
                self._handle_coherence_ready("continuous")

    def _review_session_rows(self) -> list[dict]:
        if not self._manual_review_session_rows:
            self._set_manual_review_session(self._review_rows())
        return list(self._manual_review_session_rows)

    def _set_manual_review_session(self, rows: list[dict]) -> None:
        self._manual_review_session_rows = [dict(row) for row in rows]
        self._store_manual_review_session()

    def _clear_manual_review_session(self) -> None:
        self._manual_review_session_rows.clear()
        self._store_manual_review_session()

    def _remove_processed_review_rows(
        self,
        *,
        accepted_candidate_ids: list[str],
        ignored_candidate_ids: list[str],
        promoted_record_ids: set[str],
    ) -> None:
        processed_candidate_ids = {item for item in accepted_candidate_ids + ignored_candidate_ids if item}
        processed_record_ids = {item for item in promoted_record_ids if item}
        if not processed_candidate_ids and not processed_record_ids:
            return
        self._manual_review_session_rows = [
            row
            for row in self._manual_review_session_rows
            if str(row.get("candidate_id") or "") not in processed_candidate_ids
            and str(row.get("record_id") or "") not in processed_record_ids
        ]
        self._store_manual_review_session()

    def _review_session_settings_key(self) -> str:
        return review_session_settings_key(self.app)

    def _review_session_state_key(self) -> str:
        return review_session_state_key(self.app)

    def _store_manual_review_session(self) -> None:
        store_manual_review_session(self.app, self._manual_review_session_rows)

    def _load_manual_review_session(self) -> list[dict]:
        return load_manual_review_session(self.app)

    @staticmethod
    def _json_safe_review_row(row: dict) -> dict:
        return json_safe_review_row(row)


    def _verify_coherence_before_ready(self) -> None:
        if getattr(self.app, "footer", None):
            self.app.footer.set_coherence_state("Rechecking library coherence before build...", True, can_review=False)
        self.start_coherence_audit(force=True, mode="manual")



    def apply_refinements(self, rows: list[dict], *, notify: bool = True) -> int:
        updates = self._refinement_updates(rows)
        if not updates:
            return 0
        model = getattr(self.app, "model", None)
        if model is None:
            return 0
        if not hasattr(model, "_apply_bulk_values"):
            return 0
        model._apply_bulk_values(updates)
        if hasattr(model, "_sync_bulk_updates"):
            model._sync_bulk_updates(updates)
        if getattr(self.app, "view_controller", None):
            if getattr(getattr(self.app, "search_controller", None), "current_query", ""):
                self.app.search_controller.execute_search()
            else:
                self.app.view_controller.update_library_views(tree_delay_ms=0)
        if notify:
            QMessageBox.information(self.app, "Coherence Refinements", f"Applied {len(updates)} suggested refinement(s).")
        return len(updates)

    def _learn_from_direct_refinement_updates(self, updates: list[tuple]) -> int:
        return 0

    def apply_auto_staged_refinements(self) -> int:
        engine = getattr(self.app, "engine", None)
        if not engine or not getattr(engine, "db", None):
            return 0
        rows = engine.db.list_refinement_candidates(engine.session_id, state="auto_staged")
        rows = [row for row in rows if str(row.get("candidate_id") or "") not in self._auto_applied_candidate_ids]
        if not rows:
            return 0
        updates = self._refinement_updates(self._enrich_refinement_rows(rows))
        if not updates:
            return 0
        model = getattr(self.app, "model", None)
        if model is None:
            return 0
        if hasattr(model, "_apply_bulk_values"):
            model._apply_bulk_values(updates)
        else:
            return 0
        if hasattr(model, "_sync_bulk_updates"):
            model._sync_bulk_updates(updates)
            
       
        candidate_ids = [str(row.get("candidate_id") or "") for row in rows]
        if candidate_ids:
            try:
                engine.db.set_refinement_candidate_state(engine.session_id, candidate_ids, "accepted")
            except Exception:
                logging.exception("Failed to auto-accept refinements in database.")

        if getattr(self.app, "view_controller", None):
            self.app.view_controller.update_library_views(tree_delay_ms=0)
        self._auto_applied_candidate_ids.update(str(row.get("candidate_id") or "") for row in rows)
        if getattr(self.app, "footer", None):
            self.app.footer.log(f"<b>Coherence:</b> auto-staged {len(updates)} refinement(s).")
        return len(updates)

    def _refinement_updates(self, rows: list[dict]) -> list[tuple]:
        model = getattr(self.app, "model", None)
        return coherence_refinement_updates.refinement_updates(model, rows)

    def _enrich_refinement_rows(self, rows: list[dict]) -> list[dict]:
        return coherence_review_rows.enrichment_rows(self, rows)

    def _review_rows(self) -> list[dict]:
        return coherence_review_rows.review_rows(self)

    @staticmethod
    def _review_row_sort_key(row: dict) -> tuple:
        return coherence_review_rows.review_row_sort_key(row)

    @staticmethod
    def _initial_refinement_action(row: dict) -> str:
        return initial_refinement_action(row)

    def _mark_refinement_anchor_prompt_eligibility(self, rows: list[dict]) -> None:
        coherence_review_rows.mark_refinement_anchor_prompt_eligibility(self, rows)

    def _derive_strong_outlier_rows(self, active_refinement_record_ids: set[str]) -> list[dict]:
        return coherence_review_rows.derive_strong_outlier_rows(self, active_refinement_record_ids)

    @staticmethod
    def _neighbor_summary(result: dict) -> dict:
        return neighbor_summary(result)

    @staticmethod
    def _nearest_adjacency_from_summary(summary: dict) -> str:
        return nearest_adjacency_from_summary(summary)

    @staticmethod
    def _summary_float(summary: dict, key: str) -> float:
        return summary_float(summary, key)

    @staticmethod
    def _outlier_candidate_id(record_id: str, audio_type: str, category: str, subcategory: str) -> str:
        return outlier_candidate_id(record_id, audio_type, category, subcategory)

    def _ignored_outlier_ids(self) -> set[str]:
        return ignored_outlier_ids(self.app)

    def _remember_ignored_outliers(self, outlier_ids: list[str]) -> None:
        remember_ignored_outliers(self.app, outlier_ids)

    def _remember_review_decisions(
        self,
        rows: list[dict],
        accepted_refinement_rows: list[dict],
        ignored_outlier_ids: list[str],
    ) -> int:
        engine = getattr(self.app, "engine", None)
        db = getattr(engine, "db", None)
        session_id = str(getattr(engine, "session_id", "") or "")
        if not db or not session_id or not hasattr(db, "upsert_coherence_review_decisions"):
            return 0
        decisions = persisted_review_decisions(rows, accepted_refinement_rows, ignored_outlier_ids)
        if not decisions:
            return 0
        db.upsert_coherence_review_decisions(session_id, decisions)
        return len(decisions)

    def _promote_matching_anchors_for_records(self, record_ids: list[str]) -> tuple[int, set[str]]:
        engine = getattr(self.app, "engine", None)
        model = getattr(self.app, "model", None)
        if not record_ids or not engine or not getattr(engine, "db", None) or model is None:
            return 0, set()
        if not hasattr(engine.db, "list_coherence_results") or not hasattr(engine.db, "list_anchor_candidates"):
            return 0, set()
        coherence_results = engine.db.list_coherence_results(engine.session_id)
        anchor_rows = engine.db.list_anchor_candidates(engine.session_id, state="candidate")
        promote_ids, promoted_record_ids = coherence_anchor_promotions.matching_anchor_promotions(
            record_ids,
            model.records,
            coherence_results,
            anchor_rows,
        )
        if not promote_ids:
            return 0, set()
        engine.db.set_anchor_candidate_state(engine.session_id, promote_ids, "verified")
        if getattr(self.app, "system_controller", None):
            self.app.system_controller.refresh_anchor_candidates()
        return len(promote_ids), promoted_record_ids

    def _handle_coherence_ready(self, mode: str, *, fallback_text: str = "Coherence audit complete.") -> None:
        if mode in {"manual", "continuous"}:
            reply = QMessageBox.question(
                self.app,
                "Library Ready",
                "Library looks stable. Build organized library?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply == QMessageBox.Yes and hasattr(self.app, "open_build_workspace"):
                self.app.open_build_workspace()
            if getattr(self.app, "footer", None):
                self.app.footer.set_coherence_state("", False)
            return
        if getattr(self.app, "footer", None):
            self.app.footer.set_coherence_state(
                "Coherence looks stable. Library is ready to build.",
                True,
                can_review=False,
                can_build=True,
            )

    def _refresh_analyzer_page(self) -> None:
        library_page = getattr(getattr(self.app, "library_tab", None), "coherence_map", None)
        library_tab = getattr(self.app, "library_tab", None)
        if (
            library_page is not None
            and hasattr(library_page, "refresh_from_app")
            and getattr(self.app, "stack", None) is not None
            and self.app.stack.currentWidget() is library_tab
            and library_tab.current_view_mode() == "map"
        ):
            self.app.view_controller.refresh_library_map(force=True)

    def preview_audio_path(self, source_path: str) -> None:
        audio_controller = getattr(self.app, "audio_controller", None)
        if not source_path or audio_controller is None:
            return
        audio_controller.play_path(Path(source_path))

    def find_audio_path(self, source_path: str) -> None:
        text = (source_path or "").strip()
        if not text:
            return
        path = Path(text)
        is_docked = (
            getattr(self.app, "dock_view", None) is not None
            and getattr(self.app, "stack", None) is not None
            and self.app.stack.currentWidget() is self.app.dock_view
        )
        if not is_docked and hasattr(self.app, "open_library_workspace"):
            self.app.open_library_workspace()
        search_controller = getattr(self.app, "search_controller", None)
        query = source_filter_query(path)
        if search_controller is None:
            self._select_library_path(path)
            return

        def _select_after_search(_result=None) -> None:
            try:
                search_controller.searchFinished.disconnect(_select_after_search)
            except (TypeError, RuntimeError):
                pass
            self._select_library_path(path)

        search_controller.searchFinished.connect(_select_after_search)
        search_controller.set_query(query, immediate=True)
        QTimer.singleShot(350, lambda: self._select_library_path(path))

    def _select_library_path(self, source_path: Path) -> None:
        coherence_navigation.select_library_path(self.app, source_path)

    def promote_record_as_anchor(self, record_id: str) -> None:
        record_id = (record_id or "")
        if not record_id:
            return
        promoted, _record_ids = self._promote_matching_anchors_for_records([record_id])
        if getattr(self.app, "footer", None):
            if promoted:
                self.app.footer.set_status("Coherence: added matching generated anchor.")
            else:
                self.app.footer.set_status("Coherence: no matching generated anchor exists for this sample yet.")

    def preview_refinements(self, record_ids: list[str]) -> None:
        if not record_ids or not getattr(self.app, "model", None):
            return
        model = self.app.model
        rows = coherence_navigation.source_rows_for_record_ids(model, record_ids)
        if not rows:
            return
        if hasattr(self.app, "open_library_workspace"):
            self.app.open_library_workspace()
        coherence_navigation.select_source_rows(self.app, rows)

    def _use_cached_coherence_state(self, engine) -> bool:
        results = engine.db.list_coherence_results(engine.session_id)
        if not results:
            return False
        session_state = getattr(self.app, "acoustic_session_state", None)
        staging_ids = session_state.staging_record_ids() if session_state is not None else {
            str(row.get("row_id")) for row in engine.db.get_staging_records(engine.session_id)
        }
        result_ids = {str(row.get("record_id")) for row in results}
        if not result_ids or not result_ids.issubset(staging_ids):
            return False
        auto_staged = engine.db.list_refinement_candidates(engine.session_id, state="auto_staged")
        if getattr(self.app, "system_controller", None):
            self.app.system_controller.refresh_anchor_candidates()
        if auto_staged:
            self.apply_auto_staged_refinements()
        review_rows = self._load_manual_review_session()
        if review_rows:
            self._set_manual_review_session(review_rows)
        else:
            review_rows = self._review_rows()
            if review_rows:
                self._set_manual_review_session(review_rows)
        review_count = len(review_rows)
        if review_count:
            self.app.footer.set_coherence_state(
                cached_review_state_message(review_count),
                True,
                can_review=True,
            )
        else:
            self._handle_coherence_ready("background")
        if getattr(self.app, "view_controller", None):
            self.app.view_controller.prewarm_library_map(delay_ms=250)
        self.coherenceFinished.emit()
        return True
