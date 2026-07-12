from __future__ import annotations

import threading

import numpy as np


_EIGH_LOCK = threading.Lock()


def symmetric_eigh(matrix: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Serialize LAPACK eigensolvers that are unsafe across concurrent GUI workers."""
    with _EIGH_LOCK:
        return np.linalg.eigh(matrix)
