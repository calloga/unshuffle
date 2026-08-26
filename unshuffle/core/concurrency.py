from __future__ import annotations

import os
import sys  # Kept as an observable platform hook for compatibility.
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import FIRST_COMPLETED, Executor, Future, wait
from typing import TypeVar

_T = TypeVar("_T")
_R = TypeVar("_R")

LOW_RESOURCE_SCAN_WORKERS = 4
DEFAULT_SCAN_WORKER_CAP = 8


def max_scan_workers(total: int, *, pool_cap: int = DEFAULT_SCAN_WORKER_CAP) -> int:
    if total <= 0:
        return 1

    env_cap = _read_env_int("UNSHUFFLE_MAX_SCAN_WORKERS")
    if env_cap is not None and env_cap > 0:
        return max(1, min(env_cap, total))

    return max(1, min(os.cpu_count() or 1, pool_cap, total))


def bounded_map(
    executor: Executor,
    func: Callable[[_T], _R],
    items: Iterable[_T],
    *,
    max_pending: int,
    is_interrupted: Callable[[], bool] | None = None,
) -> Iterator[tuple[_T, _R]]:
    pending: dict[Future[_R], _T] = {}
    iterator = iter(items)
    max_pending = max(1, max_pending)

    def fill() -> None:
        while len(pending) < max_pending:
            if is_interrupted and is_interrupted():
                return
            try:
                item = next(iterator)
            except StopIteration:
                return
            pending[executor.submit(func, item)] = item

    try:
        fill()
        while pending:
            if is_interrupted and is_interrupted():
                return

            done, _pending = wait(tuple(pending), return_when=FIRST_COMPLETED)
            for future in done:
                item = pending.pop(future)
                yield item, future.result()
            fill()
    finally:
        for future in pending:
            future.cancel()


def _read_env_int(key: str) -> int | None:
    raw = os.environ.get(key, "")
    try:
        return int(raw) if raw else None
    except ValueError:
        return None
