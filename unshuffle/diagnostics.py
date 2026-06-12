from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import faulthandler
import logging
import platform
import subprocess
import sys
import traceback
from pathlib import Path

from .audio import SimilarityEngine
from .core.constants import APP_NAME, APP_VERSION
from .persistence import get_global_system_dir

MAX_LAUNCHER_LOG_BYTES = 1_000_000
_FAULT_LOG_HANDLE = None


@dataclass(frozen=True)
class NativeComponentStatus:
    name: str
    path: str
    available: bool
    version: str
    detail: str


def _append_text(path: Path, text: str) -> None:
    if path.exists() and path.stat().st_size > MAX_LAUNCHER_LOG_BYTES:
        path.write_text(
            path.read_text(encoding="utf-8", errors="replace")[-MAX_LAUNCHER_LOG_BYTES:],
            encoding="utf-8",
        )
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(text)
        if not text.endswith("\n"):
            handle.write("\n")


def _launcher_log_path(channel: str) -> Path:
    safe_channel = "".join(ch for ch in channel if ch.isalnum() or ch in ("_", "-")).strip() or "launcher"
    return get_global_system_dir() / f"{safe_channel}_crash.log"


def enable_launcher_fault_log() -> Path | None:
    """Capture fatal native/Python crashes that bypass sys.excepthook."""
    global _FAULT_LOG_HANDLE
    try:
        if _FAULT_LOG_HANDLE is not None:
            return get_global_system_dir() / "gui_launcher_native_crash.log"
        path = get_global_system_dir() / "gui_launcher_native_crash.log"
        _FAULT_LOG_HANDLE = open(path, "a", encoding="utf-8")
        _FAULT_LOG_HANDLE.write(
            "\n".join(
                [
                    f"[{datetime.now(timezone.utc).isoformat()}] {APP_NAME} launcher fault handler enabled",
                    f"app_version={APP_VERSION}",
                    f"python={platform.python_version()}",
                    f"platform={platform.platform()}",
                    f"executable={sys.executable}",
                    "",
                ]
            )
        )
        _FAULT_LOG_HANDLE.flush()
        faulthandler.enable(file=_FAULT_LOG_HANDLE, all_threads=True)
        return path
    except Exception:
        logging.exception("Failed to enable launcher fault log")
        return None


def write_launcher_event_log(message: str, **fields: object) -> Path | None:
    try:
        lines = [
            f"[{datetime.now(timezone.utc).isoformat()}] {APP_NAME} launcher event",
            f"app_version={APP_VERSION}",
            f"python={platform.python_version()}",
            f"platform={platform.platform()}",
            f"executable={sys.executable}",
            f"message={message}",
        ]
        for key, value in fields.items():
            lines.append(f"{key}={value}")
        lines.append("")
        path = get_global_system_dir() / "gui_launcher_events.log"
        _append_text(path, "\n".join(lines))
        return path
    except Exception:
        logging.exception("Failed to write launcher event log")
        return None


def write_launcher_crash_log(channel: str, exc: BaseException | None = None, trace_text: str | None = None) -> Path | None:
    try:
        if trace_text is None:
            if exc is None:
                trace_text = "No traceback captured."
            else:
                trace_text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))

        report = "\n".join(
            [
                f"[{datetime.now(timezone.utc).isoformat()}] {APP_NAME} launcher crash",
                f"app_version={APP_VERSION}",
                f"python={platform.python_version()}",
                f"platform={platform.platform()}",
                f"executable={sys.executable}",
                "",
                trace_text.rstrip(),
                "",
            ]
        )
        path = _launcher_log_path(channel)
        _append_text(path, report)
        return path
    except Exception:
        logging.exception("Failed to write launcher crash log for %s", channel)
        return None


def detect_native_component_status() -> NativeComponentStatus:
    engine = SimilarityEngine()
    extractor_path = Path(engine.extractor_path)
    version = "Unavailable"
    detail = "Extractor executable was not found."

    if extractor_path.exists():
        detail = f"Extractor available at {extractor_path}"
        try:
            result = subprocess.run(
                [str(extractor_path), "--version"],
                capture_output=True,
                text=True,
                timeout=5,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0,
            )
            if result.returncode == 0:
                version = (result.stdout or "").strip() or "Available"
            else:
                version = "Available"
                stderr = (result.stderr or "").strip()
                if stderr:
                    detail = f"{detail} ({stderr})"
        except Exception as exc:
            version = "Available"
            detail = f"{detail} (version probe failed: {exc})"
    elif extractor_path.name == engine.extractor_path:
        detail = f"Extractor not found on disk. Expected candidate name: {engine.extractor_path}"

    return NativeComponentStatus(
        name="Similarity Extractor",
        path=str(extractor_path),
        available=extractor_path.exists(),
        version=version,
        detail=detail,
    )


def get_version_report() -> dict[str, str]:
    native = detect_native_component_status()
    return {
        "app_name": APP_NAME,
        "app_version": APP_VERSION,
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "native_name": native.name,
        "native_version": native.version,
        "native_path": native.path,
        "native_available": "yes" if native.available else "no",
        "native_detail": native.detail,
    }
