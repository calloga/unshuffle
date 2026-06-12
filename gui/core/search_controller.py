import logging
from PySide6.QtCore import QObject, Signal, QTimer
from .search_engine import SearchEngine

class SearchController(QObject):
    """
    Manages the search lifecycle, query building, and result propagation.
    """
    searchStarted = Signal(str)
    searchFinished = Signal(dict)
    searchError = Signal(str)
    filterChanged = Signal(str)

    def __init__(self, engine, model, proxy_model, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.model = model
        self.proxy_model = proxy_model
        self.app = parent 
        self.search_engine = SearchEngine()
        
        self._current_query = ""
        self._search_request_id = 0
        self._active_worker = None
        self._audio_types = None
        self._running_workers = set()
        self._semantic_suggestions_seen: set[tuple[str, str]] = set()
        
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self.execute_search)

    def on_search_finished_logic(self, results):
        """Dispatched after search worker completes."""
        if isinstance(results, dict) and "error" in results:
            self._set_search_error_status(str(results["error"]))
            return

        if self.app:
            if hasattr(self.app, "handle_search_results_applied"):
                self.app.handle_search_results_applied()
            elif hasattr(self.app, "view_controller"):
                self.app.view_controller.update_footer_count()
        
        if self.app:
            if hasattr(self.app, "schedule_search_tree_refresh"):
                self.app.schedule_search_tree_refresh(0)
            elif hasattr(self.app, "view_controller"):
                self.app.view_controller.schedule_tree_rebuild(0)

    def sync_search_ui(self, query):
        """Updates UI components to match current query state."""
        from .filter_query import (
            active_saved_filter_queries_for_search,
            active_source_filters_for_search,
            active_categories_for_search,
            active_confidence_range_for_search
        )

        saved_filters = []
        if self.app and hasattr(self.app, "settings_controller"):
            saved_filters = self.app.settings_controller.get_saved_filters()
        if self.app and hasattr(self.app, "library_tab"):
            from ..widgets.sidebar import (
                POSSIBLE_DUPLICATE_FILTER_NAME,
                POSSIBLE_DUPLICATE_FILTER_QUERY,
                CORRUPT_SILENT_EMPTY_FILTER_NAME,
                CORRUPT_SILENT_EMPTY_FILTER_QUERY,
            )

            saved_filters = [
                {"name": POSSIBLE_DUPLICATE_FILTER_NAME, "query": POSSIBLE_DUPLICATE_FILTER_QUERY},
                {"name": CORRUPT_SILENT_EMPTY_FILTER_NAME, "query": CORRUPT_SILENT_EMPTY_FILTER_QUERY},
                *saved_filters,
            ]
        active_saved_filters = active_saved_filter_queries_for_search(query, saved_filters)
        active_source_filters = active_source_filters_for_search(query)
        active_categories = active_categories_for_search(query)
        confidence_range = active_confidence_range_for_search(query) or (0.0, 1.0)

        if self.proxy_model:
            self.proxy_model.set_confidence_range(*confidence_range)
        if self.app:
            if hasattr(self.app, "sync_type_filter_state"):
                self.app.sync_type_filter_state()
            if hasattr(self.app, "sync_search_ui_state"):
                self.app.sync_search_ui_state(
                    query=query,
                    active_saved_filters=active_saved_filters,
                    active_source_filters=active_source_filters,
                    active_categories=active_categories,
                    confidence_range=confidence_range,
                )
            else:
                if hasattr(self.app, "library_tab"):
                    edit = self.app.library_tab.edit_search
                    if edit.text() != query:
                        edit.blockSignals(True)
                        edit.setText(query)
                        edit.blockSignals(False)
                    self.app.library_tab.set_active_saved_filters(active_saved_filters)
                    self.app.library_tab.set_active_source_filters(active_source_filters)
                    self.app.library_tab.category_carousel.set_active_values(active_categories)
                    self.app.library_tab.signal_floor_control.set_range(*confidence_range)
                if hasattr(self.app, "dock_view"):
                    self.app.dock_view.set_search_text(query)
                    self.app.dock_view.set_active_saved_filters(active_saved_filters | active_source_filters)
                    self.app.dock_view.set_category_state(active_categories)
                    self.app.dock_view.set_confidence_range(*confidence_range)
        
        self._current_query = query

    def handle_type_filter(self, oneshots, loops, all_files):
        """Updates proxy model types and triggers search refresh."""
        types = set()
        if all_files:
            types = None
        else:
            if oneshots: types.add("Oneshots")
            if loops: types.add("Loops")
        
        self._audio_types = types
        if self.proxy_model:
            self.proxy_model.set_audio_types(types)
        if self.app and hasattr(self.app, "sync_type_filter_state"):
            self.app.sync_type_filter_state()
        
        if self._current_query or types is not None:
            self.execute_search()
        else:
            self.app.view_controller.update_library_views(0)
        if self.app and hasattr(self.app, "view_controller"):
            self.app.view_controller.refresh_docked_map(force=False)
        if self.app and not getattr(self.app, "_restoring_library_page_state", False) and hasattr(self.app, "save_library_page_state"):
            self.app.save_library_page_state()

    @property
    def current_query(self):
        return self._current_query

    def _set_search_error_status(self, err_msg: str):
        if not self.app:
            return
        text = f"Search Error: {err_msg}"
        if hasattr(self.app, "set_search_status"):
            self.app.set_search_status(text)
        elif hasattr(self.app, "footer"):
            self.app.footer.set_status(text)

    def clear_query_state(self, *, sync_ui: bool = True):
        """Clear search/filter state without starting a search worker."""
        self._search_timer.stop()
        self._current_query = ""
        if sync_ui:
            self.filterChanged.emit("")
        if self.app and not getattr(self.app, "_restoring_library_page_state", False) and hasattr(self.app, "save_library_page_state"):
            self.app.save_library_page_state()
        if self.model:
            self.model.clear_similarity_scores()
        if self.proxy_model:
            self.proxy_model.set_matched_ids(None)

    def set_query(self, text: str, immediate=False):
        text = (text or "").strip()
        if self._current_query == text and not immediate:
            return
        
        self._current_query = text
        self.filterChanged.emit(text)
        if self.app and not getattr(self.app, "_restoring_library_page_state", False) and hasattr(self.app, "save_library_page_state"):
            self.app.save_library_page_state()
        
        if immediate:
            self._search_timer.stop()
            self.execute_search()
        else:
            self._search_timer.start(100)

    def execute_search(self):
        if not self.engine:
            return
            
        self._search_request_id += 1
        request_id = self._search_request_id
        query = self._current_query
        
        self.searchStarted.emit(query)
        if not query:
            if self.model:
                self.model.clear_similarity_scores()
            if self.proxy_model:
                self.proxy_model.set_matched_ids(None)
            self.searchFinished.emit(
                {
                    "request_id": request_id,
                    "query_text": query,
                    "matched_ids": None,
                }
            )
            return

        if not self.search_engine.has_database_terms(query):
            if self.model:
                self.model.clear_similarity_scores()
            if self.proxy_model:
                self.proxy_model.set_matched_ids(None)
            self.searchFinished.emit(
                {
                    "request_id": request_id,
                    "query_text": query,
                    "matched_ids": None,
                }
            )
            return
        
        from .workers import SearchWorker
        
        if self._active_worker:
            try:
                self._active_worker.finished.disconnect()
            except Exception: pass
            
        worker = SearchWorker(request_id, self.search_engine.bridge, query)
        self._active_worker = worker
        self._running_workers.add(worker)
        
        def _on_finished(result):
            if worker in self._running_workers:
                self._running_workers.remove(worker)
            
            if result.get("request_id") != self._search_request_id:
                return
            
            matched_ids = result.get("matched_ids")
            active_query = result.get("query_text", "")
            
            if matched_ids is not None:
                if self.search_engine.is_similarity_query(active_query) and isinstance(matched_ids, list):
                    if self.model:
                        self.model.apply_similarity_ranking(matched_ids)
                else:
                    if self.model:
                        self.model.clear_similarity_scores()
                
                self.proxy_model.set_matched_ids(set(matched_ids) if isinstance(matched_ids, list) else matched_ids)
            else:
                if self.model:
                    self.model.clear_similarity_scores()
                if self.proxy_model:
                    self.proxy_model.set_matched_ids(None)
                
            self.searchFinished.emit(result)
        
        def _on_error(err_msg):
            if worker in self._running_workers:
                self._running_workers.remove(worker)
            
            if request_id != self._search_request_id:
                return
            logging.warning("Background search failed; clearing filter state: %s", err_msg)
            if self.proxy_model:
                self.proxy_model.set_matched_ids(set())
            self._set_search_error_status(err_msg)
            self.searchError.emit(err_msg)

        worker.finished.connect(_on_finished)
        worker.finished.connect(worker.deleteLater)
        worker.error.connect(_on_error)
        worker.error.connect(worker.deleteLater)
        worker.start()

    def _maybe_prompt_semantic_filter(self, results) -> None:
        suggestion = self._semantic_filter_suggestion(results)
        if not suggestion:
            return
        query, filter_text = suggestion
        key = (query.casefold(), filter_text.casefold())
        if key in self._semantic_suggestions_seen:
            return
        self._semantic_suggestions_seen.add(key)
        QTimer.singleShot(0, lambda: self._show_semantic_filter_prompt(query, filter_text))

    def _show_semantic_filter_prompt(self, query: str, filter_text: str) -> None:
        if self._current_query.casefold() != query.casefold():
            return
        from PySide6.QtWidgets import QMessageBox

        parent = self.app if self.app is not None else None
        message = QMessageBox(parent)
        message.setWindowTitle("Use semantic filter?")
        message.setText(f"Did you mean {filter_text}?")
        use_filter = message.addButton("Use Filter", QMessageBox.AcceptRole)
        message.addButton("Keep Search", QMessageBox.RejectRole)
        message.setDefaultButton(use_filter)
        message.exec()
        if message.clickedButton() is use_filter:
            self.set_query(filter_text, immediate=True)

    def _semantic_filter_suggestion(self, results) -> tuple[str, str] | None:
        if not isinstance(results, dict):
            return None
        query = str(results.get("query_text") or "").strip()
        matched_ids = results.get("matched_ids")
        if not query or not matched_ids:
            return None
        plain_value = self._plain_semantic_query_value(query)
        if not plain_value:
            return None
        records = self._records_for_matched_ids(matched_ids)
        if not records:
            return None
        candidate_prefixes = []
        for prefix, getter in (
            ("packname", lambda rec: [getattr(rec, "pack", "")]),
            ("category", lambda rec: [getattr(rec, "category", "")]),
            ("subcategory", lambda rec: [getattr(rec, "subcategory", "")]),
            ("type", lambda rec: [getattr(rec, "audio_type", "")]),
            ("tag", lambda rec: list(getattr(rec, "tags", []) or [])),
        ):
            if all(self._record_values_match_plain_query(getter(record), plain_value) for record in records):
                candidate_prefixes.append(prefix)
        if len(candidate_prefixes) != 1:
            return None
        escaped = plain_value.replace('"', '\\"')
        return query, f'{candidate_prefixes[0]}:"{escaped}"'

    def _plain_semantic_query_value(self, query: str) -> str:
        groups = SearchEngine.parse_query_groups(query)
        if len(groups) != 1 or len(groups[0]) != 1:
            return ""
        term = groups[0][0].strip()
        if not term or SearchEngine._split_field_term(term):
            return ""
        return term.strip().strip('"')

    def _records_for_matched_ids(self, matched_ids) -> list[object]:
        if self.model is None:
            return []
        try:
            id_set = {int(value) for value in matched_ids}
        except (TypeError, ValueError):
            return []
        records = []
        for row, record in enumerate(getattr(self.model, "records", []) or []):
            record_id = self.model.record_id(row) if hasattr(self.model, "record_id") else row
            try:
                normalized_id = int(record_id)
            except (TypeError, ValueError):
                normalized_id = row
            if normalized_id in id_set:
                records.append(record)
        return records

    @staticmethod
    def _record_values_match_plain_query(values, plain_value: str) -> bool:
        needle = plain_value.casefold()
        if not needle:
            return False
        return any(needle in str(value or "").casefold() for value in values)

    def set_confidence_range(self, min_val: float, max_val: float):
        from .filter_query import remove_confidence_filters, confidence_filter_query
        
        base_query = remove_confidence_filters(self._current_query)
        if abs(min_val - 0.0) < 0.0001 and abs(max_val - 1.0) < 0.0001:
            new_query = base_query
        else:
            conf_query = confidence_filter_query(min_val, max_val)
            new_query = f"{base_query} AND {conf_query}" if base_query else conf_query
            
        self.set_query(new_query)

    def apply_filter(self, filter_text: str, is_active: bool, mode: str = "replace"):
        """High-level API to modify the current query."""
        from .filter_query import query_contains_token, remove_filter_query
        
        current = self._current_query
        new_query = current
        
        if not is_active:
            new_query = remove_filter_query(current, filter_text)
        elif not query_contains_token(current, filter_text):
            if mode == "or" and current:
                new_query = f"{current} OR {filter_text}"
            elif mode == "and" and current:
                new_query = f"{current} AND {filter_text}"
            else:
                new_query = filter_text
        
        self.set_query(new_query)

    def handle_category_filter(self, category: str, is_active: bool):
        """Specifically handles category carousel signals."""
        token = f'category:"{category}"'
        self.apply_filter(token, is_active, mode="replace")
