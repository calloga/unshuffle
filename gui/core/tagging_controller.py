from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

from gui.utils.constants import StagingColumn
from gui.widgets.sidebar import POSSIBLE_DUPLICATE_FILTER_QUERY
from unshuffle.logic.tagging import (
    POSSIBLE_DUPLICATE_TAG,
    merge_generated_tags,
)


class TaggingController(QObject):
    """Runs and applies secondary metadata tags for the current staging model."""

    taggingFinished = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.app = parent
        self._request_id = 0
        self._active_worker = None
        self._running_workers = set()
        self._last_duplicate_count = 0

    def start_tagging_pass(self, *, schedule_coherence: bool = True) -> None:
        if not getattr(self.app, "model", None):
            if schedule_coherence and getattr(self.app, "coherence_controller", None):
                self.app.coherence_controller.schedule_after_render()
            self.taggingFinished.emit()
            return
        records = list(self.app.model.records)
        if not records:
            self.clear_state()
            if schedule_coherence and getattr(self.app, "coherence_controller", None):
                self.app.coherence_controller.schedule_after_render()
            self.taggingFinished.emit()
            return
        session_state = getattr(self.app, "acoustic_session_state", None)
        cached = session_state.cached_tagging_state() if session_state is not None else self._cached_state(self._records_fingerprint(records))
        skip_pass = False
        if cached is not None:
            duplicate_count = int(cached.get("duplicate_count") or 0)
            tagged_count = session_state.tagged_duplicate_count() if session_state is not None else self._tagged_duplicate_count(records)
            if duplicate_count == 0 or tagged_count > 0:
                if tagged_count:
                    duplicate_count = tagged_count
                self._last_duplicate_count = duplicate_count
                if getattr(self.app, "library_tab", None):
                    self.app.library_tab.set_possible_duplicate_filter_enabled(bool(duplicate_count))
                if getattr(self.app, "filter_controller", None):
                    self.app.filter_controller.refresh_dock_filters()
                if getattr(self.app, "footer", None):
                    self.app.footer.set_tagging_state("", False)
                if schedule_coherence and getattr(self.app, "coherence_controller", None):
                    self.app.coherence_controller.schedule_after_render()
                self.taggingFinished.emit()
                return

        self._request_id += 1
        request_id = self._request_id
        self.app.footer.set_tagging_state("Checking possible duplicates...", True, can_filter=False)

        from .workers import TaggingWorker

        worker = TaggingWorker(request_id, records)
        self._active_worker = worker
        self._running_workers.add(worker)

        def _finished(payload: dict) -> None:
            self._running_workers.discard(worker)
            if payload.get("request_id") != self._request_id:
                self.taggingFinished.emit()
                return
            self.apply_tagging_result(payload, schedule_coherence=schedule_coherence)

        def _error(message: str) -> None:
            self._running_workers.discard(worker)
            if request_id != self._request_id:
                self.taggingFinished.emit()
                return
            self.app.footer.set_tagging_state("", False)
            self.app.footer.set_status(f"Tagging failed: {message}")
            if schedule_coherence and getattr(self.app, "coherence_controller", None):
                self.app.coherence_controller.schedule_after_render()
            self.taggingFinished.emit()

        worker.finished.connect(_finished)
        worker.finished.connect(worker.deleteLater)
        worker.error.connect(_error)
        worker.error.connect(worker.deleteLater)
        worker.start()

    def clear_state(self) -> None:
        self._request_id += 1
        self._last_duplicate_count = 0
        if getattr(self.app, "footer", None):
            self.app.footer.set_tagging_state("", False)
        if getattr(self.app, "library_tab", None):
            self.app.library_tab.set_possible_duplicate_filter_enabled(False)
        if getattr(self.app, "filter_controller", None):
            self.app.filter_controller.refresh_dock_filters()

    def apply_tagging_result(self, payload: dict, *, schedule_coherence: bool = True) -> None:
        model = getattr(self.app, "model", None)
        if model is None:
            self.taggingFinished.emit()
            return
        tags_by_path = payload.get("tags_by_path") or {}
        changed_rows: list[int] = []

        with model.suspended_sync():
            for row, rec in enumerate(model.records):
                path_key = str(Path(getattr(rec, "source_path", ""))).replace("\\", "/")
                generated_tags = tags_by_path.get(path_key, [])
                merged = merge_generated_tags(getattr(rec, "tags", []) or [], generated_tags)
                if list(getattr(rec, "tags", []) or []) == merged:
                    continue
                rec.tags = merged
                if generated_tags:
                    evidence = getattr(rec, "evidence", None)
                    if isinstance(evidence, dict):
                        evidence["generated_tags"] = list(generated_tags)
                changed_rows.append(row)

        if changed_rows:
            model._invalidate_unique_values(StagingColumn.TAGS)
            first, last = min(changed_rows), max(changed_rows)
            model.dataChanged.emit(model.index(first, StagingColumn.TAGS), model.index(last, StagingColumn.TAGS))
            if model.sync_callback:
                for row in changed_rows:
                    model.sync_callback(row, model.records[row])

        self.app.view_controller.update_library_views(tree_delay_ms=0)
        if self.app.search_controller.current_query:
            self.app.search_controller.execute_search()

        duplicate_count = int(payload.get("duplicate_file_count") or 0)
        self._last_duplicate_count = duplicate_count
        session_state = getattr(self.app, "acoustic_session_state", None)
        if session_state is not None:
            session_state.store_tagging_state(duplicate_count)
        else:
            self._store_cached_state(duplicate_count)
        summary = self._summary_text(duplicate_count)
        if getattr(self.app, "library_tab", None):
            self.app.library_tab.set_possible_duplicate_filter_enabled(bool(duplicate_count))
        if getattr(self.app, "filter_controller", None):
            self.app.filter_controller.refresh_dock_filters()
        self.app.footer.set_tagging_state(
            summary,
            bool(duplicate_count),
            can_filter=bool(duplicate_count),
        )
        request_id = self._request_id
        if duplicate_count:
            QTimer.singleShot(5000, lambda: self._hide_tagging_notice(request_id))
        if schedule_coherence and getattr(self.app, "coherence_controller", None):
            self.app.coherence_controller.schedule_after_render()
        self.taggingFinished.emit()

    def filter_possible_duplicates(self) -> None:
        if not getattr(self.app, "filter_controller", None):
            return
        self.app.filter_controller.apply_filter_query(
            POSSIBLE_DUPLICATE_FILTER_QUERY,
            True,
            mode="replace",
        )

    def _hide_tagging_notice(self, request_id: int) -> None:
        if request_id != self._request_id:
            return
        if getattr(self.app, "footer", None):
            self.app.footer.set_tagging_state("", False)

    def _summary_text(self, duplicate_count: int) -> str:
        if not duplicate_count:
            return ""
        return f"Tagging pass: {duplicate_count} possible duplicate{'s' if duplicate_count != 1 else ''}."

    def _session_cache_prefix(self) -> str:
        engine = getattr(self.app, "engine", None)
        session_id = str(getattr(engine, "session_id", "") or "").strip()
        return f"tagging_pass/{session_id}" if session_id else ""

    def _records_fingerprint(self, records) -> str:
        import hashlib
        h = hashlib.sha1()
        h.update(str(len(records)).encode("utf-8"))
        for rec in records:
            h.update(str(getattr(rec, "source_path", "")).encode("utf-8"))
            h.update(str(getattr(rec, "hash", "") or "").encode("utf-8"))
        self._last_fingerprint = h.hexdigest()
        return self._last_fingerprint

    @staticmethod
    def _tagged_duplicate_count(records) -> int:
        return sum(
            1
            for rec in records
            if POSSIBLE_DUPLICATE_TAG in {str(tag).lower() for tag in (getattr(rec, "tags", []) or [])}
        )

    def _cached_state(self, fingerprint: str) -> dict | None:
        settings = getattr(self.app, "settings", None)
        prefix = self._session_cache_prefix()
        if not settings or not prefix:
            return None
        if str(settings.value(f"{prefix}/fingerprint", "") or "") != fingerprint:
            return None
        return {"duplicate_count": int(settings.value(f"{prefix}/duplicate_count", 0) or 0)}

    def _store_cached_state(self, duplicate_count: int) -> None:
        settings = getattr(self.app, "settings", None)
        prefix = self._session_cache_prefix()
        fingerprint = getattr(self, "_last_fingerprint", "")
        if not settings or not prefix or not fingerprint:
            return
        settings.setValue(f"{prefix}/fingerprint", fingerprint)
        settings.setValue(f"{prefix}/duplicate_count", duplicate_count)
