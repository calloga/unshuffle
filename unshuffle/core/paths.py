import logging
import os
import sys
from pathlib import Path


SYSTEM_FOLDER_NAME = "DO_NOT_DELETE_unshuffle"
DRY_RUN_FOLDER_NAME = "dry_run"
HASH_CACHE_FILE = ".unshuffle_hashes.json"
DIRECTORY_DUMP_FILE = "all_directories"
DB_FILE_NAME = "unshuffle.db"
TRASH_WARNING_SESSION_COUNT = 25


def _warn_if_trash_root_grows_large(trash_root: Path) -> None:
    """Warn when session-trash accumulation becomes large enough to merit cleanup."""
    if not trash_root.exists():
        return
    try:
        session_dirs = [entry for entry in trash_root.iterdir() if entry.is_dir()]
    except OSError as exc:
        logging.debug("Could not inspect trash root %s: %s", trash_root, exc)
        return

    if len(session_dirs) > TRASH_WARNING_SESSION_COUNT:
        logging.warning(
            "Session trash under %s currently contains %d session folders. Consider manual cleanup if this target is no longer using old undo history.",
            trash_root,
            len(session_dirs),
        )


def get_global_system_dir() -> Path:
    """Returns the centralized AppData path for the Global Brain."""
    if os.name == "nt":
        appdata = os.getenv("APPDATA")
        local_appdata = os.getenv("LOCALAPPDATA")
        if appdata:
            base = Path(appdata)
        elif local_appdata:
            base = Path(local_appdata)
        else:
            base = Path.home() / "AppData" / "Roaming"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path.home() / ".config"

    folder = base / "Unshuffle"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def get_system_dir(target_dir: Path, is_dry_run: bool = False) -> Path:
    """
    Returns the appropriate metadata directory:
    - Dry Run: local `dry_run` folder in target.
    - Standard: global AppData folder.
    """
    if is_dry_run:
        folder = target_dir / DRY_RUN_FOLDER_NAME
    else:
        folder = get_global_system_dir()

    if not folder.exists():
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logging.warning("Could not create metadata folder %s: %s", folder, exc)
    return folder


def get_local_system_dir(target_dir: Path) -> Path:
    """Returns the local sidecar metadata directory in the music library."""
    folder = target_dir / SYSTEM_FOLDER_NAME
    if not folder.exists():
        try:
            folder.mkdir(parents=True, exist_ok=True)
            if os.name == "nt":
                import ctypes

                file_attribute_hidden = 0x02
                ctypes.windll.kernel32.SetFileAttributesW(str(folder), file_attribute_hidden)
        except OSError:
            pass
    return folder


def get_trash_dir(target_dir: Path, session_id: str) -> Path:
    """Returns a session-specific trash directory for original files."""
    trash_root = get_local_system_dir(target_dir) / "trash"
    _warn_if_trash_root_grows_large(trash_root)
    folder = trash_root / session_id
    folder.mkdir(parents=True, exist_ok=True)
    return folder
