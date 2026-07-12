from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _worker(source: Path, workspace: Path) -> int:
    from unshuffle.logic.planning.service import run_plan
    from unshuffle.persistence import UnshuffleDB

    phase_started: dict[str, float] = {}
    phase_elapsed: dict[str, float] = {}
    current_phase: str | None = None

    def progress(payload: dict) -> None:
        nonlocal current_phase
        phase = str(payload.get("phase") or "").strip()
        if not phase or phase == current_phase:
            return
        now = time.perf_counter()
        if current_phase is not None:
            phase_elapsed[current_phase] = phase_elapsed.get(current_phase, 0.0) + (
                now - phase_started[current_phase]
            )
        current_phase = phase
        phase_started[phase] = now

    database = UnshuffleDB(workspace / "scan.db")
    started = time.perf_counter()
    try:
        records = run_plan(
            source,
            workspace / "target",
            session_id="benchmark",
            db=database,
            progress_callback=progress,
            collect_records=False,
        )
        from gui.utils.state import iter_scan_item_staging_rows, iter_staging_rows

        progress({"phase": "Finding Duplicates"})
        scan_stats = database.classified_scan_session_stats("benchmark")
        progress({"phase": "Creating Session"})
        database.register_session("benchmark", source, workspace / "target", "pending")
        inserted = database.add_staging_records_iter(
            "benchmark",
            iter_scan_item_staging_rows(database.iter_classified_scan_session_items("benchmark")),
        )
        if records:
            inserted += database.add_staging_records_iter(
                "benchmark",
                iter_staging_rows(records, start_index=inserted),
            )
    finally:
        database.close()
    finished = time.perf_counter()
    if current_phase is not None:
        phase_elapsed[current_phase] = phase_elapsed.get(current_phase, 0.0) + (
            finished - phase_started[current_phase]
        )
    print(
        json.dumps(
            {
                "elapsed_seconds": round(finished - started, 6),
                "records": int(scan_stats.get("total", 0)) + len(records),
                "staged_records": inserted,
                "phase_seconds": {key: round(value, 6) for key, value in phase_elapsed.items()},
            },
            sort_keys=True,
        )
    )
    return 0


def _synthetic_worker(item_count: int, workspace: Path) -> int:
    from gui.utils.state import iter_scan_item_staging_rows
    from unshuffle.persistence import UnshuffleDB

    database = UnshuffleDB(workspace / "scan.db")
    started = time.perf_counter()
    try:
        database.create_scan_run(
            scan_id="synthetic",
            session_id="benchmark",
            target_root=workspace / "target",
            roots=[workspace / "source"],
        )
        database.insert_scan_directories(
            "synthetic",
            [(0, 0, None, 0, workspace / "source", "source", False, False)],
        )

        def item_rows():
            for index in range(item_count):
                yield (
                    index,
                    index + 1,
                    0,
                    workspace / "source" / f"item-{index}.dat",
                    f"item-{index}.dat",
                    ".dat",
                    128,
                    1.0,
                    index + 1,
                    False,
                    False,
                    False,
                )

        database.insert_scan_items("synthetic", item_rows(), batch_size=5000)
        database.conn.execute(
            """
            UPDATE scan_items SET
                fast_hash = 'segmd5-v1:' || printf('%032x', item_id),
                effective_hash = 'segmd5-v1:' || printf('%032x', item_id),
                hash_state = 'done', analysis_state = 'done',
                classification_state = 'done', pack = 'Synthetic',
                category = 'Non-Audio Assets', subcategory = '',
                audio_type = 'Non-Audio Assets', confidence = '1.00',
                duration = 0.0, tags = '[]', pack_candidates = '[]',
                evidence_json = '{"synthetic":true}', analysis_status = 'ok',
                analysis_tags_json = '[]'
            WHERE scan_id = 'synthetic'
            """
        )
        database.conn.commit()
        database.register_session(
            "benchmark",
            workspace / "source",
            workspace / "target",
            "pending",
        )
        inserted = database.add_staging_records_iter(
            "benchmark",
            iter_scan_item_staging_rows(
                database.iter_classified_scan_session_items("benchmark", batch_size=2000)
            ),
            batch_size=2000,
        )
        database.update_session_scan_runs("benchmark", state="ready", phase="ready")
    finally:
        database.close()
    print(
        json.dumps(
            {
                "elapsed_seconds": round(time.perf_counter() - started, 6),
                "records": item_count,
                "staged_records": inserted,
            },
            sort_keys=True,
        )
    )
    return 0


