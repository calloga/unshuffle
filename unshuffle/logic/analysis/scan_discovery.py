from __future__ import annotations

import os
import hashlib
from pathlib import Path
from typing import Callable

from ...core.constants import (
    AUDIO_EXTS,
    IGNORED_SYSTEM_ARTIFACT_NAMES,
    PRESERVED_MARKER,
    RESERVED_NAMES,
)
from ...core.path_safety import _is_protected_path_resolved, is_symlink_or_reparse
from ...core.progress import PhaseProgress


DISCOVERY_BATCH_SIZE = 2000


def _is_reserved_scan_name(path: Path) -> bool:
    name = path.name.casefold()
    return name in {str(value).casefold() for value in RESERVED_NAMES} or name in {
        str(value).casefold() for value in IGNORED_SYSTEM_ARTIFACT_NAMES
    }


def _signature_update(digest, kind: str, path: Path, size=None, mtime_ns=None) -> None:
    for value in (kind, path.as_posix(), "" if size is None else size, "" if mtime_ns is None else mtime_ns):
        digest.update(str(value).encode("utf-8", errors="surrogatepass"))
        digest.update(b"\0")


def _filesystem_manifest_signature(
    root_path: Path,
    target: Path | None,
    interrupted: Callable[[], bool],
) -> str | None:
    digest = hashlib.sha1()
    _signature_update(digest, "D", root_path)
    if (root_path / PRESERVED_MARKER).exists():
        return digest.hexdigest()
    for root, dirs, files in os.walk(root_path):
        if interrupted():
            return None
        current_path = Path(root)
        dirs.sort()
        files.sort()
        dirs[:] = [name for name in dirs if not _is_reserved_scan_name(current_path / name)]
        files = [name for name in files if not _is_reserved_scan_name(current_path / name)]
        dirs[:] = [name for name in dirs if not is_symlink_or_reparse(current_path / name)]
        files = [name for name in files if not is_symlink_or_reparse(current_path / name)]
        hands_off = [name for name in dirs if (current_path / name / PRESERVED_MARKER).exists()]
        for name in hands_off:
            dirs.remove(name)
        if target and current_path != root_path and _is_protected_path_resolved(current_path, target):
            dirs[:] = []
            continue
        for name in [*hands_off, *dirs]:
            path = current_path / name
            if name not in hands_off and _is_protected_path_resolved(path, root_path):
                continue
            _signature_update(digest, "D", path)
        for name in files:
            path = current_path / name
            if _is_protected_path_resolved(path, root_path):
                continue
            try:
                stat = path.stat()
                size, mtime_ns = int(stat.st_size), int(stat.st_mtime_ns)
            except OSError:
                size, mtime_ns = 0, None
            _signature_update(digest, "F", path, size, mtime_ns)
    return digest.hexdigest()


