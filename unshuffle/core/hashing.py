import hashlib
import logging
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger("unshuffle")
FAST_HASH_PREFIX = "segmd5-v1:"
FAST_HASH_CHUNK_SIZE = 128 * 1024


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


def _read_chunk(
    file_handle,
    offset: int,
    length: int,
    interrupted_check: Optional[Callable[[], bool]] = None,
) -> Optional[bytes]:
    if interrupted_check and interrupted_check():
        return None
    file_handle.seek(offset)
    return file_handle.read(length)


def _update_chunk(hasher, offset: int, data: bytes) -> None:
    hasher.update(b"chunk")
    hasher.update(offset.to_bytes(8, byteorder="big", signed=False))
    hasher.update(len(data).to_bytes(8, byteorder="big", signed=False))
    hasher.update(data)


def _fast_hash_ranges(size: int) -> list[tuple[int, int]]:
    if size <= FAST_HASH_CHUNK_SIZE:
        return [(0, size)]
    if size <= 2 * FAST_HASH_CHUNK_SIZE:
        return [(0, FAST_HASH_CHUNK_SIZE)]
    if size <= 3 * FAST_HASH_CHUNK_SIZE:
        return [
            (0, FAST_HASH_CHUNK_SIZE),
            (size - FAST_HASH_CHUNK_SIZE, FAST_HASH_CHUNK_SIZE),
        ]
    return [
        (0, FAST_HASH_CHUNK_SIZE),
        ((size - FAST_HASH_CHUNK_SIZE) // 2, FAST_HASH_CHUNK_SIZE),
        (size - FAST_HASH_CHUNK_SIZE, FAST_HASH_CHUNK_SIZE),
    ]


def get_fast_hash(
    path: Path,
    interrupted_check: Optional[Callable[[], bool]] = None,
) -> Optional[str]:
    """Calculates a segment-based fast hash for duplicate bucketing."""
    hasher = hashlib.md5()
    try:
        size = Path(path).stat().st_size
        with open(path, "rb") as file_handle:
            if interrupted_check and interrupted_check():
                return None

            hasher.update(b"size")
            hasher.update(size.to_bytes(8, byteorder="big", signed=False))

            for offset, length in _fast_hash_ranges(size):
                buf = _read_chunk(file_handle, offset, length, interrupted_check)
                if buf is None:
                    return None
                _update_chunk(hasher, offset, buf)

            return FAST_HASH_PREFIX + hasher.hexdigest()
    except OSError as exc:
        logger.debug("Could not read file %s for hashing: %s", path, exc)
        return None


def is_fast_hash(value: str | None) -> bool:
    if not isinstance(value, str) or not value.startswith(FAST_HASH_PREFIX):
        return False
    digest = value[len(FAST_HASH_PREFIX):]
    return len(digest) == 32 and all(c in "0123456789abcdef" for c in digest)


def hash_for_verification(
    path: Path,
    expected_hash: str | None,
    interrupted_check: Optional[Callable[[], bool]] = None,
) -> Optional[str]:
    if is_fast_hash(expected_hash):
        return get_fast_hash(path, interrupted_check=interrupted_check)
    return get_file_hash(path, interrupted_check=interrupted_check)
