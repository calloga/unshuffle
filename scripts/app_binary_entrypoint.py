from __future__ import annotations

import json
from pathlib import Path
import sys


def native_self_test() -> dict:
    """Exercise the packaged extensions without loading Qt or opening user data."""
    import hashlib
    import hnswlib
    import numpy as np
    from unshuffle.core.features import FEATURE_VECTOR_SIZE

    # Nonzero, distinct vectors exercise both the SIMD blocks and residual dimensions.
    base = np.arange(FEATURE_VECTOR_SIZE, dtype=np.float32) / FEATURE_VECTOR_SIZE
    vectors = np.stack([base + offset for offset in (0.0, 1.0, 3.0, 7.0)])
    index = hnswlib.Index(space="l2", dim=FEATURE_VECTOR_SIZE)
    index.init_index(max_elements=len(vectors), ef_construction=100, M=8, random_seed=100)
    index.add_items(vectors, np.arange(len(vectors)), num_threads=1)
    index.set_ef(20)
    labels, distances = index.knn_query(vectors[:2], k=2, num_threads=1)
    expected_labels = np.array([[0, 1], [1, 0]])
    expected_distances = np.array([[0, FEATURE_VECTOR_SIZE], [0, FEATURE_VECTOR_SIZE]])
    if not np.array_equal(labels, expected_labels):
        raise RuntimeError(f"Unexpected native neighbors: {labels.tolist()}")
    if not np.isfinite(distances).all() or not np.allclose(distances, expected_distances):
        raise RuntimeError(f"Unexpected native distances: {distances.tolist()}")
    extension = Path(hnswlib.__file__)
    return {
        "status": "ok",
        "feature_dimension": FEATURE_VECTOR_SIZE,
        "labels": labels.tolist(),
        "distances": distances.tolist(),
        "numpy_version": np.__version__,
        "hnswlib_path": str(extension),
        "hnswlib_sha256": hashlib.sha256(extension.read_bytes()).hexdigest(),
    }


def main() -> int:
    # Direct script execution should work just like the frozen entrypoint.
    if not getattr(sys, "frozen", False):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    if sys.argv[1:] == ["--self-test-native"]:
        try:
            print(json.dumps(native_self_test(), sort_keys=True))
            return 0
        except Exception as exc:
            print(json.dumps({"status": "error", "error": str(exc)}), file=sys.stderr)
            return 1

    from gui.main.launcher import main as launch_gui
    return launch_gui()


if __name__ == "__main__":
    raise SystemExit(main())
