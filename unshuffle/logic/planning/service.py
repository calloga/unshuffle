import concurrent.futures
import hashlib
import json
import os
import sys
import threading
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from ...audio.acoustic import SimilarityEngine
from ...audio.metadata import get_audio_duration
from ...core.concurrency import bounded_map, max_scan_workers
from ...core.features import (
    CURRENT_EXTRACTOR_VERSION,
    CURRENT_FEATURE_SCHEMA,
    CURRENT_FEATURE_SPACE_VERSION,
    feature_blob_from_vector,
    vector_to_feature_values,
)
from ...core.constants import (
    AUDIO_EXTS,
    CONSISTENCY_MIN_FILES,
    CONSISTENCY_THRESHOLD,
    PACK_CONSISTENCY_BONUS,
    PACK_CONSISTENCY_THRESHOLD,
    get_runtime_config_snapshot,
)
from ...core.models import LibNode, NodeType, PlanRecord
from ...core.logging import logger
from ...core.progress import PhaseProgress
from ...core.tags import extract_tags_from_name
from ...logic.analysis import AnalysisContext, build_discovery_data, run_analysis
from ...logic.classification import classify_node, compute_component_score, detect_audio_type, get_subcategory, tokenize
from ...logic.planning.rules import is_generic_folder
from ...persistence import (
    get_directory_dump_filename,
    get_discovery_data_filename,
    save_discovery_data_iter,
    save_json_array_meta_iter,
    save_json_meta,
)

DEFAULT_EXTRACTOR_WORKERS = 4
MACOS_EXTRACTOR_WORKERS = 2
DEFAULT_EXTRACTOR_BATCH_SIZE = 512
CACHE_UPDATE_BATCH_SIZE = 256


def _extractor_worker_count(total: int) -> int:
    if total <= 0:
        return 1
    try:
        override = int(os.environ.get("UNSHUFFLE_EXTRACTOR_WORKERS", "0") or "0")
    except ValueError:
        override = 0
    if override > 0:
        return max(1, min(override, total))
    platform_cap = MACOS_EXTRACTOR_WORKERS if sys.platform == "darwin" else DEFAULT_EXTRACTOR_WORKERS
    return min(platform_cap, max_scan_workers(total, pool_cap=platform_cap))


def _extractor_process_limit() -> int:
    try:
        override = int(os.environ.get("UNSHUFFLE_EXTRACTOR_WORKERS", "0") or "0")
    except ValueError:
        override = 0
    if override > 0:
        return max(1, override)
    return MACOS_EXTRACTOR_WORKERS if sys.platform == "darwin" else DEFAULT_EXTRACTOR_WORKERS


def _extractor_batch_size(total: int) -> int:
    if total <= 0:
        return 1
    try:
        override = int(os.environ.get("UNSHUFFLE_EXTRACTOR_BATCH_SIZE", "0") or "0")
    except ValueError:
        override = 0
    if override > 0:
        return max(1, min(override, total))
    return min(DEFAULT_EXTRACTOR_BATCH_SIZE, total)


def _chunks(items: List[Path], size: int) -> Iterator[List[Path]]:
    for index in range(0, len(items), size):
        yield items[index : index + size]


def _ancestor_candidates(
    node: LibNode,
    source_root: Path,
    nodes: Dict[Path, LibNode],
    cache: Optional[Dict[Path, List[LibNode]]] = None,
) -> List[LibNode]:
    if cache is not None and node.path in cache:
        return cache[node.path]

    candidates = []
    current_path = node.path.parent
    while current_path != source_root and current_path in nodes:
        candidates.append(nodes[current_path])
        current_path = current_path.parent
    candidates.append(nodes[source_root])
    if cache is not None:
        cache[node.path] = candidates
    return candidates


def _determine_best_pack(
    node: LibNode,
    source_root: Path,
    nodes: Dict[Path, LibNode],
    candidate_cache: Optional[Dict[Path, List[LibNode]]] = None,
) -> Tuple[LibNode, List[Tuple[str, float]]]:
    """Helper to select the best parent folder as a pack name based on weights and generic malus."""
    candidates = _ancestor_candidates(node, source_root, nodes, cache=candidate_cache)

    adjusted_candidates = []
    for idx, candidate in enumerate(candidates):
        weight = candidate.pack_candidate_weight
        is_generic = is_generic_folder(candidate)
        parents_above = candidates[idx + 1 :]
        has_non_generic_parent = any(not is_generic_folder(parent) for parent in parents_above)
        if is_generic and has_non_generic_parent and not getattr(candidate, "is_child_of_duplicate", False):
            weight -= 0.4
        adjusted_candidates.append((candidate, weight))

    best_candidate = max(adjusted_candidates, key=lambda item: item[1])[0]
    pack_candidates = [
        (candidate.name, weight)
        for candidate, weight in sorted(adjusted_candidates, key=lambda item: item[1], reverse=True)
    ]
    return best_candidate, pack_candidates


def _is_audio_file_node(node: LibNode) -> bool:
    return (
        node.node_type == NodeType.FILE
        and not node.name.startswith("._")
        and bool(node.extension)
        and node.extension.lower() in AUDIO_EXTS
    )


