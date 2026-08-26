from __future__ import annotations

import concurrent.futures
from pathlib import Path
from typing import Callable

from ...core.concurrency import bounded_map, max_scan_workers
from ...core.hashing import get_fast_hash, get_file_hash
from ...core.progress import PhaseProgress


HASH_BATCH_SIZE = 2000


def hash_scan_items(
    db,
    scan_id: str,
    *,
    is_interrupted: Callable[[], bool] | None = None,
    progress_callback=None,
) -> None:
    interrupted = is_interrupted or (lambda: False)
    pending_total = db.count_scan_items(scan_id, "hash", "pending")
    total = db.count_scan_items(scan_id)
    completed = total - pending_total
    cache_progress = PhaseProgress(
        progress_callback,
        "Checking Cache",
        total=max(1, total),
        message=f"Checking hash cache for {pending_total} files.",
        update_every=100,
    )
    hash_progress = None
    if pending_total > 0:
        hash_progress = PhaseProgress(
            progress_callback,
            "Hashing",
            total=max(1, total),
            message=f"Fast hashing {pending_total} files.",
            update_every=25,
        )

    for batch in db.iter_scan_items(
        scan_id,
        columns="item_id, normalized_path, size, mtime",
        where_sql="hash_state = 'pending'",
        batch_size=HASH_BATCH_SIZE,
    ):
        if interrupted():
            db.update_scan_run(scan_id, state="paused", phase="hash", completed_count=completed)
            return
        stats = [(Path(row["normalized_path"]), row["size"], row["mtime"]) for row in batch]
        cached = db.get_cached_entries(stats) if hasattr(db, "get_cached_entries") else {}
        updates = []
        unresolved = []
        for row in batch:
            path = Path(row["normalized_path"])
            entry = cached.get(path.as_posix())
            if entry:
                updates.append((row["item_id"], entry.get("fast_hash"), entry.get("hash"), "done"))
            else:
                unresolved.append((row["item_id"], path))
        cache_progress.emit(min(total, completed + len(batch)))

        if unresolved:
            workers = max_scan_workers(len(unresolved))
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
                for item, fast_hash in bounded_map(
                    executor,
                    lambda value: get_fast_hash(value[1]),
                    unresolved,
                    max_pending=workers * 2,
                    is_interrupted=interrupted,
                ):
                    updates.append((item[0], fast_hash, fast_hash, "fast_new"))
                    completed += 1
                    if hash_progress is not None:
                        hash_progress.emit(min(total, completed))
        completed += len(batch) - len(unresolved)
        db.update_scan_item_hashes(scan_id, updates, batch_size=HASH_BATCH_SIZE)
        if interrupted():
            db.update_scan_run(scan_id, state="paused", phase="hash", completed_count=completed)
            return

    promotion_total = db.count_fast_hash_collision_items(scan_id)
    promotion_progress = PhaseProgress(
        progress_callback,
        "Finding Duplicates",
        total=max(1, promotion_total),
        message=f"Confirming {promotion_total} possible duplicate files.",
        update_every=25,
    )
    promoted = 0
    for group in db.iter_fast_hash_collision_items(scan_id):
        if interrupted():
            db.update_scan_run(scan_id, state="paused", phase="hash", completed_count=completed)
            return
        workers = max_scan_workers(len(group))
        updates = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            for row, full_hash in bounded_map(
                executor,
                lambda value: get_file_hash(Path(value["normalized_path"])),
                group,
                max_pending=workers * 2,
                is_interrupted=interrupted,
            ):
                updates.append((row["item_id"], row["fast_hash"], full_hash, "done"))
                promoted += 1
                promotion_progress.emit(promoted)
        db.update_scan_item_hashes(scan_id, updates)
        if interrupted():
            db.update_scan_run(scan_id, state="paused", phase="hash", completed_count=completed)
            return
    db.finalize_fast_hashes(scan_id)
    db.update_scan_run(scan_id, phase="structure", completed_count=db.count_scan_items(scan_id))
    if hash_progress is not None:
        hash_progress.emit(max(1, total), force=True)
    if promotion_total:
        promotion_progress.emit(promotion_total, force=True)


def promote_session_fast_hash_collisions(
    db,
    session_id: str,
    *,
    is_interrupted: Callable[[], bool] | None = None,
    progress_callback=None,
) -> int:
    interrupted = is_interrupted or (lambda: False)
    total = db.count_session_fast_hash_collision_items(session_id)
    if total <= 0:
        return 0
    progress = PhaseProgress(
        progress_callback,
        "Finding Duplicates",
        total=total,
        message=f"Confirming {total} possible duplicate files across source folders.",
        update_every=25,
    )
    completed = 0
    for batch in db.iter_session_fast_hash_collision_items(session_id, batch_size=1000):
        if interrupted():
            return completed
        workers = max_scan_workers(len(batch))
        updates = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            for row, full_hash in bounded_map(
                executor,
                lambda value: get_file_hash(Path(value["normalized_path"])),
                batch,
                max_pending=workers * 2,
                is_interrupted=interrupted,
            ):
                updates.append((row["scan_id"], row["item_id"], row["fast_hash"], full_hash))
                completed += 1
                progress.emit(completed)
        db.update_session_item_hashes(updates)
    progress.emit(total, force=True)
    return completed


def promote_scan_against_staging(
    db,
    session_id: str,
    scan_id: str,
    *,
    is_interrupted: Callable[[], bool] | None = None,
    progress_callback=None,
) -> int:
    """Promote provisional hashes shared by a new scan and existing staging."""
    if not hasattr(db, "iter_append_fast_hash_collision_items"):
        return 0
    interrupted = is_interrupted or (lambda: False)
    completed = 0
    for batch in db.iter_append_fast_hash_collision_items(session_id, scan_id, batch_size=1000):
        if interrupted():
            db.update_scan_run(scan_id, state="paused", phase="hash")
            return completed
        workers = max_scan_workers(len(batch))
        updates = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            for row, full_hash in bounded_map(
                executor,
                lambda value: get_file_hash(Path(value["normalized_path"])),
                batch,
                max_pending=workers * 2,
                is_interrupted=interrupted,
            ):
                updates.append((
                    str(row["source_kind"]),
                    int(row["source_id"]),
                    row.get("fast_hash"),
                    full_hash,
                ))
                completed += 1
        if updates:
            db.update_append_promoted_hashes(session_id, scan_id, updates)
        if interrupted():
            db.update_scan_run(scan_id, state="paused", phase="hash")
            return completed
    return completed
