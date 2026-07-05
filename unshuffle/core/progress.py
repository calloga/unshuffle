from __future__ import annotations

import time
from typing import Callable, Optional


ETA_MIN_CURRENT = 1


def eta_seconds(current: int, total: int, started_at: float | None = None, now: float | None = None) -> int | None:
    current = max(0, int(current or 0))
    total = max(0, int(total or 0))
    if current < ETA_MIN_CURRENT or total <= current or started_at is None:
        return None
    elapsed = max(0.001, float((now if now is not None else time.monotonic()) - started_at))
    rate = current / elapsed
    if rate <= 0:
        return None
    return max(0, int(round((total - current) / rate)))


def format_eta(seconds: int | None) -> str:
    if seconds is None:
        return ""
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {seconds:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


def progress_message(payload: dict) -> str:
    text = str(payload.get("message") or payload.get("status") or payload.get("text") or "")
    phase = str(payload.get("phase") or "").strip()
    current = payload.get("current")
    total = payload.get("total")
    if phase and not text:
        text = phase
    if current is None or total is None:
        return text
    try:
        current_int = max(0, int(current))
        total_int = max(0, int(total))
    except (TypeError, ValueError):
        return text
    if total_int <= 0:
        return text
    remaining = max(0, total_int - current_int)
    eta_text = format_eta(payload.get("eta_seconds"))
    parts = [text or phase or "Scanning", f"{current_int}/{total_int}", f"{remaining} remaining"]
    if eta_text:
        parts.append(f"ETA {eta_text}")
    return " - ".join(parts)


class PhaseProgress:
    def __init__(
        self,
        callback: Optional[Callable[[dict], None]],
        phase: str,
        *,
        total: int = 0,
        message: str | None = None,
        update_every: int = 100,
        min_interval_seconds: float = 0.5,
    ) -> None:
        self.callback = callback
        self.phase = phase
        self.total = max(0, int(total or 0))
        self.message = message or phase
        self.update_every = max(1, int(update_every or 1))
        self.min_interval_seconds = max(0.0, float(min_interval_seconds or 0.0))
        self.started_at = time.monotonic()
        self._last_emit_at = 0.0
        self._last_current = -1

    def emit(self, current: int = 0, *, total: int | None = None, message: str | None = None, force: bool = False) -> None:
        if self.callback is None:
            return
        if total is not None:
            self.total = max(0, int(total or 0))
        current = max(0, int(current or 0))
        now = time.monotonic()
        if not force:
            if current == self._last_current:
                return
            if current % self.update_every != 0 and current < self.total:
                return
            if now - self._last_emit_at < self.min_interval_seconds and current < self.total:
                return
        self._last_current = current
        self._last_emit_at = now
        total_value = self.total
        percent = int(round((current / total_value) * 100)) if total_value > 0 else None
        payload = {
            "phase": self.phase,
            "message": message or self.message,
            "current": current,
            "total": total_value,
            "remaining": max(0, total_value - current) if total_value > 0 else None,
            "eta_seconds": eta_seconds(current, total_value, self.started_at, now),
        }
        if percent is not None:
            payload["percent"] = max(0, min(100, percent))
        self.callback(payload)