def _non_audio_asset_pack_name(node: LibNode, source_root: Path, fallback: str) -> str:
    try:
        parts = node.path.relative_to(source_root).parts
    except ValueError:
        return fallback
    for index, part in enumerate(parts[:-1]):
        if part.casefold() == "non-audio assets":
            if index + 1 < len(parts) - 1:
                return parts[index + 1]
            return fallback
    return fallback


def _duration_from_vector(vector: Optional[List[float]]) -> Optional[float]:
    if vector and len(vector) > SimilarityEngine.IDX_ACTIVE_DURATION:
        duration = vector[SimilarityEngine.IDX_ACTIVE_DURATION]
        if duration > 0:
            return duration
    return None


def _scan_item_node(row: dict) -> LibNode:
    return LibNode(
        path=Path(row["normalized_path"]),
        name=str(row.get("sample_name") or Path(row["normalized_path"]).name),
        node_type=NodeType.FILE,
        extension=str(row.get("extension") or ""),
        hash=row.get("effective_hash") or None,
        fast_hash=row.get("fast_hash") or None,
    )


def _iter_scan_nodes(db, scan_id: str, *, batch_size: int = 1000):
    for batch in db.iter_scan_items(
        scan_id,
        columns="item_id, normalized_path, sample_name, extension, size, mtime, mtime_ns, "
                "effective_hash, fast_hash, is_supported_audio, analysis_status, analysis_tags_json",
        batch_size=batch_size,
    ):
        for row in batch:
            yield row, _scan_item_node(row)


def _save_db_scan_artifacts(context, source_root: Path, target_dir: Path, session_id: str, is_dry_run: bool) -> None:
    def dump_rows():
        for batch in context.db.iter_discovered_scan_nodes(context.scan_id, batch_size=1000):
            for row in batch:
                path = Path(row["normalized_path"])
                if row["node_type"] == "file":
                    node_type = "FILE"
                    file_hash = row.get("effective_hash")
                else:
                    node = context.nodes.get(path)
                    node_type = node.node_type.name if node is not None else "CONTAINER"
                    file_hash = None
                yield {"path": path.as_posix(), "node": row.get("name") or path.name, "type": node_type, "hash": file_hash}

    def discovery_rows():
        for batch in context.db.iter_scan_items(
            context.scan_id,
            columns="normalized_path, sample_name",
            batch_size=1000,
        ):
            for row in batch:
                yield {
                    "path": Path(row["normalized_path"]).as_posix(),
                    "name": row["sample_name"],
                    "tokens": sorted(tokenize(str(row["sample_name"] or ""))),
                }

    save_json_array_meta_iter(
        target_dir,
        get_directory_dump_filename(session_id, source_root),
        dump_rows(),
        is_dry_run=is_dry_run,
    )
    save_discovery_data_iter(
        target_dir,
        get_discovery_data_filename(source_root),
        source_root,
        discovery_rows(),
        is_dry_run=is_dry_run,
    )


