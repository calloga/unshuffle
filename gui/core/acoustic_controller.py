import logging
from pathlib import Path
from PySide6.QtCore import QObject, Signal, QTimer
from .filter_query import active_categories_for_search

class AcousticController(QObject):
    """
    Handles similarity-based discovery and 'vibe' exploration.
    """
    vibeStarted = Signal(str) 
    vibeCleared = Signal()
    biasChanged = Signal(float)
    
    def __init__(self, model, proxy_model, parent=None):
        super().__init__(parent)
        self.model = model
        self.proxy_model = proxy_model
        self.app = parent 
        self._previous_query = ""
        self._has_previous_query = False
        
        self._bias_timer = QTimer(self)
        self._bias_timer.setSingleShot(True)
        self._bias_timer.timeout.connect(self._apply_pending_bias)
        self._pending_bias = 0.0
        self._similarity_request_id = 0
        self._active_worker = None
        self._running_workers = set()

    def _refresh_similarity_views(self):
        if self.app and hasattr(self.app, "view_controller"):
            self.app.view_controller.update_library_views(tree_delay_ms=0)

    def handle_similarity_request_compact(self, target):
        rec = self.handle_similarity_request(target)
        if rec:
            self.app.vibe_bar.set_value(0)
            if hasattr(self.app, "dock_view"):
                self.app.dock_view.set_vibe_state(rec.source_path.name, 0, True)

    def handle_similarity_request(self, target):
        """Resolves target to a record and triggers similarity search."""
        if not self.model:
            return

        rec = None
        if hasattr(target, "column") and hasattr(target, "row"):
            if target is None or not target.isValid():
                return None
            source_index = self.proxy_model.mapToSource(target)
            if not source_index.isValid():
                return None
            row = source_index.row()
            records = getattr(self.model, "records", [])
            if row < 0 or row >= len(records):
                return None
            rec = records[row]
        elif hasattr(target, "acoustic_vector"):
            rec = target
        elif isinstance(target, Path):
            if hasattr(self.model, "find_record_by_source_path"):
                rec = self.model.find_record_by_source_path(target)
            else:
                for item in self.model.records:
                    if item.source_path == target:
                        rec = item
                        break
        
        if rec:
            self.anchor_similarity(rec)
            return rec
        return None

    def on_similarity_bias_changed(self, value):
        self.set_bias(value)
        if hasattr(self.app, "dock_view"):
            anchor_text = self.app.vibe_bar.anchor_text()
            self.app.dock_view.set_vibe_state(anchor_text, value, self.app.vibe_bar.isVisible())

    def anchor_similarity(self, anchor_rec):
        if not self.model or not anchor_rec or not anchor_rec.acoustic_vector:
            self._clear_similarity_state()
            self._refresh_similarity_views()
            self.vibeCleared.emit()
            return

        search_controller = getattr(self.app, "search_controller", None)
        self._prepare_search_for_similarity(search_controller)

        category = anchor_rec.category
        audio_type = getattr(anchor_rec, "audio_type", "")
      
        cat_records = [
            (i, r)
            for i, r in enumerate(self.model.records)
            if r.category == category
            and getattr(r, "audio_type", "") == audio_type
            and r.acoustic_vector
        ]

        if not cat_records:
            self._clear_similarity_state()
            self._refresh_similarity_views()
            self.vibeCleared.emit()
            self._restore_previous_query()
            return

        anchor_row = -1
        candidates = []

        for i, rec in cat_records:
            if rec == anchor_rec:
                anchor_row = i
            candidates.append((i, rec.acoustic_vector, getattr(rec, "duration", 0.0)))

        if not candidates or anchor_row < 0:
            self._clear_similarity_state()
            self._refresh_similarity_views()
            self.vibeCleared.emit()
            self._restore_previous_query()
            return

        self._similarity_request_id += 1
        request_id = self._similarity_request_id
        from .workers import SimilarityWorker

        worker = SimilarityWorker(
            request_id,
            anchor_row,
            anchor_rec.acoustic_vector,
            getattr(anchor_rec, "duration", 0.0),
            candidates,
        )
        self._active_worker = worker
        self._running_workers.add(worker)

        def _on_finished(result):
            self._running_workers.discard(worker)
            if result.get("request_id") != self._similarity_request_id:
                return
            distances = result.get("distances") or {}
            if not distances:
                self._clear_similarity_state()
                self._refresh_similarity_views()
                self.vibeCleared.emit()
                self._restore_previous_query()
                return

            self.proxy_model.set_matched_ids(None)
            if self.model and hasattr(self.model, "clear_similarity_scores"):
                self.model.clear_similarity_scores()
            elif self.model:
                self.model.scores.clear()

            self.proxy_model.set_similarity_data(
                distances,
                float(result.get("avg_dist", 0.0) or 0.0),
                int(result.get("anchor_row", -1)),
            )
            self._refresh_similarity_views()
            self.vibeStarted.emit(anchor_rec.source_path.name)

        def _on_error(_err_msg):
            self._running_workers.discard(worker)
            if request_id != self._similarity_request_id:
                return
            self._clear_similarity_state()
            self._refresh_similarity_views()
            self.vibeCleared.emit()
            self._restore_previous_query()

        worker.finished.connect(_on_finished)
        worker.finished.connect(worker.deleteLater)
        worker.error.connect(_on_error)
        worker.error.connect(worker.deleteLater)
        worker.start()

    def _clear_similarity_state(self):
        self.proxy_model.set_matched_ids(None)
        self.proxy_model.clear_similarity()
        if self.model and hasattr(self.model, "clear_similarity_scores"):
            self.model.clear_similarity_scores()
        elif self.model:
            self.model.scores.clear()

    def _prepare_search_for_similarity(self, search_controller) -> None:
        if search_controller is None:
            return
        current_query = search_controller.current_query
        if not self._has_previous_query:
            self._previous_query = current_query
            self._has_previous_query = True
        category_query = self._category_only_query(current_query)
        if category_query:
            search_controller.set_query(category_query, immediate=True)
        else:
            search_controller.clear_query_state(sync_ui=True)

    def _restore_previous_query(self) -> None:
        if not self._has_previous_query:
            return
        search_controller = getattr(self.app, "search_controller", None)
        previous_query = self._previous_query
        self._previous_query = ""
        self._has_previous_query = False
        if search_controller is None:
            return
        if previous_query:
            search_controller.set_query(previous_query, immediate=True)
        else:
            search_controller.clear_query_state(sync_ui=True)

    @staticmethod
    def _category_only_query(query: str) -> str:
        categories = sorted(active_categories_for_search(query or ""))
        parts = []
        for category in categories:
            escaped = category.replace('"', '\\"')
            parts.append(f'category:"{escaped}"')
        return " OR ".join(parts)

    def set_bias(self, value: float):
        self._pending_bias = value
        self._bias_timer.start(45)

    def _apply_pending_bias(self):
        self.proxy_model.set_similarity_bias(self._pending_bias)
        self.biasChanged.emit(self._pending_bias)

    def clear_vibe(self):
        self._clear_similarity_state()
        self._refresh_similarity_views()
        self.vibeCleared.emit()
        self._restore_previous_query()
