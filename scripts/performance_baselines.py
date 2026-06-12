from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from gui.models.library_tree_resolution import build_normal_resolved_tree
from gui.widgets.startup_launcher import StartupLaunchRequest
from unshuffle.core import PlanRecord
from unshuffle.core.features import FEATURE_VECTOR_SIZE
from unshuffle.logic.coherence import CoherenceEngine
from unshuffle.logic.coherence.models import CoherenceRecord
from unshuffle.logic.tree_organization import TreeOrganizationNode, TreeOrganizationProfile, TreeOrganizationResolver
from unshuffle.persistence import UnshuffleDB


Benchmark = Callable[[list[PlanRecord], int], int]


def make_records(count: int) -> list[PlanRecord]:
    categories = ["Kicks", "Snares", "Bass", "FX", "Hats & Cymbals", "Percussion"]
    audio_types = ["Oneshots", "Loops"]
    records: list[PlanRecord] = []
    for index in range(count):
        category = categories[index % len(categories)]
        audio_type = audio_types[index % len(audio_types)]
        records.append(
            PlanRecord(
                source_path=Path(f"C:/Synthetic/Packs/Pack_{index % 24:02d}/sample_{index:05d}.wav"),
                pack=f"Pack_{index % 24:02d}",
                category=category,
                subcategory="" if index % 3 else "Punchy",
                audio_type=audio_type,
                confidence="0.85",
                hash=f"hash-{index:05d}",
                tags=[f"tag-{index % 8}"],
                duration=0.25 + (index % 64) / 10.0,
            )
        )
    return records


def benchmark_tree_routing(records: list[PlanRecord], _count: int) -> int:
    profile = TreeOrganizationProfile(
        id="bench",
        name="Bench",
        root_node_id="root",
        created_at="now",
        updated_at="now",
        nodes=[
            TreeOrganizationNode("root", None, "Root", None, "system", 0),
            TreeOrganizationNode("loops", "root", "Loops", 'type:"Loops"', "system", 1),
            TreeOrganizationNode("oneshots", "root", "Oneshots", 'type:"Oneshots"', "system", 2),
            TreeOrganizationNode("kicks", "oneshots", "Kicks", 'cat:"Kicks"', "system", 1),
            TreeOrganizationNode("snares", "oneshots", "Snares", 'cat:"Snares"', "system", 2),
            TreeOrganizationNode("fallback", "root", "Other", None, "fallback", 99),
        ],
    )
    routed = TreeOrganizationResolver().routed_records(profile, records)
    return sum(len(items) for items in routed.values())


def benchmark_library_tree_payload(records: list[PlanRecord], _count: int) -> int:
    levels = [("audio_type", "type"), ("category", "category"), ("subcategory", "subcategory"), ("pack", "pack")]

    def group(group_records: list[PlanRecord], group_levels: list[tuple[str, str]]):
        if not group_levels:
            return group_records
        field, _node_type = group_levels[0]
        grouped: dict[str, list[PlanRecord]] = {}
        for record in group_records:
            grouped.setdefault(str(getattr(record, field, "") or "Other"), []).append(record)
        if len(group_levels) == 1:
            return grouped
        return {key: group(value, group_levels[1:]) for key, value in grouped.items()}

    nodes = build_normal_resolved_tree(records, levels, group)
    return _count_nodes(nodes)


def benchmark_fts_search(records: list[PlanRecord], _count: int) -> int:
    with tempfile.TemporaryDirectory() as tmp:
        db = UnshuffleDB(Path(tmp) / "bench.db")
        try:
            session_id = "bench"
            db.register_session(session_id, Path("C:/Synthetic"), Path("C:/Library"), "copy")
            db.add_staging_records_bulk(session_id, [_staging_tuple(index, record) for index, record in enumerate(records, start=1)])
            total = 0
            for query in ('category:"Kicks"', 'audio_type:"Loops"', "sample_00010", 'pack:"Pack_03"'):
                total += len(db.search_staging(session_id, query))
            return total
        finally:
            db.close()