def _analyze_db_audio(
    context,
    *,
    skip_expensive_hashes: set[str],
    progress_callback,
    is_interrupted,
) -> None:
    db = context.db
    scan_id = context.scan_id
    sim_engine = SimilarityEngine()
    candidates = db.count_scan_items(scan_id) if hasattr(db, "count_scan_items") else 0
    progress = PhaseProgress(
        progress_callback,
        "Analyzing Audio Features",
        total=max(1, candidates),
        message="Analyzing audio features...",
        update_every=1,
        min_interval_seconds=0.5,
    )
    progress.emit(0, force=True)
    completed = 0
    completed_lock = threading.Lock()

    def report_complete(_path: Path) -> None:
        nonlocal completed
        with completed_lock:
            completed += 1
            progress.emit(completed)

    sim_engine.configure_extraction_runtime(
        max_processes=_extractor_process_limit(),
        is_interrupted=is_interrupted,
        completion_callback=report_complete,
    )
    supported = SimilarityEngine.SUPPORTED_EXTS
    for batch in db.iter_canonical_scan_audio_items(scan_id, batch_size=2000):
        if is_interrupted and is_interrupted():
            return
        eligible = [
            row for row in batch
            if str(row.get("extension") or "").lower() in supported
            and (not row.get("effective_hash") or row.get("effective_hash") not in skip_expensive_hashes)
        ]
        hashes = [str(row["effective_hash"]) for row in eligible if row.get("effective_hash")]
        cached_vectors = db.get_feature_vectors_bulk(hashes) if hasattr(db, "get_feature_vectors_bulk") else {}
        cached_failures = db.get_analysis_failures_bulk(hashes) if hasattr(db, "get_analysis_failures_bulk") else {}
        unresolved = []
        analysis_updates = []
        for row in eligible:
            file_hash = str(row.get("effective_hash") or "")
            if file_hash and cached_failures.get(file_hash):
                tag = cached_failures[file_hash]
                analysis_updates.append((file_hash, "done", tag, tag, None))
                report_complete(Path(row["normalized_path"]))
            elif file_hash and SimilarityEngine.vector_from_blob(cached_vectors.get(file_hash)):
                analysis_updates.append((file_hash, "done", None, None, None))
                report_complete(Path(row["normalized_path"]))
            else:
                unresolved.append(row)
        if analysis_updates:
            db.update_scan_analysis_by_hash(scan_id, analysis_updates)

        extract_rows = {Path(row["normalized_path"]): row for row in unresolved}
        paths = list(extract_rows)
        if paths:
            batch_size = _extractor_batch_size(len(paths))
            chunks = _chunks(paths, batch_size)
            batch_count = (len(paths) + batch_size - 1) // batch_size
            workers = _extractor_worker_count(batch_count)
            cache_updates = []
            analysis_updates = []
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
                for _chunk, payloads in bounded_map(
                    executor,
                    sim_engine.extract_feature_payloads_bulk,
                    chunks,
                    max_pending=workers * 2,
                    is_interrupted=is_interrupted,
                ):
                    for path, payload in payloads.items():
                        row = extract_rows[path]
                        file_hash = str(row.get("effective_hash") or "")
                        if payload:
                            blob = feature_blob_from_vector(payload.vector)
                            if blob and file_hash:
                                cache_updates.append((
                                    file_hash,
                                    path,
                                    int(row.get("size") or 0),
                                    float(row.get("mtime") or 0.0),
                                    blob,
                                    payload.feature_space_version or CURRENT_FEATURE_SPACE_VERSION,
                                    payload.extractor_version or CURRENT_EXTRACTOR_VERSION,
                                    json.dumps(list(payload.feature_schema or CURRENT_FEATURE_SCHEMA)),
                                    payload.analysis_status or "ok",
                                    "[]",
                                    row.get("fast_hash"),
                                ))
                                analysis_updates.append((file_hash, "done", None, payload.analysis_status or "ok", None))
                        else:
                            tag = sim_engine.extraction_failure_tag(path)
                            if file_hash:
                                analysis_updates.append((
                                    file_hash,
                                    "failed",
                                    tag or "unknown",
                                    tag or "analysis_unavailable",
                                    sim_engine.extraction_failure_message(path),
                                ))
                            if tag in {"Empty", "Silent"} and file_hash:
                                cache_updates.append((
                                    file_hash,
                                    path,
                                    int(row.get("size") or 0),
                                    float(row.get("mtime") or 0.0),
                                    None,
                                    CURRENT_FEATURE_SPACE_VERSION,
                                    CURRENT_EXTRACTOR_VERSION,
                                    json.dumps(list(CURRENT_FEATURE_SCHEMA)),
                                    tag,
                                    json.dumps([tag]),
                                    row.get("fast_hash"),
                                ))
                        if len(cache_updates) >= CACHE_UPDATE_BATCH_SIZE:
                            db.update_cache_bulk(cache_updates)
                            cache_updates.clear()
            if cache_updates:
                db.update_cache_bulk(cache_updates)
            if analysis_updates:
                db.update_scan_analysis_by_hash(scan_id, analysis_updates)
    progress.emit(max(1, candidates), force=True)


