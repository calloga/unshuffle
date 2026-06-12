import concurrent.futures
import json
import os
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, Set, Tuple, TypeVar

from ...audio.acoustic import SimilarityEngine
from ...audio.metadata import get_audio_duration
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
from ...core.tags import extract_tags_from_name
from ...logic.analysis import AnalysisContext, build_discovery_data, run_analysis
from ...logic.classification import classify_node, compute_component_score, detect_audio_type, get_subcategory, tokenize
from ...logic.planning.rules import is_generic_folder
from ...persistence import get_directory_dump_filename, get_discovery_data_filename, save_json_meta

_T = TypeVar("_T")
DEFAULT_EXTRACTOR_WORKERS = 8


def _extractor_worker_count(total: int) -> int:
    if total <= 0:
        return 1
    try:
        override = int(os.environ.get("UNSHUFFLE_EXTRACTOR_WORKERS", "0") or "0")
    except ValueError:
        override = 0
    if override > 0:
        return max(1, min(override, total))
    return min(max(os.cpu_count() or 1, 1), DEFAULT_EXTRACTOR_WORKERS, total)


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


def _iter_executor_results(
    executor: concurrent.futures.Executor,
    func: Callable[[Any], _T],
    items: Iterable[Any],
    is_interrupted: Any = None,
) -> Iterator[_T]:
    iterator = executor.map(func, items)
    for item in iterator:
        if is_interrupted and is_interrupted():
            break
        yield item


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
) -> List[PlanRecord]:
    """Coordinates the multi-pass planning algorithm."""
    if progress_callback:
        progress_callback({"message": f"Phase 1: Structural Analysis of {source_root.name}..."})
    context = run_analysis(source_root, progress_callback=progress_callback, db=db, target_dir=target_dir)
    if is_interrupted:
        context.is_interrupted = is_interrupted

    if progress_callback:
        progress_callback({"message": "Phase 1.5: Finalizing Global Library Composition..."})
    context.frequency_analyzer.finalize()
    global_boosts = context.frequency_analyzer.boosts
    runtime_config = get_runtime_config_snapshot()
    logger.info("Global Frequency Boosts: %s", global_boosts)

    dump_data = [
        {"path": node.path.as_posix(), "node": node.name, "type": node.node_type.name, "hash": node.hash}
        for node in context.nodes.values()
    ]
    dump_filename = get_directory_dump_filename(session_id, source_root)
    save_json_meta(target_dir, dump_filename, dump_data, is_dry_run=is_dry_run)
    save_json_meta(target_dir, get_discovery_data_filename(source_root), build_discovery_data(context), is_dry_run=is_dry_run)

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

            best_pack, _ = _determine_best_pack(node, source_root, context.nodes, candidate_cache=pack_candidate_cache)
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
    logger.info("Phase 3: Final Weighted Categorization...")

    process_nodes = [node for node in context.nodes.values() if node.node_type == NodeType.FILE or node.is_preserved]
    file_nodes = [node for node in process_nodes if node.node_type == NodeType.FILE]

    skip_expensive_hashes = set(skip_expensive_hashes or ())
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
            progress_callback({"message": f"Audio feature analysis of {len(expensive_audio_nodes)} files..."})
        sim_engine = SimilarityEngine()

        to_extract: list[Path] = []
        extract_dependents: Dict[Path, list[Path]] = {}
        queued_extract_keys: Dict[tuple[str, str], Path] = {}
        supported = SimilarityEngine.SUPPORTED_EXTS
        cached_feature_vectors: Dict[str, bytes] = {}
        if db and hasattr(db, "get_feature_vectors_bulk"):
            cached_feature_vectors = db.get_feature_vectors_bulk(
                [node.hash for node in expensive_audio_nodes if node.hash]
            )

        for node in expensive_audio_nodes:
            if not node.extension or node.extension.lower() not in supported:
                continue

            if db:
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
            max_workers = _extractor_worker_count(len(to_extract))
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                results = list(_iter_executor_results(executor, sim_engine.extract_feature_payload, to_extract, is_interrupted=is_interrupted))
                for path, payload in zip(to_extract, results):
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
                                ))
                    else:
                        failure_tag = sim_engine.extraction_failure_tag(path)
                        if failure_tag:
                            for result_path in dependent_paths:
                                analysis_failure_tags[result_path] = failure_tag
                                analysis_statuses[result_path] = failure_tag
        if db and cache_updates:
            db.update_cache_bulk(cache_updates)

    duration_nodes = [node for node in expensive_audio_nodes if node.path not in durations]
    if duration_nodes:
        if progress_callback:
            progress_callback({"message": f"Detecting durations for {len(duration_nodes)} files..."})
        max_workers = max(1, (os.cpu_count() or 1) - 1)
        paths = [node.path for node in duration_nodes]
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            duration_list = list(_iter_executor_results(executor, get_audio_duration, paths, is_interrupted=is_interrupted))
            durations.update(dict(zip(paths, duration_list)))

    total_items = len(process_nodes)
    if progress_callback:
        progress_callback({"message": "Commencing final categorization..."})

    for index, node in enumerate(process_nodes, start=1):
        if is_interrupted and is_interrupted():
            break
        path = node.path

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
            continue

        duration = durations.get(path)

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
                features=feature_values.get(path),
                min_confidence=min_confidence,
                debug=debug_this,
                runtime=runtime_config,
            )

            if debug_this:
                print(f"  [RESULT] Category: {cat} ({conf})")
                print("-" * 40)

            audio_type = detect_audio_type(node, duration=duration, runtime=runtime_config, features=feature_values.get(path))
            tokens = tokenize(node.name)
            subcategory = get_subcategory(cat, tokens, runtime=runtime_config)

        if audio_type == "Metadata":
            continue

        if progress_callback and index % 5 == 0:
            progress_callback({"current": index, "total": total_items})

        tags = extract_tags_from_name(node.name)
        if failure_tag := analysis_failure_tags.get(path):
            tags = [*tags, failure_tag]

        records.append(
            PlanRecord(
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
                tags=tags,
                duration=duration or 0.0,
                feature_vector=feature_vectors.get(path),
                feature_space_version=CURRENT_FEATURE_SPACE_VERSION if path in feature_vectors else None,
                feature_schema_json=json.dumps(list(CURRENT_FEATURE_SCHEMA)) if path in feature_vectors else None,
                analysis_status=analysis_statuses.get(path),
                analysis_tags_json=json.dumps([analysis_failure_tags[path]]) if path in analysis_failure_tags else "[]",
            )
        )

    return records