def benchmark_staging_writes(records: list[PlanRecord], _count: int) -> int:
    with tempfile.TemporaryDirectory() as tmp:
        db = UnshuffleDB(Path(tmp) / "bench.db")
        try:
            session_id = "bench"
            db.register_session(session_id, Path("C:/Synthetic"), Path("C:/Library"), "copy")
            db.add_staging_records_bulk(session_id, [_staging_tuple(index, record) for index, record in enumerate(records, start=1)])
            return db.conn.execute("SELECT COUNT(*) FROM staging_records WHERE session_id = ?", (session_id,)).fetchone()[0]
        finally:
            db.close()


def benchmark_coherence_distances(records: list[PlanRecord], count: int) -> int:
    coherence_records = [
        CoherenceRecord(
            record_id=str(index),
            category=record.category,
            subcategory=record.subcategory or "",
            audio_type=record.audio_type,
            vector=[float((index + offset) % 17) / 17.0 for offset in range(FEATURE_VECTOR_SIZE)],
            classification_confidence=0.7,
        )
        for index, record in enumerate(records[: max(12, min(count, 240))])
    ]
    results, candidates = CoherenceEngine().audit(coherence_records)
    return len(results) + len(candidates)


def benchmark_startup_restore(records: list[PlanRecord], _count: int) -> int:
    payload = {
        "mode": "refresh",
        "target": "C:/Library",
        "roots": sorted({str(record.source_path.parent) for record in records[:48]}),
        "show_launcher_next_time": False,
    }
    restored = StartupLaunchRequest.from_settings(payload)
    return len(restored.roots) if restored is not None else 0


BENCHMARKS: dict[str, Benchmark] = {
    "tree_routing": benchmark_tree_routing,
    "library_tree_payload": benchmark_library_tree_payload,
    "fts_search": benchmark_fts_search,
    "staging_writes": benchmark_staging_writes,
    "coherence_distances": benchmark_coherence_distances,
    "startup_restore": benchmark_startup_restore,
}


def run_benchmarks(count: int, rounds: int) -> dict[str, dict[str, float | int]]:
    records = make_records(count)
    results: dict[str, dict[str, float | int]] = {}
    for name, benchmark in BENCHMARKS.items():
        timings: list[float] = []
        produced = 0
        for _round in range(rounds):
            started = time.perf_counter()
            produced = int(benchmark(records, count))
            timings.append(time.perf_counter() - started)
        results[name] = {
            "records": count,
            "rounds": rounds,
            "min_seconds": round(min(timings), 6),
            "median_seconds": round(statistics.median(timings), 6),
            "max_seconds": round(max(timings), 6),
            "result_size": produced,
        }
    return results


def _staging_tuple(index: int, record: PlanRecord) -> tuple:
    return (
        index,
        record.source_path.as_posix(),
        record.source_path.name,
        record.pack,
        record.category,
        record.subcategory,
        record.audio_type,
        json.dumps(record.tags),
        float(record.confidence),
        record.duration,
        record.hash,
        json.dumps(getattr(record, "pack_candidates", []) or []),
        None,
        None,
        0,
    )


def _count_nodes(nodes) -> int:
    return sum(1 + _count_nodes(node.children) for node in nodes)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run synthetic Unshuffle performance baselines.")
    parser.add_argument("--records", type=int, default=1500, help="Synthetic record count.")
    parser.add_argument("--rounds", type=int, default=3, help="Rounds per benchmark.")
    parser.add_argument("--quick", action="store_true", help="Use a small CI smoke-test workload.")
    args = parser.parse_args()

    count = 120 if args.quick else max(1, args.records)
    rounds = 1 if args.quick else max(1, args.rounds)
    print(json.dumps(run_benchmarks(count, rounds), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