def _run_db_backed_plan(
    context,
    source_root: Path,
    target_dir: Path,
    *,
    session_id: str,
    is_dry_run: bool,
    token_adjustments,
    skip_expensive_hashes: set[str],
    min_confidence,
    progress_callback,
    is_interrupted,
) -> List[PlanRecord]:
    db = context.db
    scan_id = context.scan_id
    runtime_config = get_runtime_config_snapshot()
    global_boosts = context.frequency_analyzer.boosts
    _save_db_scan_artifacts(context, source_root, target_dir, session_id, is_dry_run)

    folder_category_counts: Dict[Path, Counter] = {}
    folder_pack_counts: Dict[Path, int] = {}
    folder_total_files: Dict[Path, int] = {}
    for _row, node in _iter_scan_nodes(db, scan_id):
        scores = compute_component_score(node.name, runtime=runtime_config)
        if scores:
            top_cat = max(scores.items(), key=lambda item: item[1])[0]
            current = node.path.parent
            while current in context.nodes:
                folder_category_counts.setdefault(current, Counter())[top_cat] += 1
                if current == source_root:
                    break
                current = current.parent
        best_pack, _ = _determine_best_pack(node, source_root, context.nodes)
        current = node.path.parent
        while current in context.nodes:
            folder_total_files[current] = folder_total_files.get(current, 0) + 1
            if best_pack.path == current:
                folder_pack_counts[current] = folder_pack_counts.get(current, 0) + 1
            if current == source_root:
                break
            current = current.parent

    consistency_boosts: Dict[Path, str] = {}
    for path, counts in folder_category_counts.items():
        total = sum(counts.values())
        if total > CONSISTENCY_MIN_FILES:
            top_cat, top_count = counts.most_common(1)[0]
            if top_count / total >= CONSISTENCY_THRESHOLD:
                consistency_boosts[path] = top_cat
    pack_consistency_boosts = {
        path
        for path, total in folder_total_files.items()
        if total >= CONSISTENCY_MIN_FILES
        and folder_pack_counts.get(path, 0) / total >= PACK_CONSISTENCY_THRESHOLD
    }

    _analyze_db_audio(
        context,
        skip_expensive_hashes=skip_expensive_hashes,
        progress_callback=progress_callback,
        is_interrupted=is_interrupted,
    )

    records: List[PlanRecord] = []
    for node in context.nodes.values():
        if node.is_preserved:
            records.append(PlanRecord(
                source_path=node.path,
                pack=node.name,
                category="Preserved",
                subcategory="",
                audio_type="Utility",
                confidence="1.00",
                evidence={"preserved": True},
                is_preserved=True,
                preserved_root=node.preserved_root,
                hash=node.hash,
                fast_hash=node.fast_hash,
            ))

    total_items = db.count_scan_items(scan_id)
    classification_progress = PhaseProgress(
        progress_callback,
        "Classifying Samples",
        total=total_items,
        message="Classifying samples...",
        update_every=5,
    )
    classification_progress.emit(0, force=True)
    classified = 0
    classification_updates: List[dict] = []
    skipped_updates = []
    for batch in db.iter_scan_classification_items(scan_id, batch_size=500):
        if is_interrupted and is_interrupted():
            break
        durations: dict[Path, Optional[float]] = {}
        vectors: dict[Path, tuple[bytes | None, Optional[List[float]]]] = {}
        duration_paths = []
        for row in batch:
            path = Path(row["normalized_path"])
            blob = row.get("feature_vector")
            vector = SimilarityEngine.vector_from_blob(blob)
            vectors[path] = (blob, vector)
            vector_duration = _duration_from_vector(vector)
            if vector_duration is not None:
                durations[path] = vector_duration
            elif (
                str(row.get("extension") or "").lower() in AUDIO_EXTS
                and (not row.get("effective_hash") or row.get("effective_hash") not in skip_expensive_hashes)
            ):
                duration_paths.append(path)
        if duration_paths:
            workers = max_scan_workers(len(duration_paths))
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
                for path, duration in bounded_map(
                    executor,
                    get_audio_duration,
                    duration_paths,
                    max_pending=workers * 2,
                    is_interrupted=is_interrupted,
                ):
                    durations[path] = duration

        for row in batch:
            path = Path(row["normalized_path"])
            node = _scan_item_node(row)
            duration = durations.get(path)
            feature_blob, vector = vectors[path]
            feature_values_for_path = vector_to_feature_values(vector) if vector else None
            failure_tag = row.get("analysis_error_code") or None
            analysis_status = row.get("analysis_status") or None
            best_candidate, pack_candidates = _determine_best_pack(node, source_root, context.nodes)
            base_candidates = _ancestor_candidates(node, source_root, context.nodes)
            if best_candidate.path not in pack_consistency_boosts:
                candidates_with_boost = []
                for candidate_index, candidate in enumerate(base_candidates):
                    weight = candidate.pack_candidate_weight
                    parents_above = base_candidates[candidate_index + 1:]
                    if (
                        is_generic_folder(candidate)
                        and any(not is_generic_folder(parent) for parent in parents_above)
                        and not candidate.is_child_of_duplicate
                    ):
                        weight -= 0.4
                    if candidate.path in pack_consistency_boosts:
                        weight += PACK_CONSISTENCY_BONUS
                    candidates_with_boost.append((candidate, weight))
                best_candidate = max(candidates_with_boost, key=lambda item: item[1])[0]
                pack_candidates = [
                    (candidate.name, weight)
                    for candidate, weight in sorted(candidates_with_boost, key=lambda item: item[1], reverse=True)
                ]
            pack_name = best_candidate.name
            initial_audio_type = detect_audio_type(node, runtime=runtime_config)
            if initial_audio_type == "Metadata":
                skipped_updates.append((int(row["item_id"]), {"classification_state": "excluded"}))
                continue
            if initial_audio_type == "Non-Audio Assets":
                pack_name = _non_audio_asset_pack_name(node, source_root, pack_name)
                category, confidence, evidence = "Non-Audio Assets", 1.0, {"non_audio_asset": True}
                audio_type, subcategory = initial_audio_type, ""
            else:
                category, confidence, evidence = classify_node(
                    node,
                    pack_name=pack_name,
                    global_boosts=global_boosts,
                    token_adjustments=token_adjustments,
                    duration=duration,
                    features=feature_values_for_path,
                    min_confidence=min_confidence,
                    runtime=runtime_config,
                    runtime_revision_trusted=True,
                )
                audio_type = detect_audio_type(
                    node,
                    duration=duration,
                    runtime=runtime_config,
                    features=feature_values_for_path,
                )
                subcategory = get_subcategory(category, tokenize(node.name), runtime=runtime_config)
            if audio_type == "Metadata":
                skipped_updates.append((int(row["item_id"]), {"classification_state": "excluded"}))
                continue
            tags = extract_tags_from_name(node.name)
            if failure_tag:
                tags = [*tags, str(failure_tag)]
            record = PlanRecord(
                source_path=path,
                pack=pack_name,
                category=category,
                subcategory=subcategory,
                audio_type=audio_type,
                confidence=f"{confidence:.2f}",
                evidence=evidence,
                pack_candidates=pack_candidates,
                hash=node.hash,
                fast_hash=node.fast_hash,
                tags=tags,
                duration=duration or 0.0,
                feature_vector=feature_blob,
                feature_space_version=row.get("feature_space_version") if feature_blob is not None else None,
                feature_schema_json=row.get("feature_schema_json") if feature_blob is not None else None,
                analysis_status=analysis_status,
                analysis_tags_json=row.get("cached_analysis_tags_json") or json.dumps([failure_tag]) if failure_tag else "[]",
            )
            classification_updates.append({
                "source_path": path,
                "pack": record.pack,
                "category": record.category,
                "subcategory": record.subcategory,
                "audio_type": record.audio_type,
                "confidence": record.confidence,
                "duration": record.duration,
                "tags": json.dumps(record.tags),
                "pack_candidates": json.dumps(record.pack_candidates),
                "evidence_json": json.dumps(record.evidence),
                "analysis_status": record.analysis_status,
                "analysis_tags_json": record.analysis_tags_json,
                "classification_state": "done",
            })
            classified += 1
            classification_progress.emit(classified)
        if classification_updates:
            db.update_scan_item_classifications_by_path(scan_id, classification_updates)
            classification_updates.clear()
        if skipped_updates:
            db.update_scan_items(scan_id, skipped_updates)
            skipped_updates.clear()
    classification_progress.emit(total_items, force=True)
    db.update_scan_run(
        scan_id,
        state="paused" if is_interrupted and is_interrupted() else "running",
        phase="staging",
        completed_count=classified,
    )
    return records


