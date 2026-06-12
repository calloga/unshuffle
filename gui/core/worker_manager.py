import logging
from pathlib import Path
from PySide6.QtCore import QObject, Signal, QThread
from .workers import ScanWorker, CommitWorker, UndoWorker

class WorkerManager(QObject):
    """
    Orchestrates background workers for scanning, committing, and undoing operations.
    Handles worker lifecycle and signal connections.
    """
    progress = Signal(dict)
    finished = Signal(str, object)
    error = Signal(str)
    busyStateChanged = Signal(bool)
    cancelling = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.worker = None
        self.engine = None
        self.worker_type = None
        self._cancel_requested_type = None

    def set_engine(self, engine):
        self.engine = engine

    def is_busy(self):
        if self.worker is None:
            return False
        try:
            return self.worker.isRunning()
        except (RuntimeError, AttributeError):
            self.worker = None
            return False

    def start_scan(self, engine, sources, acoustic_index=False, append=False, skip_expensive_hashes=None, min_confidence=None, existing_hashes=None, lib_hashes=None, current_records=None):
        if self.is_busy(): return False
        self.engine = engine
        if self.engine:
            self.engine.interrupted = False
        self.worker_type = "scan"
        self._cancel_requested_type = None
        self.busyStateChanged.emit(True)
        try:
            self.worker = ScanWorker(
                self.engine,
                sources,
                acoustic_index=acoustic_index,
                skip_expensive_hashes=skip_expensive_hashes,
                min_confidence=min_confidence,
                append=append,
                existing_hashes=existing_hashes,
                lib_hashes=lib_hashes,
                current_records=current_records,
            )

            self.worker.progress.connect(self.progress.emit)
            worker = self.worker
            self.worker.finished.connect(lambda new_recs, app_val, stats, w=worker: self._on_finished(w, "scan", (new_recs, app_val, stats)))
            self.worker.finished.connect(self.worker.deleteLater)
            self.worker.error.connect(lambda err, w=worker: self._on_error(w, err))
            self.worker.start(QThread.LowPriority)
        except Exception as exc:
            logging.exception("Failed to start scan worker")
            self._clear_failed_start(str(exc))
            return False
        return True

    def start_commit(self, records, move=False, dry_run=False, flat=False, no_px=False):
        if self.is_busy() or not self.engine: return False
        self.engine.interrupted = False
        self.worker_type = "commit"
        self._cancel_requested_type = None
        self.busyStateChanged.emit(True)
        try:
            self.worker = CommitWorker(self.engine, records, move, dry_run, flat, no_px)
            self._connect_and_start("commit")
        except Exception as exc:
            logging.exception("Failed to start commit worker")
            self._clear_failed_start(str(exc))
            return False
        return True

    def start_undo(self, session_id, confirm_preserved=False):
        if self.is_busy() or not self.engine or not str(session_id or "").strip(): return False
        self.engine.interrupted = False
        self.worker_type = "undo"
        self._cancel_requested_type = None
        self.busyStateChanged.emit(True)
        self.progress.emit({"message": "Preparing undo...", "current": 0, "total": 0})
        try:
            self.worker = UndoWorker(self.engine, session_id, confirm_preserved=confirm_preserved)
            self._connect_and_start("undo")
        except Exception as exc:
            logging.exception("Failed to start undo worker")
            self._clear_failed_start(str(exc))
            return False
        return True

    def request_cancel(self):
        if not self.is_busy():
            logging.debug("Cancel requested but no worker is running.")
            return False
        if self.engine:
            if getattr(self.engine, "interrupted", False):
                logging.debug("Cancel already in progress.")
                return True
            self.engine.interrupted = True
            self._cancel_requested_type = self.worker_type
            logging.info("Worker cancellation requested.")
            self.cancelling.emit()
            return True
        return False

    def cancel_all(self):
        return self.request_cancel()

    def _connect_and_start(self, worker_type):
        self.worker.progress.connect(self.progress.emit)
        worker = self.worker
        self.worker.finished.connect(lambda res, w=worker: self._on_finished(w, worker_type, res))
        self.worker.finished.connect(self.worker.deleteLater)
        self.worker.error.connect(lambda err, w=worker: self._on_error(w, err))
        self.worker.start(QThread.LowPriority)

    def _clear_failed_start(self, message: str) -> None:
        self.worker = None
        self.worker_type = None
        self._cancel_requested_type = None
        self.busyStateChanged.emit(False)
        self.error.emit(message or "Could not start background worker.")

    def _on_finished(self, worker, worker_type, res):
        if worker is not self.worker:
            return
        res = self._annotate_cancelled_result(worker_type, res)
        self.busyStateChanged.emit(False)
        self.worker = None
        self.worker_type = None
        self._cancel_requested_type = None
        self.finished.emit(worker_type, res)

    def _on_error(self, worker, err_msg):
        if worker is not self.worker:
            return
        self.busyStateChanged.emit(False)
        self.worker = None
        self.worker_type = None
        self._cancel_requested_type = None
        self.error.emit(err_msg)

    def _annotate_cancelled_result(self, worker_type, res):
        if self._cancel_requested_type != worker_type:
            return res
        if worker_type == "scan" and isinstance(res, tuple) and len(res) == 3:
            new_records, is_append, stats = res
            stats = dict(stats or {})
            stats["cancelled"] = True
            return new_records, is_append, stats
        if isinstance(res, dict):
            res = dict(res)
            res["cancelled"] = True
            return res
        return res
