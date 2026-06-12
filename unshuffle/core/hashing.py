import hashlib
import logging
from pathlib import Path
from typing import Callable, Optional


logger = logging.getLogger("unshuffle")


def get_file_hash(
    filepath: Path,
    interrupted_check: Optional[Callable[[], bool]] = None,
) -> Optional[str]:
    """Calculates the MD5 hash of an audio file for deduplication."""
    hasher = hashlib.md5()
    try:
        with open(filepath, "rb") as file_handle:
            for buf in iter(lambda: file_handle.read(65536), b""):
                if interrupted_check and interrupted_check():
                    return None
                hasher.update(buf)
        return hasher.hexdigest()
    except OSError as exc:
        logger.debug("Could not read file %s for hashing: %s", filepath, exc)
        return None