def run_plan(
    source_root: Path,
    target_dir: Path,
    is_dry_run: bool = False,
    session_id: str = "",
    progress_callback=None,
    token_adjustments: Optional[Dict[str, Dict[str, float]]] = None,
    db=None,
    acoustic_index: bool = False,
    is_interrupted: Any = None,
    skip_expensive_hashes: Optional[Set[str]] = None,
    min_confidence: Optional[float] = None,
    collect_records: bool = True,
) -> List[PlanRecord]:
    """Coordinates the multi-pass planning algorithm."""
    if progress_callback:
        progress_callback({"phase": "Discovering Samples", "message": f"Discovering samples in {source_root.name}..."})
    scan_id = None
    if db is not None and hasattr(db, "create_scan_run"):
        source_key = hashlib.sha1(str(Path(source_root).resolve()).encode("utf-8")).hexdigest()[:12]
        scan_id = f"{session_id}:{source_key}"
        db.create_scan_run(
            scan_id=scan_id,
            session_id=session_id,
            target_root=target_dir,
            roots=[source_root],
            versions={
                "hash": "segmd5-v1",
                "feature": CURRENT_FEATURE_SPACE_VERSION,
                "taxonomy": "current",
                "classification": "current",
            },
        )
    context = run_analysis(
        source_root,
        progress_callback=progress_callback,
        db=db,
        target_dir=target_dir,
        scan_id=scan_id,
        is_interrupted=is_interrupted,
        lean_db_items=bool(scan_id and not collect_records),
    )
    if is_interrupted:
        context.is_interrupted = is_interrupted
    if is_interrupted and is_interrupted():
        if scan_id and db is not None and hasattr(db, "update_scan_run"):
            db.update_scan_run(scan_id, state="paused")
        return []
    if context.lean_db_items and scan_id and db is not None:
        from ..analysis.scan_hashing import (
            promote_scan_against_staging,
            promote_session_fast_hash_collisions,
        )

        promote_scan_against_staging(
            db,
            session_id,
            scan_id,
            is_interrupted=is_interrupted,
            progress_callback=progress_callback,
        )
        promote_session_fast_hash_collisions(
            db,
            session_id,
            is_interrupted=is_interrupted,
            progress_callback=progress_callback,
        )
        if is_interrupted and is_interrupted():
            db.update_scan_run(scan_id, state="paused")
            return []

    if progress_callback:
        progress_callback({"phase": "Discovering Samples", "message": "Preparing sample groups..."})
    context.frequency_analyzer.finalize()
    global_boosts = context.frequency_analyzer.boosts
    runtime_config = get_runtime_config_snapshot()
    logger.info("Global Frequency Boosts: %s", global_boosts)

    skip_expensive_hashes = set(skip_expensive_hashes or ())
    if context.lean_db_items:
        return _run_db_backed_plan(
            context,
            source_root,
            target_dir,
            session_id=session_id,
            is_dry_run=is_dry_run,
            token_adjustments=token_adjustments,
            skip_expensive_hashes=skip_expensive_hashes,
            min_confidence=min_confidence,
            progress_callback=progress_callback,
            is_interrupted=is_interrupted,
        )

    dump_data = [
        {"path": node.path.as_posix(), "node": node.name, "type": node.node_type.name, "hash": node.hash}
        for node in context.nodes.values()
    ]
    dump_filename = get_directory_dump_filename(session_id, source_root)
    save_json_meta(target_dir, dump_filename, dump_data, is_dry_run=is_dry_run)
    del dump_data
    discovery_data = build_discovery_data(context)
    save_json_meta(target_dir, get_discovery_data_filename(source_root), discovery_data, is_dry_run=is_dry_run)
    del discovery_data

    folder_category_counts: Dict[Path, Counter] = {}
    folder_pack_counts: Dict[Path, int] = {}
    folder_total_files: Dict[Path, int] = {}
    pack_candidate_cache: Dict[Path, List[LibNode]] = {}

    for path, node in context.nodes.items():
        if node.node_type == NodeType.FILE:
            scores = compute_component_score(node.name, runtime=runtime_config)
            if scores:
                top_cat = max(scores.items(), key=lambda item: item[1])[0]
                current = path.parent
                while current in context.nodes:
                    if current not in folder_category_counts:
                        folder_category_counts[current] = Counter()
                    folder_category_counts[current][top_cat] += 1
                    if current == source_root:
                        break
                    current = current.parent

            best_pack, _ = _determine_best_pack(node, source_root, context.nodes)
            current = path.parent
            while current in context.nodes:
                folder_total_files[current] = folder_total_files.get(current, 0) + 1
                if best_pack.path == current:
                    folder_pack_counts[current] = folder_pack_counts.get(current, 0) + 1
                if current == source_root:
                    break
                current = current.parent

    consistency_boosts: Dict[Path, str] = {}
    for path, counts in folder_category_counts.items():
        total = sum(counts.values())
        if total > CONSISTENCY_MIN_FILES:
            top_cat, top_count = counts.most_common(1)[0]
            if (top_count / total) >= CONSISTENCY_THRESHOLD:
                consistency_boosts[path] = top_cat

    pack_consistency_boosts: Set[Path] = set()
    for path, total in folder_total_files.items():
        if total >= CONSISTENCY_MIN_FILES:
            loyal = folder_pack_counts.get(path, 0)
            if (loyal / total) >= PACK_CONSISTENCY_THRESHOLD:
                pack_consistency_boosts.add(path)
                logger.info("Pack Loyalty Boost enabled for folder: %s (%s/%s)", path.name, loyal, total)

    records: List[PlanRecord] = []
    classification_updates: List[dict] = []
    logger.info("Phase 3: Final Weighted Categorization...")

    process_nodes = [node for node in context.nodes.values() if node.node_type == NodeType.FILE or node.is_preserved]
    file_nodes = [node for node in process_nodes if node.node_type == NodeType.FILE]

    expensive_file_nodes = [node for node in file_nodes if not node.hash or node.hash not in skip_expensive_hashes]
    expensive_audio_nodes = [node for node in expensive_file_nodes if _is_audio_file_node(node)]

    durations: Dict[Path, Optional[float]] = {}
    feature_vectors: Dict[Path, bytes] = {}
    feature_values: Dict[Path, Dict[str, float]] = {}
    analysis_failure_tags: Dict[Path, str] = {}
    analysis_statuses: Dict[Path, str] = {}
    cache_updates: List[tuple] = []
    if expensive_audio_nodes:
        if progress_callback:
            progress_callback({
                "phase": "Analyzing Audio Features",
                "message": f"Checking audio feature cache for {len(expensive_audio_nodes)} files...",
            })
        sim_engine = SimilarityEngine()

        to_extract: list[Path] = []
        extract_dependents: Dict[Path, list[Path]] = {}
        queued_extract_keys: Dict[tuple[str, str], Path] = {}
        supported = SimilarityEngine.SUPPORTED_EXTS
        cached_feature_vectors: Dict[str, bytes] = {}
        cached_analysis_failures: Dict[str, str] = {}
        if db and hasattr(db, "get_feature_vectors_bulk"):
            cached_feature_vectors = db.get_feature_vectors_bulk(
                [node.hash for node in expensive_audio_nodes if node.hash]
            )
        if db and hasattr(db, "get_analysis_failures_bulk"):
            cached_analysis_failures = db.get_analysis_failures_bulk(
                [node.hash for node in expensive_audio_nodes if node.hash]
            )

        for node in expensive_audio_nodes:
            if not node.extension or node.extension.lower() not in supported:
                continue

            if db:
                cached_failure = cached_analysis_failures.get(node.hash) if node.hash else None
                if cached_failure:
                    analysis_failure_tags[node.path] = cached_failure
                    analysis_statuses[node.path] = cached_failure
                    continue
                cached = cached_feature_vectors.get(node.hash) if node.hash else None
                if cached is None and not hasattr(db, "get_feature_vectors_bulk"):
                    cached = db.get_feature_vector(node.hash)
                cached_vector = SimilarityEngine.vector_from_blob(cached)
                if cached and cached_vector:
                    feature_vectors[node.path] = cached
                    feature_values[node.path] = vector_to_feature_values(cached_vector)
                    vector_duration = _duration_from_vector(cached_vector)
                    if vector_duration is not None:
                        durations[node.path] = vector_duration
                    continue

            extract_key = (str(node.hash or node.path), node.extension.lower())
            representative = queued_extract_keys.get(extract_key)
            if representative is not None:
                extract_dependents.setdefault(representative, []).append(node.path)
                continue
            queued_extract_keys[extract_key] = node.path
            to_extract.append(node.path)

        if to_extract:
            logger.info(
                "Audio feature analysis extracting %s/%s supported audio files; %s reused from cache; %s duplicate extraction(s) skipped",
                len(to_extract),
                len(expensive_audio_nodes),
                len(feature_vectors),
                sum(len(paths) for paths in extract_dependents.values()),
            )
            batch_size = _extractor_batch_size(len(to_extract))
            batch_count = (len(to_extract) + batch_size - 1) // batch_size
            batches = _chunks(to_extract, batch_size)
            max_workers = _extractor_worker_count(batch_count)
            max_pending = max_workers * 2
            logger.info("Audio feature analysis")
            feature_progress = PhaseProgress(
                progress_callback,
                "Analyzing Audio Features",
                total=len(to_extract),
                message=f"Analyzing audio features for {len(to_extract)} files.",
                update_every=1,
                min_interval_seconds=0.5,
            )
            feature_progress.emit(0, force=True)
            completed_lock = threading.Lock()
            completed_count = 0

            def report_feature_complete(_path: Path) -> None:
                nonlocal completed_count
                with completed_lock:
                    completed_count += 1
                    feature_progress.emit(completed_count)

            sim_engine.configure_extraction_runtime(
                max_processes=_extractor_process_limit(),
                is_interrupted=is_interrupted,
                completion_callback=report_feature_complete,
            )
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                for _batch, payloads in bounded_map(
                    executor,
                    sim_engine.extract_feature_payloads_bulk,
                    batches,
                    max_pending=max_pending,
                    is_interrupted=is_interrupted,
                ):
                    for path, payload in payloads.items():
                        dependent_paths = [path, *extract_dependents.get(path, [])]
                        if payload:
                            vector = payload.vector
                            blob = feature_blob_from_vector(vector)
                            if not blob:
                                continue
                            values = vector_to_feature_values(vector)
                            for result_path in dependent_paths:
                                feature_vectors[result_path] = blob
                                feature_values[result_path] = values
                                analysis_statuses[result_path] = payload.analysis_status
                                vector_duration = _duration_from_vector(vector)
                                if vector_duration is not None:
                                    durations[result_path] = vector_duration
                            vector_duration = _duration_from_vector(vector)
                            if db:
                                node = context.nodes.get(path)
                                if node:
                                    stat = path.stat()
                                    cache_updates.append((
                                        node.hash,
                                        path,
                                        stat.st_size,
                                        stat.st_mtime,
                                        blob,
                                        payload.feature_space_version or CURRENT_FEATURE_SPACE_VERSION,
                                        payload.extractor_version or CURRENT_EXTRACTOR_VERSION,
                                        json.dumps(list(payload.feature_schema or CURRENT_FEATURE_SCHEMA)),
                                        payload.analysis_status or "ok",
                                        "[]",
                                        node.fast_hash,
                                    ))
                                    if len(cache_updates) >= CACHE_UPDATE_BATCH_SIZE:
                                        db.update_cache_bulk(cache_updates)
                                        cache_updates.clear()
                        else:
                            failure_tag = sim_engine.extraction_failure_tag(path)
                            if failure_tag:
                                for result_path in dependent_paths:
                                    analysis_failure_tags[result_path] = failure_tag
                                    analysis_statuses[result_path] = failure_tag
                                if db and failure_tag in {"Empty", "Silent"}:
                                    node = context.nodes.get(path)
                                    if node and node.hash:
                                        stat = path.stat()
                                        cache_updates.append((
                                            node.hash,
                                            path,
                                            stat.st_size,
                                            stat.st_mtime,
                                            None,
                                            CURRENT_FEATURE_SPACE_VERSION,
                                            CURRENT_EXTRACTOR_VERSION,
                                            json.dumps(list(CURRENT_FEATURE_SCHEMA)),
                                            failure_tag,
                                            json.dumps([failure_tag]),
                                            node.fast_hash,
                                        ))
                                        if len(cache_updates) >= CACHE_UPDATE_BATCH_SIZE:
                                            db.update_cache_bulk(cache_updates)
                                            cache_updates.clear()
                feature_progress.emit(len(to_extract), force=True)
        if db and cache_updates:
            db.update_cache_bulk(cache_updates)

    duration_nodes = [node for node in expensive_audio_nodes if node.path not in durations]
    if duration_nodes:
        duration_progress = PhaseProgress(
            progress_callback,
            "Analyzing Audio Features",
            total=len(duration_nodes),
            message=f"Detecting durations for {len(duration_nodes)} files.",
            update_every=100,
        )
        duration_progress.emit(0, force=True)
        max_workers = max_scan_workers(len(duration_nodes))
        max_pending = max_workers * 2
        paths = [node.path for node in duration_nodes]
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            for index, (path, duration) in enumerate(bounded_map(
                executor,
                get_audio_duration,
                paths,
                max_pending=max_pending,
                is_interrupted=is_interrupted,
            ), 1):
                durations[path] = duration
                duration_progress.emit(index)
        duration_progress.emit(len(duration_nodes), force=True)

    total_items = len(process_nodes)
    classification_progress = PhaseProgress(
        progress_callback,
        "Classifying Samples",
        total=total_items,
        message="Classifying samples...",
        update_every=5,
    )
    classification_progress.emit(0, force=True)

    for index, node in enumerate(process_nodes, start=1):
        if is_interrupted and is_interrupted():
            break
        path = node.path
        duration = durations.pop(path, None)
        feature_values_for_path = feature_values.pop(path, None)
        feature_vector = feature_vectors.pop(path, None)
        failure_tag = analysis_failure_tags.pop(path, None)
        analysis_status = analysis_statuses.pop(path, None)

        if node.is_preserved:
            records.append(
                PlanRecord(
                    source_path=node.path,
                    pack=node.name,
                    category="Preserved",
                    subcategory="",
                    audio_type="Utility",
                    confidence="1.00",
                    evidence={"preserved": True},
                    is_preserved=True,
                    preserved_root=node.preserved_root,
                    hash=node.hash,
                    fast_hash=node.fast_hash,
                )
            )
            continue

        best_candidate, pack_candidates = _determine_best_pack(
            node,
            source_root,
            context.nodes,
            candidate_cache=pack_candidate_cache,
        )
        base_candidates = _ancestor_candidates(
            node,
            source_root,
            context.nodes,
            cache=pack_candidate_cache,
        )
        if best_candidate.path not in pack_consistency_boosts:
            candidates_with_boost = []


            for idx, candidate in enumerate(base_candidates):
                weight = candidate.pack_candidate_weight
                is_generic = is_generic_folder(candidate)
                parents_above = base_candidates[idx + 1 :]
                has_non_generic_parent = any(not is_generic_folder(parent) for parent in parents_above)
                if is_generic and has_non_generic_parent and not getattr(candidate, "is_child_of_duplicate", False):
                    weight -= 0.4
                if candidate.path in pack_consistency_boosts:
                    weight += PACK_CONSISTENCY_BONUS
                candidates_with_boost.append((candidate, weight))

            best_candidate = max(candidates_with_boost, key=lambda item: item[1])[0]
            pack_name = best_candidate.name
            pack_candidates = [
                (candidate.name, weight)
                for candidate, weight in sorted(candidates_with_boost, key=lambda item: item[1], reverse=True)
            ]
        else:
            pack_name = best_candidate.name

        debug_this = False

        if debug_this:
            print(f"--- Weight Debug for {path.name} ---")
            for candidate in base_candidates:
                evidence_str = ", ".join([f"{key}: {value:+}" for key, value in candidate.weight_evidence.items()])
                print(f"  > {candidate.name:<35} | W: {candidate.pack_candidate_weight:<5} | {evidence_str}")
            print("--- Pass 2: Scoring Engine ---")

        initial_audio_type = detect_audio_type(node, runtime=runtime_config)
        if initial_audio_type == "Metadata":
            pack_candidate_cache.pop(path, None)
            continue

        if initial_audio_type == "Non-Audio Assets":
            pack_name = _non_audio_asset_pack_name(node, source_root, pack_name)
            cat = "Non-Audio Assets"
            conf = 1.0
            evidence = {"non_audio_asset": True}
            audio_type = initial_audio_type
            subcategory = ""
        else:
            cat, conf, evidence = classify_node(
                node,
                pack_name=pack_name,
                global_boosts=global_boosts,
                token_adjustments=token_adjustments,
                duration=duration,
                features=feature_values_for_path,
                min_confidence=min_confidence,
                debug=debug_this,
                runtime=runtime_config,
                runtime_revision_trusted=True,
            )

            if debug_this:
                print(f"  [RESULT] Category: {cat} ({conf})")
                print("-" * 40)

            audio_type = detect_audio_type(node, duration=duration, runtime=runtime_config, features=feature_values_for_path)
            tokens = tokenize(node.name)
            subcategory = get_subcategory(cat, tokens, runtime=runtime_config)

        if audio_type == "Metadata":
            pack_candidate_cache.pop(path, None)
            continue

        classification_progress.emit(index)

        tags = extract_tags_from_name(node.name)
        if failure_tag:
            tags = [*tags, failure_tag]

        record = PlanRecord(
                source_path=node.path,
                pack=pack_name,
                category=cat,
                subcategory=subcategory,
                audio_type=audio_type,
                confidence=f"{conf:.2f}",
                evidence=evidence,
                is_preserved=False,
                pack_candidates=pack_candidates,
                hash=node.hash,
                fast_hash=node.fast_hash,
                tags=tags,
                duration=duration or 0.0,
                feature_vector=feature_vector,
                feature_space_version=CURRENT_FEATURE_SPACE_VERSION if feature_vector is not None else None,
                feature_schema_json=json.dumps(list(CURRENT_FEATURE_SCHEMA)) if feature_vector is not None else None,
                analysis_status=analysis_status,
                analysis_tags_json=json.dumps([failure_tag]) if failure_tag else "[]",
            )
        if collect_records:
            records.append(record)
        if scan_id and db and hasattr(db, "update_scan_item_classifications_by_path"):
            classification_updates.append({
                "source_path": record.source_path,
                "pack": record.pack,
                "category": record.category,
                "subcategory": record.subcategory,
                "audio_type": record.audio_type,
                "confidence": record.confidence,
                "duration": record.duration,
                "tags": json.dumps(record.tags),
                "pack_candidates": json.dumps(record.pack_candidates),
                "evidence_json": json.dumps(record.evidence),
                "analysis_status": record.analysis_status,
                "analysis_tags_json": record.analysis_tags_json,
                "classification_state": "done",
            })
            if len(classification_updates) >= 500:
                db.update_scan_item_classifications_by_path(scan_id, classification_updates)
                classification_updates.clear()
        pack_candidate_cache.pop(path, None)

    if classification_updates and scan_id and db:
        db.update_scan_item_classifications_by_path(scan_id, classification_updates)
    if scan_id and db and hasattr(db, "update_scan_run"):
        db.update_scan_run(
            scan_id,
            phase="staging",
            completed_count=db.count_scan_items(scan_id, "classification", "done"),
        )
    classification_progress.emit(total_items, force=True)
    return records