def _rss_tree_bytes(pid: int) -> int:
    try:
        import psutil
    except ImportError:
        return 0
    try:
        process = psutil.Process(pid)
        children = process.children(recursive=True)
        return process.memory_info().rss + sum(child.memory_info().rss for child in children)
    except (psutil.Error, OSError):
        return 0


def _run_once(source: Path) -> dict:
    with tempfile.TemporaryDirectory(prefix="unshuffle-scan-benchmark-") as tmp:
        workspace = Path(tmp)
        env = os.environ.copy()
        env["APPDATA"] = str(workspace / "appdata")
        env["LOCALAPPDATA"] = str(workspace / "localappdata")
        command = [sys.executable, str(Path(__file__).resolve()), "--worker", str(source), str(workspace)]
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
        peak_rss = 0
        while process.poll() is None:
            peak_rss = max(peak_rss, _rss_tree_bytes(process.pid))
            time.sleep(0.05)
        stdout, stderr = process.communicate()
        peak_rss = max(peak_rss, _rss_tree_bytes(process.pid))
        if process.returncode:
            raise RuntimeError(stderr.strip() or stdout.strip() or f"benchmark exited {process.returncode}")
        lines = [line for line in stdout.splitlines() if line.strip().startswith("{")]
        if not lines:
            raise RuntimeError(f"benchmark produced no JSON result: {stdout}\n{stderr}")
        result = json.loads(lines[-1])
        result["peak_process_tree_mib"] = round(peak_rss / (1024 * 1024), 3) if peak_rss else None
        return result


def _run_synthetic_once(item_count: int) -> dict:
    with tempfile.TemporaryDirectory(prefix="unshuffle-scan-synthetic-") as tmp:
        workspace = Path(tmp)
        env = os.environ.copy()
        env["APPDATA"] = str(workspace / "appdata")
        env["LOCALAPPDATA"] = str(workspace / "localappdata")
        command = [
            sys.executable,
            str(Path(__file__).resolve()),
            "--synthetic-worker",
            str(item_count),
            str(workspace),
        ]
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
        peak_rss = 0
        while process.poll() is None:
            peak_rss = max(peak_rss, _rss_tree_bytes(process.pid))
            time.sleep(0.05)
        stdout, stderr = process.communicate()
        if process.returncode:
            raise RuntimeError(stderr.strip() or stdout.strip() or f"benchmark exited {process.returncode}")
        lines = [line for line in stdout.splitlines() if line.strip().startswith("{")]
        if not lines:
            raise RuntimeError(f"benchmark produced no JSON result: {stdout}\n{stderr}")
        result = json.loads(lines[-1])
        result["peak_process_tree_mib"] = round(peak_rss / (1024 * 1024), 3) if peak_rss else None
        return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark an isolated Unshuffle planning scan.")
    parser.add_argument("source", nargs="?", type=Path)
    parser.add_argument("--rounds", type=int, default=1)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--worker", nargs=2, metavar=("SOURCE", "WORKSPACE"))
    parser.add_argument("--synthetic-worker", nargs=2, metavar=("COUNT", "WORKSPACE"))
    parser.add_argument("--synthetic-items", type=int)
    args = parser.parse_args()
    if args.worker:
        return _worker(Path(args.worker[0]), Path(args.worker[1]))
    if args.synthetic_worker:
        return _synthetic_worker(int(args.synthetic_worker[0]), Path(args.synthetic_worker[1]))
    if args.synthetic_items is not None:
        results = [_run_synthetic_once(max(0, args.synthetic_items)) for _round in range(max(1, args.rounds))]
        payload = {"synthetic_items": args.synthetic_items, "rounds": results}
        rendered = json.dumps(payload, indent=2, sort_keys=True)
        print(rendered)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered + "\n", encoding="utf-8")
        return 0
    if args.source is None:
        parser.error("source is required")

    source = args.source.resolve()
    results = [_run_once(source) for _round in range(max(1, args.rounds))]
    payload = {"source": str(source), "rounds": results}
    rendered = json.dumps(payload, indent=2, sort_keys=True)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
