from __future__ import annotations

import logging
import os
import threading
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class ResourceSample:
    elapsed_seconds: float
    phase: str
    coordinator_rss: int
    child_rss: int
    available_memory: int | None
    child_count: int


class ResourceMonitor:
    """Low-frequency process-tree telemetry for long-running operations."""

    def __init__(
        self,
        operation: str,
        *,
        interval_seconds: float = 2.0,
        sample_callback: Callable[[ResourceSample], None] | None = None,
    ) -> None:
        self.operation = str(operation)
        self.interval_seconds = max(0.1, float(interval_seconds))
        self.sample_callback = sample_callback
        self._phase = "starting"
        self._phase_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._started_at = 0.0
        self._phase_changed_at = 0.0
        self._stopped_at: float | None = None
        self._samples = 0
        self._peak_tree_rss = 0
        self._phase_peaks: dict[str, int] = defaultdict(int)
        self._phase_durations: dict[str, float] = defaultdict(float)

    def set_phase(self, phase: str | None) -> None:
        if not phase:
            return
        next_phase = str(phase)
        with self._phase_lock:
            if next_phase == self._phase:
                return
            now = time.monotonic()
            if self._started_at > 0.0 and self._stopped_at is None:
                self._phase_durations[self._phase] += max(0.0, now - self._phase_changed_at)
                self._phase_changed_at = now
            self._phase = next_phase

    def start(self) -> None:
        if self._thread is not None:
            return
        self._started_at = time.monotonic()
        self._phase_changed_at = self._started_at
        self._thread = threading.Thread(
            target=self._run,
            name=f"unshuffle-{self.operation}-resources",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> dict[str, object]:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(1.0, self.interval_seconds + 0.5))
        with self._phase_lock:
            if self._stopped_at is None:
                self._stopped_at = time.monotonic()
                if self._started_at > 0.0:
                    self._phase_durations[self._phase] += max(
                        0.0,
                        self._stopped_at - self._phase_changed_at,
                    )
            stopped_at = self._stopped_at
        summary = {
            "operation": self.operation,
            "elapsed_seconds": round(max(0.0, stopped_at - self._started_at), 3),
            "samples": self._samples,
            "peak_process_tree_mib": round(self._peak_tree_rss / (1024 * 1024), 3),
            "phase_seconds": {
                phase: round(seconds, 3)
                for phase, seconds in sorted(self._phase_durations.items())
            },
            "phase_peak_mib": {
                phase: round(value / (1024 * 1024), 3)
                for phase, value in sorted(self._phase_peaks.items())
            },
        }
        logging.info("Resource monitor summary: %s", summary)
        return summary

    def _run(self) -> None:
        try:
            import psutil
        except ImportError:
            logging.debug("Resource telemetry unavailable: psutil is not installed.")
            return
        try:
            process = psutil.Process(os.getpid())
        except psutil.Error:
            return
        while not self._stop.is_set():
            try:
                children = process.children(recursive=True)
                coordinator_rss = int(process.memory_info().rss)
                child_rss = sum(int(child.memory_info().rss) for child in children)
                available = int(psutil.virtual_memory().available)
            except (psutil.Error, OSError):
                if self._stop.wait(self.interval_seconds):
                    return
                continue
            with self._phase_lock:
                phase = self._phase
            tree_rss = coordinator_rss + child_rss
            self._samples += 1
            self._peak_tree_rss = max(self._peak_tree_rss, tree_rss)
            self._phase_peaks[phase] = max(self._phase_peaks[phase], tree_rss)
            sample = ResourceSample(
                elapsed_seconds=time.monotonic() - self._started_at,
                phase=phase,
                coordinator_rss=coordinator_rss,
                child_rss=child_rss,
                available_memory=available,
                child_count=len(children),
            )
            if self.sample_callback is not None:
                self.sample_callback(sample)
            if self._stop.wait(self.interval_seconds):
                return
