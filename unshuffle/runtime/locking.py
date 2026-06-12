import json
import logging
import os
import socket
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..core.paths import get_local_system_dir


def _normalize_host(hostname: str) -> str:
    return (hostname or "").strip().lower()


def _machine_identity() -> str:
    hostname = _normalize_host(socket.gethostname())
    node = uuid.getnode()
    if node:
        return f"{hostname}@{node:012x}"
    return hostname


def _pid_info_fallback(pid: int):
    """Best-effort PID probe without psutil. Returns (is_alive, process_name)."""
    if pid <= 0:
        return False, ""
    if os.name == "nt":
        try:
            out = subprocess.check_output(
                ["tasklist", "/FI", f"PID eq {pid}"],
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="ignore",
            )
            lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
            for ln in lines:
                if str(pid) not in ln:
                    continue
                proc_name = ln.split()[0] if ln.split() else ""
                return True, proc_name.lower()
            return False, ""
        except Exception:
            return False, ""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False, ""
    except PermissionError:
        return True, ""
    except OSError:
        return True, ""
    return True, ""


def acquire_lock(target_dir: Path, session_id: str, log):
    lock_dir = get_local_system_dir(target_dir) / "lock"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / "lock.json"
    exclusive_guard = lock_dir / "lock.exclusive"
    stale_minutes = int(os.environ.get("UNSHUFFLE_LOCK_STALE_MINUTES", "15"))
    guard_stale_seconds = int(os.environ.get("UNSHUFFLE_LOCK_GUARD_STALE_SECONDS", "30"))
    force_takeover = os.environ.get("UNSHUFFLE_FORCE_LOCK_TAKEOVER", "0") == "1"

    current_host = _normalize_host(socket.gethostname())
    current_host_id = _machine_identity()
    lock_data = {
        "pid": os.getpid(),
        "hostname": current_host,
        "host_id": current_host_id,
        "process_name": Path(sys.executable).name.lower(),
        "session_id": session_id,
        "start_time": datetime.now().isoformat(),
    }

    def _write_lock_file():
        tmp_path = lock_path.with_name(f"{lock_path.name}.{os.getpid()}.tmp")
        try:
            with open(tmp_path, "w", encoding="utf-8") as file_handle:
                json.dump(lock_data, file_handle)
                file_handle.flush()
                os.fsync(file_handle.fileno())
            os.replace(tmp_path, lock_path)
        finally:
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except OSError:
                pass

    def _acquired():
        _cleanup_legacy_lock_dir(target_dir, log)
        return lock_path

    def _guard_age_seconds() -> float:
        try:
            mtime = datetime.fromtimestamp(exclusive_guard.stat().st_mtime, tz=timezone.utc)
            return max(0.0, (datetime.now(timezone.utc) - mtime).total_seconds())
        except OSError:
            return 0.0

    guard_fd = None
    for attempt in range(2):
        try:
            guard_fd = os.open(exclusive_guard, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
            break
        except FileExistsError as exc:
            age_seconds = _guard_age_seconds()
            if age_seconds >= guard_stale_seconds or force_takeover:
                log(
                    f"Stale lock negotiation guard detected (age {age_seconds:.1f}s). Taking over.",
                    level=logging.WARNING,
                )
                try:
                    exclusive_guard.unlink()
                except FileNotFoundError:
                    pass
                except OSError as unlink_exc:
                    raise RuntimeError(
                        "Another instance is currently negotiating the library lock. "
                        "Please retry in a moment."
                    ) from unlink_exc
                if attempt == 0:
                    continue
            raise RuntimeError(
                "Another instance is currently negotiating the library lock. "
                "Please retry in a moment."
            ) from exc

    try:
        try:
            fd = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
            with os.fdopen(fd, "w", encoding="utf-8") as file_handle:
                json.dump(lock_data, file_handle)
                file_handle.flush()
                os.fsync(file_handle.fileno())
            log("Library lock acquired.")
            return _acquired()
        except FileExistsError:
            try:
                with open(lock_path, "r", encoding="utf-8") as file_handle:
                    old_lock = json.load(file_handle)
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                if not force_takeover:
                    raise RuntimeError(
                        "Library lock metadata is missing or corrupt. "
                        "Refusing automatic takeover; set UNSHUFFLE_FORCE_LOCK_TAKEOVER=1 only after verifying no other instance is active."
                    )
                log(
                    "Lock metadata is missing or corrupt; force takeover is enabled.",
                    level=logging.WARNING,
                )
                _write_lock_file()
                log("Library lock acquired.")
                return _acquired()

            if force_takeover:
                log("Force lock takeover enabled by UNSHUFFLE_FORCE_LOCK_TAKEOVER=1.", level=logging.WARNING)
                _write_lock_file()
                log("Library lock acquired.")
                return _acquired()

            old_pid = int(old_lock["pid"])
            old_host = _normalize_host(old_lock.get("hostname", "unknown"))
            old_host_id = str(old_lock.get("host_id", "")).strip().lower()
            old_proc = str(old_lock.get("process_name", "")).lower()
            current_pid = os.getpid()
            if old_pid == current_pid:
                _write_lock_file()
                log("Library lock refreshed for current process.")
                return _acquired()
            try:
                mtime = datetime.fromtimestamp(lock_path.stat().st_mtime, tz=timezone.utc)
                lock_age_minutes = (datetime.now(timezone.utc) - mtime).total_seconds() / 60.0
            except OSError:
                lock_age_minutes = 0.0

            if old_host_id:
                same_machine = old_host_id == current_host_id
            else:
                same_machine = not old_host or old_host == current_host

            if not same_machine:
                raise RuntimeError(
                    f"Library is locked by another machine ({old_host}, PID {old_pid}). "
                    f"Lock age: {lock_age_minutes:.1f}m; automatic cross-machine takeover is disabled."
                )

            try:
                import psutil

                is_alive = psutil.pid_exists(old_pid)
                running_name = ""
                if is_alive:
                    try:
                        running_name = psutil.Process(old_pid).name().lower()
                    except Exception:
                        running_name = ""
            except ImportError:
                is_alive, running_name = _pid_info_fallback(old_pid)

            if is_alive and old_proc and running_name and running_name != old_proc:
                is_alive = False

            if not is_alive:
                log(
                    f"Stale lock detected (PID {old_pid} not running, age {lock_age_minutes:.1f}m). Taking over.",
                    level=logging.WARNING,
                )
                _write_lock_file()
                log("Library lock acquired.")
                return _acquired()

            raise RuntimeError(
                f"Library is locked by another instance (PID {old_pid} on {old_host}). "
                f"Lock age: {lock_age_minutes:.1f}m; stale threshold: {stale_minutes}m."
            )
    finally:
        if guard_fd is not None:
            try:
                os.close(guard_fd)
            except OSError:
                pass
        try:
            if exclusive_guard.exists():
                exclusive_guard.unlink()
        except OSError:
            pass


def release_lock(lock_path: Optional[Path], log=None):
    if lock_path and lock_path.exists():
        try:
            with open(lock_path, "r", encoding="utf-8") as file_handle:
                lock_data = json.load(file_handle)
            current_process = Path(sys.executable).name.lower()
            owns_lock = (
                int(lock_data.get("pid", -1)) == os.getpid()
                and str(lock_data.get("host_id", "")).strip().lower() == _machine_identity()
                and str(lock_data.get("process_name", "")).lower() == current_process
            )
            if not owns_lock:
                if log is not None:
                    try:
                        log("Library lock not released because it is owned by another process.", level=logging.WARNING)
                    except Exception:
                        pass
                return
            lock_path.unlink()
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass
        else:
            if log is None:
                return
            try:
                log("Library lock released.")
            except Exception:
                pass


def _is_legacy_lock_temp_file(path: Path) -> bool:
    name = path.name
    return name.startswith("lock.json.") and name.endswith(".tmp")


def _cleanup_legacy_lock_dir(target_dir: Path, log=None) -> None:
    legacy_dir = target_dir / ".unshuffle"
    try:
        if not legacy_dir.exists() or not legacy_dir.is_dir():
            return

        entries = list(legacy_dir.iterdir())
        allowed_names = {"lock.json", "lock.exclusive"}
        for entry in entries:
            if entry.is_dir() or (entry.name not in allowed_names and not _is_legacy_lock_temp_file(entry)):
                if log:
                    log(f"Legacy lock folder left in place; unknown entry: {entry}", level=logging.DEBUG)
                return

        for entry in entries:
            try:
                entry.unlink()
            except OSError:
                return
        try:
            legacy_dir.rmdir()
        except OSError:
            return
    except OSError:
        return