def discover_to_scan_store(
    db,
    scan_id: str,
    root_path: Path,
    *,
    target_dir: Path | None = None,
    is_interrupted: Callable[[], bool] | None = None,
    progress_callback=None,
) -> int:
    """Persist discovery in bounded batches while retaining current walk semantics."""
    root_path = Path(root_path).resolve()
    target = Path(target_dir).resolve() if target_dir else None
    interrupted = is_interrupted or (lambda: False)
    progress = PhaseProgress(
        progress_callback,
        "Discovering Samples",
        message="Discovering samples...",
        update_every=500,
    )

    conn = db.conn
    existing = conn.execute(
        "SELECT COUNT(*) FROM scan_directories WHERE scan_id = ?",
        (scan_id,),
    ).fetchone()
    if existing is not None and int(existing[0] or 0) > 0:
        run = db.get_scan_run(scan_id) if hasattr(db, "get_scan_run") else None
        if run and str(run.get("phase") or "") != "discovery":
            current_signature = _filesystem_manifest_signature(root_path, target, interrupted)
            if current_signature is None:
                db.update_scan_run(scan_id, state="paused")
                return db.count_scan_items(scan_id) + int(existing[0])
            if str(run.get("source_signature") or "") == current_signature:
                item_count = db.count_scan_items(scan_id)
                directory_count = int(existing[0])
                progress.emit(item_count + directory_count, force=True)
                return item_count + directory_count
        # Discovery has no stable filesystem cursor yet. Restart only this
        # incomplete manifest; completed hashes/features live in file_cache.
        with db.write_transaction():
            conn.execute("DELETE FROM scan_items WHERE scan_id = ?", (scan_id,))
            conn.execute("DELETE FROM scan_directories WHERE scan_id = ?", (scan_id,))

    next_directory_id = 0
    next_item_id = 0
    discovery_order = 0
    manifest_digest = hashlib.sha1()
    _signature_update(manifest_digest, "D", root_path)
    root_preserved = (root_path / PRESERVED_MARKER).exists()
    db.insert_scan_directories(
        scan_id,
        [
            (
                next_directory_id,
                discovery_order,
                None,
                0,
                root_path,
                root_path.name,
                root_preserved,
                False,
            )
        ],
    )
    next_directory_id += 1
    discovery_order += 1

    if root_preserved:
        db.update_scan_run(
            scan_id,
            discovered_count=1,
            phase="structure",
            source_signature=manifest_digest.hexdigest(),
        )
        progress.emit(1, force=True)
        return 1

    directory_batch: list[tuple] = []
    item_batch: list[tuple] = []
    pending_directory_ids: dict[str, int] = {}
    total = 1

    def flush_directories() -> None:
        if directory_batch:
            db.insert_scan_directories(scan_id, directory_batch, batch_size=DISCOVERY_BATCH_SIZE)
            directory_batch.clear()
            pending_directory_ids.clear()

    def flush_items() -> None:
        if item_batch:
            # A child's directory row may still be buffered when its files
            # reach the independent item threshold.
            flush_directories()
            db.insert_scan_items(scan_id, item_batch, batch_size=DISCOVERY_BATCH_SIZE)
            item_batch.clear()

    for root, dirs, files in os.walk(root_path):
        if interrupted():
            break
        current_path = Path(root)
        current_key = current_path.as_posix()
        parent_id = pending_directory_ids.get(current_key)
        if parent_id is None:
            row = conn.execute(
                "SELECT directory_id FROM scan_directories WHERE scan_id = ? AND normalized_path = ?",
                (scan_id, current_key),
            ).fetchone()
            if row is None:
                continue
            parent_id = int(row[0])

        dirs.sort()
        files.sort()
        dirs[:] = [name for name in dirs if not _is_reserved_scan_name(current_path / name)]
        files = [name for name in files if not _is_reserved_scan_name(current_path / name)]
        dirs[:] = [name for name in dirs if not is_symlink_or_reparse(current_path / name)]
        files = [name for name in files if not is_symlink_or_reparse(current_path / name)]

        hands_off = [name for name in dirs if (current_path / name / PRESERVED_MARKER).exists()]
        for name in hands_off:
            dirs.remove(name)

        if target and current_path != root_path and _is_protected_path_resolved(current_path, target):
            dirs[:] = []
            continue

        for name in [*hands_off, *dirs]:
            path = current_path / name
            if name not in hands_off and _is_protected_path_resolved(path, root_path):
                continue
            directory_id = next_directory_id
            next_directory_id += 1
            directory_batch.append(
                (
                    directory_id,
                    discovery_order,
                    parent_id,
                    len(path.relative_to(root_path).parts),
                    path,
                    name,
                    name in hands_off,
                    False,
                )
            )
            pending_directory_ids[path.as_posix()] = directory_id
            _signature_update(manifest_digest, "D", path)
            discovery_order += 1
            total += 1

        for name in files:
            path = current_path / name
            if _is_protected_path_resolved(path, root_path):
                continue
            try:
                stat = path.stat()
            except OSError:
                size = 0
                mtime = None
                mtime_ns = None
            else:
                size = int(stat.st_size)
                mtime = float(stat.st_mtime)
                mtime_ns = int(stat.st_mtime_ns)
            extension = path.suffix.lower()
            item_batch.append(
                (
                    next_item_id,
                    discovery_order,
                    parent_id,
                    path,
                    name,
                    extension,
                    size,
                    mtime,
                    mtime_ns,
                    False,
                    False,
                    extension in AUDIO_EXTS,
                )
            )
            _signature_update(manifest_digest, "F", path, size, mtime_ns)
            next_item_id += 1
            discovery_order += 1
            total += 1

        if len(directory_batch) >= DISCOVERY_BATCH_SIZE:
            flush_directories()
        if len(item_batch) >= DISCOVERY_BATCH_SIZE:
            flush_items()
        progress.emit(total, message=f"Discovering samples: {total - 1} items found...")

    flush_directories()
    flush_items()
    if interrupted():
        db.update_scan_run(scan_id, state="paused", phase="discovery", discovered_count=total)
    else:
        db.update_scan_run(
            scan_id,
            phase="structure",
            discovered_count=total,
            source_signature=manifest_digest.hexdigest(),
        )
    progress.emit(total, force=True)
    return total
