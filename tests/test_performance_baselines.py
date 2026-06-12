from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from unshuffle.core.features import FEATURE_VECTOR_SIZE


def test_performance_baseline_quick_command_runs() -> None:
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "scripts/performance_baselines.py", "--quick"],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert set(payload) == {
        "tree_routing",
        "library_tree_payload",
        "fts_search",
        "staging_writes",
        "coherence_distances",
        "startup_restore",
    }
    for metrics in payload.values():
        assert metrics["records"] == 120
        assert metrics["rounds"] == 1
        assert metrics["median_seconds"] >= 0


def test_coherence_benchmark_uses_current_feature_vector_size(monkeypatch) -> None:
    from scripts import performance_baselines

    captured = []

    def fake_audit(self, records):
        captured.extend(records)
        return [], []

    monkeypatch.setattr(performance_baselines.CoherenceEngine, "audit", fake_audit)

    performance_baselines.benchmark_coherence_distances(performance_baselines.make_records(12), 12)

    assert captured
    assert {len(record.vector) for record in captured} == {FEATURE_VECTOR_SIZE}
