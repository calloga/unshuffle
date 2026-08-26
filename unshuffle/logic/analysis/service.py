import concurrent.futures
import os
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Optional, Set

from ...core.concurrency import bounded_map, max_scan_workers
from ...core.constants import (
    ALIAS_TABLE,
    CACHE_FILE_NAME,
    CHILD_DUP_BONUS,
    IGNORED_SYSTEM_ARTIFACT_NAMES,
    LARGE_BRAND_BONUS_MULT,
    PRESERVED_MARKER,
    LARGE_CONTAINER_MALUS,
    LEAF_GENERIC_MALUS,
    LEAF_IDENTITY_BONUS,
    LEAF_MALUS,
    LOSER_MALUS_MULT,
    MODEL_NUMBERS,
    NEIGHBOR_BOOST_BASE,
    NEIGHBOR_BOOST_MAX,
    NOISE_WORDS,
    PURE_CONTAINER_BONUS,
    PURE_GENERIC_BONUS,
    RESERVED_NAMES,
    SHARED_BOOST_BASE,
    SHARED_BOOST_THRESHOLD,
    WINNER_BONUS_MULT,
)
from ...core.hashing import get_fast_hash, get_file_hash
from ...core.models import LibNode, NodeType
from ...core.path_safety import _is_protected_path_resolved, is_symlink_or_reparse
from ...core.progress import PhaseProgress
from ...logic.classification import is_category_alias, tokenize
from .frequency import GlobalFrequencyAnalyzer

_DUPLICATE_CHILD_SHARED_RATIO_THRESHOLD = 0.5


def _is_reserved_scan_name(path: Path) -> bool:
    name = path.name.casefold()
    reserved = {str(value).casefold() for value in RESERVED_NAMES}
    ignored_artifacts = {str(value).casefold() for value in IGNORED_SYSTEM_ARTIFACT_NAMES}
    return name in reserved or name in ignored_artifacts


def _is_protected_scan_path(path: Path, root_path: Path) -> bool:
    return _is_protected_path_resolved(path, root_path)


class TokenRegistry:
    def __init__(self):
        self.token_to_id = {}
        self.id_to_token = {}
        self.next_id = 0

    def get_id(self, token: str) -> int:
        if token not in self.token_to_id:
            self.token_to_id[token] = self.next_id
            self.id_to_token[self.next_id] = token
            self.next_id += 1
        return self.token_to_id[token]


class AnalysisContext:
    def __init__(
        self,
        root_path: Path,
        progress_callback=None,
        db=None,
        target_dir: Optional[Path] = None,
        scan_id: str | None = None,
        lean_db_items: bool = False,
    ):
        self.db = db
        self.target_dir = target_dir
        self.scan_id = scan_id
        self.lean_db_items = lean_db_items
        self.root_path = root_path
        self.resolved_target_dir = target_dir.resolve() if target_dir else None
        self.nodes: Dict[Path, LibNode] = {}
        self.word_map: Counter = Counter()
        self.global_word_map: Dict[str, int] = {}
        self.category_distribution: Dict[Path, Counter] = {}
        self.total_scanned = 0
        self.progress_callback = progress_callback
        self.is_interrupted = lambda: False

        self.token_registry = TokenRegistry()
        self.descendant_token_sets: Dict[Path, Set[int]] = {}
        self.descendant_counts: Dict[Path, int] = {}
        self.frequency_analyzer = GlobalFrequencyAnalyzer()

    def get_node_roles(self):
        sorted_nodes = sorted(self.nodes.values(), key=lambda node: len(node.path.parts), reverse=True)

        for node in sorted_nodes:
            if node.node_type not in (NodeType.CONTAINER, NodeType.ROOT):
                continue

            subfolders = [child for child in node.children if child.node_type in (NodeType.CONTAINER, NodeType.LEAF)]
            if not subfolders:
                continue

            if len(subfolders) == 1:
                child = subfolders[0]
                node_tokens = {
                    token for token in tokenize(node.name) if (token in MODEL_NUMBERS) or (token not in ALIAS_TABLE and token not in NOISE_WORDS)
                }
                child_tokens = {
                    token for token in tokenize(child.name) if (token in MODEL_NUMBERS) or (token not in ALIAS_TABLE and token not in NOISE_WORDS)
                }
                shared_tokens = node_tokens & child_tokens
                shared_ratio = len(shared_tokens) / max(len(node_tokens), len(child_tokens), 1)
                if shared_ratio > _DUPLICATE_CHILD_SHARED_RATIO_THRESHOLD:
                    node.is_duplicate_container = True
                    child.is_child_of_duplicate = True
                    child.duplicate_child_bonus = round(CHILD_DUP_BONUS * shared_ratio, 3)

            pure_children = [child for child in subfolders if child.is_pure_container]
            leaf_children = [child for child in subfolders if child.node_type == NodeType.LEAF]
            file_children = [child for child in node.children if child.node_type == NodeType.FILE]
            non_pure_containers = [
                child for child in subfolders if child.node_type == NodeType.CONTAINER and not child.is_pure_container
            ]
            std_children = [child for child in subfolders if child.is_standard_container]

            node.is_pure_container = len(subfolders) > 0 and all(child.node_type == NodeType.LEAF for child in subfolders)

            total_children = len(node.children)
            if total_children > 0 and not node.is_pure_container:
                majority_count = len(pure_children) + len(leaf_children) + len(file_children)
                if majority_count > (total_children / 2):
                    node.is_standard_container = True

            if total_children > 0:
                is_mostly_nonpure = len(non_pure_containers) > (total_children / 2)
                is_multi_standard = (len(std_children) >= 3) and (len(std_children) > (total_children * 0.30))
                if is_mostly_nonpure or is_multi_standard:
                    node.is_large_container = True

    def calculate_pack_weights(self):
        sorted_paths = sorted(self.nodes.keys(), key=lambda path: len(path.parts), reverse=True)

        if self.progress_callback:
            self.progress_callback({"message": "Analyzing token density..."})

        for path in sorted_paths:
            node = self.nodes[path]
            token_set = {self.token_registry.get_id(token) for token in node.unweighted_tokens}
            descendant_count = 0

            for child in node.children:
                if child.path in self.descendant_token_sets:
                    token_set.update(self.descendant_token_sets[child.path])
                descendant_count += 1 + self.descendant_counts.get(child.path, 0)

            self.descendant_token_sets[path] = token_set
            self.descendant_counts[path] = descendant_count

        for node in self.nodes.values():
            if node.node_type not in (NodeType.CONTAINER, NodeType.LEAF, NodeType.ROOT):
                continue

            weight = 0.0
            node.weight_evidence = {}
            if node.is_pure_container:
                tokens = [token for token in tokenize(node.name) if not is_category_alias(token) and token not in NOISE_WORDS]
                if tokens:
                    weight += PURE_CONTAINER_BONUS
                    node.weight_evidence["PURE"] = PURE_CONTAINER_BONUS
                else:
                    weight += PURE_GENERIC_BONUS
                    node.weight_evidence["PURE_GENERIC"] = PURE_GENERIC_BONUS
            if node.is_large_container:
                weight += LARGE_CONTAINER_MALUS
                node.weight_evidence["LARGE_MALUS"] = LARGE_CONTAINER_MALUS

                tokens = [token for token in tokenize(node.name) if not is_category_alias(token) and token not in NOISE_WORDS]
                if tokens:
                    brand_bonus = round(LARGE_BRAND_BONUS_MULT * len(tokens), 3)
                    weight += brand_bonus
                    node.weight_evidence["LARGE_BRAND_BONUS"] = brand_bonus
            if node.is_child_of_duplicate:
                boost = node.duplicate_child_bonus or CHILD_DUP_BONUS
                weight += boost
                node.weight_evidence["CHILD_DUP"] = boost

            if node.node_type == NodeType.LEAF:
                tokens = tokenize(node.name)
                has_unweighted = any(not is_category_alias(token) and token not in NOISE_WORDS for token in tokens)
                if has_unweighted:
                    weight += LEAF_IDENTITY_BONUS
                    node.weight_evidence["LEAF_IDENTITY"] = LEAF_IDENTITY_BONUS
                else:
                    weight += LEAF_GENERIC_MALUS
                    node.weight_evidence["LEAF_GENERIC"] = LEAF_GENERIC_MALUS

            has_boost = False
            if node.node_type in (NodeType.CONTAINER, NodeType.LEAF):
                tokens = [token for token in node.unweighted_tokens]
                parent_token_count = len(node.unweighted_tokens)

                if tokens:
                    total_descendants = self.descendant_counts.get(node.path, 0)
                    if total_descendants > 0:
                        match_count = 0
                        for child in node.children:
                            child_token_count = len(child.unweighted_tokens)
                            if parent_token_count >= child_token_count or child.node_type == NodeType.FILE:
                                child_tokens = self.descendant_token_sets.get(child.path, set())
                                if any(self.token_registry.get_id(token) in child_tokens for token in tokens):
                                    match_count += 1 + self.descendant_counts.get(child.path, 0)

                        if (match_count / total_descendants) >= SHARED_BOOST_THRESHOLD:
                            token_count = min(4, parent_token_count)
                            if token_count <= 1:
                                boost_val = SHARED_BOOST_BASE
                            else:
                                boost_val = SHARED_BOOST_BASE + (SHARED_BOOST_BASE * token_count)

                            weight += boost_val
                            node.weight_evidence["SHARED_BOOST"] = round(boost_val, 3)
                            has_boost = True

            if node.node_type == NodeType.LEAF and not has_boost:
                weight += LEAF_MALUS
                node.weight_evidence["LEAF_MALUS"] = LEAF_MALUS

            node.pack_candidate_weight = round(weight, 2)

        boosted_nodes = set()
        for node in self.nodes.values():
            if node.node_type not in (NodeType.CONTAINER, NodeType.LEAF):
                continue
            tokens = tokenize(node.name)
            if tokens and all(is_category_alias(token) for token in tokens):
                neighbors = []
                if node.parent and node.parent.node_type in (NodeType.CONTAINER, NodeType.LEAF):
                    neighbors.append(node.parent)
                neighbors.extend([child for child in node.children if child.node_type in (NodeType.CONTAINER, NodeType.LEAF)])

                valid = [
                    neighbor
                    for neighbor in neighbors
                    if any(not is_category_alias(token) and token not in NOISE_WORDS for token in tokenize(neighbor.name))
                ]
                if valid:
                    best = min(valid, key=lambda neighbor: abs(neighbor.pack_candidate_weight - node.pack_candidate_weight))
                    if best.path not in boosted_nodes:
                        boost_val = round(abs(best.pack_candidate_weight - node.pack_candidate_weight) + NEIGHBOR_BOOST_BASE, 2)
                        boost_val = min(boost_val, NEIGHBOR_BOOST_MAX)
                        best.pack_candidate_weight += boost_val
                        best.weight_evidence["NEIGHBOR_BOOST"] = boost_val
                        boosted_nodes.add(best.path)

        parent_stats = {}
        for node in self.nodes.values():
            if node.parent and node.node_type in (NodeType.CONTAINER, NodeType.LEAF):
                parent = node.parent
                if parent.node_type in (NodeType.CONTAINER, NodeType.LEAF, NodeType.ROOT):
                    stats = parent_stats.get(parent.path, [0, 0, 0])
                    stats[1] += 1
                    diff = node.pack_candidate_weight - parent.pack_candidate_weight

                    if diff > 0.1:
                        stats[0] += 1
                    elif diff < -0.1:
                        stats[2] += 1

                    parent_stats[parent.path] = stats

        for path, (losses, total, wins) in parent_stats.items():
            if total > 0:
                parent = self.nodes[path]

                if losses > 0:
                    loss_ratio = losses / total
                    penalty = round(LOSER_MALUS_MULT * loss_ratio, 3)
                    parent.pack_candidate_weight += penalty
                    parent.weight_evidence["LOSER_MALUS"] = penalty

                if wins > 0 and parent.is_large_container:
                    name_tokens = [token for token in tokenize(parent.name) if not is_category_alias(token) and token not in NOISE_WORDS]
                    if name_tokens:
                        win_ratio = wins / total
                        bonus = round(WINNER_BONUS_MULT * win_ratio, 3)
                        parent.pack_candidate_weight += bonus
                        parent.weight_evidence["WINNER_BONUS"] = bonus

                parent.pack_candidate_weight = round(parent.pack_candidate_weight, 3)


def _discover_paths_in_memory(root_path: Path, context: AnalysisContext) -> list[Path]:
    all_paths = [root_path]
    count = 0
    discovery_progress = PhaseProgress(
        context.progress_callback,
        "Discovering Samples",
        message="Discovering samples...",
        update_every=500,
    )
    
    if (root_path / PRESERVED_MARKER).exists():
        
        dirs_to_walk = []
    else:
        dirs_to_walk = [root_path]

    for root_p in dirs_to_walk:
        for root, dirs, files in os.walk(root_p):
            if context.is_interrupted():
                break
            root_path_obj = Path(root)
            dirs.sort()
            files.sort()

            dirs[:] = [directory for directory in dirs if not _is_reserved_scan_name(root_path_obj / directory)]
            files = [filename for filename in files if not _is_reserved_scan_name(root_path_obj / filename)]
            dirs[:] = [directory for directory in dirs if not is_symlink_or_reparse(root_path_obj / directory)]
            files = [filename for filename in files if not is_symlink_or_reparse(root_path_obj / filename)]

            hands_off_dirs = [directory for directory in dirs if (root_path_obj / directory / PRESERVED_MARKER).exists()]
            for directory in hands_off_dirs:
                all_paths.append(root_path_obj / directory)
                dirs.remove(directory)

            if (
                context.resolved_target_dir
                and root_path_obj != root_path
                and _is_protected_scan_path(root_path_obj, context.resolved_target_dir)
            ):
                dirs[:] = []
                continue

            for directory in dirs:
                directory_path = root_path_obj / directory
                if not _is_protected_scan_path(directory_path, root_path):
                    all_paths.append(directory_path)
            for filename in files:
                file_path = root_path_obj / filename
                if not _is_protected_scan_path(file_path, root_path):
                    all_paths.append(file_path)

            count += len(dirs) + len(files)
            discovery_progress.emit(count, message=f"Discovering samples: {count} items found...")

    return all_paths


def _ensure_root_node(root_path: Path, context: AnalysisContext) -> LibNode:
    root_node = context.nodes.get(root_path)
    if root_node is not None:
        return root_node

    root_node = LibNode(path=root_path, name=root_path.name, node_type=NodeType.ROOT)
    if (root_path / PRESERVED_MARKER).exists():
        root_node.is_preserved = True
        root_node.preserved_root = root_path
    context.nodes[root_path] = root_node
    return root_node


def build_node_graph(root_path: Path, context: AnalysisContext) -> LibNode:
    root_path = root_path.resolve()
    context.root_path = root_path

    use_scan_store = bool(
        context.scan_id
        and context.db is not None
        and hasattr(context.db, "insert_scan_items")
        and hasattr(context.db, "iter_discovered_scan_nodes")
    )
    if use_scan_store:
        from .scan_discovery import discover_to_scan_store

        assert context.scan_id is not None
        total_found = discover_to_scan_store(
            context.db,
            context.scan_id,
            root_path,
            target_dir=context.target_dir,
            is_interrupted=context.is_interrupted,
            progress_callback=context.progress_callback,
        )
        node_rows = (
            row
            for batch in context.db.iter_discovered_scan_nodes(context.scan_id, batch_size=1000)
            for row in batch
        )
    else:
        all_paths = _discover_paths_in_memory(root_path, context)
        total_found = len(all_paths)
        node_rows = (
            {
                "normalized_path": path.as_posix(),
                "name": path.name,
                "node_type": "root" if path == root_path else "file" if path.is_file() else "directory",
                "extension": path.suffix.lower() if path.is_file() else None,
                "is_preserved": bool(path.is_dir() and (path / PRESERVED_MARKER).exists()),
            }
            for path in all_paths
        )

    mapping_progress = PhaseProgress(
        context.progress_callback,
        "Discovering Samples",
        total=total_found,
        message="Mapping samples to graph...",
        update_every=1000,
    )
    mapping_progress.emit(0, force=True)

    for index, row in enumerate(node_rows, 1):
        if context.is_interrupted():
            break
        path = Path(str(row["normalized_path"]))
        name = str(row.get("name") or path.name)

        if _is_reserved_scan_name(path) or name.startswith("._"):
            continue

        row_type = str(row.get("node_type") or "")
        if row_type == "root" or path == root_path:
            node_type = NodeType.ROOT
        elif row_type == "file":
            node_type = NodeType.FILE
        else:
            node_type = NodeType.CONTAINER

        if node_type == NodeType.FILE and context.lean_db_items:
            context.frequency_analyzer.feed_path(path)
            context.total_scanned += 1
            mapping_progress.emit(index)
            continue

        node = LibNode(
            path=path,
            name=name,
            node_type=node_type,
            extension=str(row.get("extension") or "") if node_type == NodeType.FILE else None,
        )
        if node_type == NodeType.FILE:
            node.hash = row.get("effective_hash") or None
            node.fast_hash = row.get("fast_hash") or None
        if node_type in (NodeType.CONTAINER, NodeType.ROOT) and bool(row.get("is_preserved")):
            node.is_preserved = True
            node.preserved_root = path

        context.nodes[path] = node
        context.total_scanned += 1

        if node_type == NodeType.FILE:
            context.frequency_analyzer.feed_path(path)

        mapping_progress.emit(index)

    if context.is_interrupted():
        return _ensure_root_node(root_path, context)

    mapping_progress.emit(total_found, force=True)

    if use_scan_store:
        from .scan_hashing import hash_scan_items

        assert context.scan_id is not None
        hash_scan_items(
            context.db,
            context.scan_id,
            is_interrupted=context.is_interrupted,
            progress_callback=context.progress_callback,
        )
        for batch in context.db.iter_scan_items(
            context.scan_id,
            columns="normalized_path, fast_hash, effective_hash",
            batch_size=2000,
        ):
            for row in batch:
                node = context.nodes.get(Path(row["normalized_path"]))
                if node is not None:
                    node.fast_hash = row.get("fast_hash") or None
                    node.hash = row.get("effective_hash") or None

    all_file_nodes = [node for node in context.nodes.values() if node.node_type == NodeType.FILE and not node.name.startswith("._")]

    to_hash = []
    if all_file_nodes and not use_scan_store:
        cache_progress = PhaseProgress(
            context.progress_callback,
            "Checking Cache",
            total=len(all_file_nodes),
            message=f"Checking hash cache for {len(all_file_nodes)} files.",
            update_every=500,
        )
        cache_progress.emit(0, force=True)
        file_stats = []
        statted_nodes = []
        if context.db and (hasattr(context.db, "get_cached_entries") or hasattr(context.db, "get_cached_hashes")):
            for index, node in enumerate(all_file_nodes, 1):
                if node.hash:
                    cache_progress.emit(index)
                    continue
                try:
                    stat = node.path.stat()
                except OSError:
                    to_hash.append(node)
                    cache_progress.emit(index)
                    continue
                statted_nodes.append(node)
                file_stats.append((node.path, stat.st_size, stat.st_mtime))
                cache_progress.emit(index)
            if hasattr(context.db, "get_cached_entries"):
                cached_entries = context.db.get_cached_entries(file_stats)
            else:
                cached_entries = {
                    path: {"hash": file_hash, "fast_hash": None}
                    for path, file_hash in context.db.get_cached_hashes(file_stats).items()
                }
            for node in statted_nodes:
                cached = cached_entries.get(node.path.as_posix())
                if cached:
                    node.hash = cached.get("hash")
                    node.fast_hash = cached.get("fast_hash")
                else:
                    to_hash.append(node)
            if context.scan_id and hasattr(context.db, "update_scan_item_hashes_by_path"):
                context.db.update_scan_item_hashes_by_path(
                    context.scan_id,
                    (
                        (node.path, node.fast_hash, node.hash)
                        for node in statted_nodes
                        if node.hash
                    ),
                )
            if context.progress_callback and cached_entries:
                context.progress_callback({"message": f"Hash cache: {len(cached_entries)} reused, {len(to_hash)} new."})
        elif context.db and (hasattr(context.db, "get_cached_entry") or hasattr(context.db, "get_cached_hash")):
            for index, node in enumerate(all_file_nodes, 1):
                try:
                    stat = node.path.stat()
                    if hasattr(context.db, "get_cached_entry"):
                        cached = context.db.get_cached_entry(node.path, stat.st_size, stat.st_mtime)
                    else:
                        file_hash = context.db.get_cached_hash(node.path, stat.st_size, stat.st_mtime)
                        cached = {"hash": file_hash, "fast_hash": None} if file_hash else None
                    if cached:
                        node.hash = cached.get("hash")
                        node.fast_hash = cached.get("fast_hash")
                        cache_progress.emit(index)
                        continue
                except OSError:
                    pass
                to_hash.append(node)
                cache_progress.emit(index)
        else:
            to_hash.extend(node for node in all_file_nodes if not node.hash)
            cache_progress.emit(len(all_file_nodes), force=True)
        cache_progress.emit(len(all_file_nodes), force=True)
    if to_hash:
        from collections import defaultdict

        def assign_hashes(nodes, hash_func, message: str, serial_message: str, phase: str) -> None:
            progress = PhaseProgress(
                context.progress_callback,
                phase,
                total=len(nodes),
                message=message,
                update_every=100 if len(nodes) > 50 else 10,
            )
            if len(nodes) > 50:
                max_workers = max_scan_workers(len(nodes))
                max_pending = max_workers * 2
                progress.emit(0, force=True)

                with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                    hash_updates = []
                    for idx, (node, file_hash) in enumerate(
                        bounded_map(
                            executor,
                            lambda item: hash_func(item.path),
                            nodes,
                            max_pending=max_pending,
                            is_interrupted=context.is_interrupted,
                        ),
                        1,
                    ):
                        if context.is_interrupted():
                            return
                        node.hash = file_hash
                        if hash_func is get_fast_hash:
                            node.fast_hash = file_hash
                        hash_updates.append((node.path, node.fast_hash, node.hash))
                        if len(hash_updates) >= 500 and context.scan_id and hasattr(context.db, "update_scan_item_hashes_by_path"):
                            context.db.update_scan_item_hashes_by_path(context.scan_id, hash_updates)
                            hash_updates.clear()
                        progress.emit(idx)
                    if hash_updates and context.scan_id and hasattr(context.db, "update_scan_item_hashes_by_path"):
                        context.db.update_scan_item_hashes_by_path(context.scan_id, hash_updates)
                progress.emit(len(nodes), force=True)
            else:
                progress.emit(0, message=serial_message, force=True)

                hash_updates = []
                for idx, node in enumerate(nodes, 1):
                    if context.is_interrupted():
                        return
                    file_hash = hash_func(node.path)
                    node.hash = file_hash
                    if hash_func is get_fast_hash:
                        node.fast_hash = file_hash
                    hash_updates.append((node.path, node.fast_hash, node.hash))
                    progress.emit(idx)
                if hash_updates and context.scan_id and hasattr(context.db, "update_scan_item_hashes_by_path"):
                    context.db.update_scan_item_hashes_by_path(context.scan_id, hash_updates)
                progress.emit(len(nodes), force=True)

        assign_hashes(
            to_hash,
            get_fast_hash,
            f"Fast hashing {len(to_hash)} files.",
            f"Fast hashing {len(to_hash)} files (Serial)...",
            "Hashing",
        )

        if context.is_interrupted():
            return context.nodes[root_path]

        buckets = defaultdict(list)
        for node in to_hash:
            if node.fast_hash:
                buckets[node.fast_hash].append(node)

        to_promote = [
            node
            for bucket in buckets.values()
            if len(bucket) > 1
            for node in bucket
        ]

        if to_promote:
            assign_hashes(
                to_promote,
                get_file_hash,
                f"Confirming {len(to_promote)} possible duplicate files.",
                f"Confirming {len(to_promote)} possible duplicate files (Serial)...",
                "Finding Duplicates",
            )

        if context.is_interrupted():
            return context.nodes[root_path]
            
    if use_scan_store:
        import json
        from .scan_structure import (
            ROLE_CHILD_OF_DUPLICATE,
            ROLE_DUPLICATE,
            ROLE_LARGE,
            ROLE_LEAF,
            ROLE_PURE,
            ROLE_STANDARD,
            analyze_scan_structure,
        )

        assert context.scan_id is not None
        analyze_scan_structure(
            context.db,
            context.scan_id,
            progress_callback=context.progress_callback,
        )
        for batch in context.db.iter_scan_directories(context.scan_id, batch_size=1000):
            for row in batch:
                node = context.nodes.get(Path(row["normalized_path"]))
                if node is None:
                    continue
                flags = int(row.get("role_flags") or 0)
                node.node_type = NodeType.LEAF if flags & ROLE_LEAF else node.node_type
                node.is_pure_container = bool(flags & ROLE_PURE)
                node.is_duplicate_container = bool(flags & ROLE_DUPLICATE)
                node.is_child_of_duplicate = bool(flags & ROLE_CHILD_OF_DUPLICATE)
                node.is_large_container = bool(flags & ROLE_LARGE)
                node.is_standard_container = bool(flags & ROLE_STANDARD)
                node.pack_candidate_weight = float(row.get("pack_weight") or 0.0)
                try:
                    node.weight_evidence = json.loads(row.get("weight_evidence_json") or "{}")
                    node.unweighted_tokens = list(json.loads(row.get("token_blob") or "[]"))
                except (TypeError, json.JSONDecodeError):
                    node.weight_evidence = {}
                    node.unweighted_tokens = []
                tokens = tokenize(node.name)
                node.name_weighted_tokens = [token for token in tokens if is_category_alias(token)]
                context.word_map.update(node.unweighted_tokens)
        for node in context.nodes.values():
            if node.node_type == NodeType.FILE:
                tokens = tokenize(node.name)
                node.name_weighted_tokens = [token for token in tokens if is_category_alias(token)]
                node.unweighted_tokens = [
                    token for token in tokens
                    if not is_category_alias(token) and token not in NOISE_WORDS
                ]
        context.global_word_map = dict(context.word_map)
        return context.nodes[root_path]

    for path, node in context.nodes.items():
        if path == root_path:
            continue
        parent = context.nodes.get(path.parent)
        if parent:
            node.parent = parent
            parent.children.append(node)

    for node in context.nodes.values():
        if node.node_type == NodeType.CONTAINER:
            if all(child.node_type == NodeType.FILE for child in node.children):
                node.node_type = NodeType.LEAF

        if node.parent and node.parent.is_preserved:
            node.is_preserved = True
            node.preserved_root = node.parent.preserved_root

    for path, node in context.nodes.items():
        if node.node_type == NodeType.CONTAINER and all(child.node_type == NodeType.FILE for child in node.children):
            node.node_type = NodeType.LEAF
        tokens = tokenize(node.name)
        node.name_weighted_tokens = [token for token in tokens if is_category_alias(token)]
        node.unweighted_tokens = [token for token in tokens if not is_category_alias(token) and token not in NOISE_WORDS]
        if node.node_type != NodeType.FILE:
            context.word_map.update(node.unweighted_tokens)

    context.get_node_roles()
    context.calculate_pack_weights()
    context.global_word_map = dict(context.word_map)
    # Planning uses path lookups and the computed weights, not graph edges.
    # Break cycles and release structural-only indexes before feature analysis.
    for node in context.nodes.values():
        node.parent = None
        node.children.clear()
    context.descendant_token_sets.clear()
    context.descendant_counts.clear()
    context.category_distribution.clear()
    return context.nodes[root_path]


def run_analysis(
    root_path: Path,
    progress_callback=None,
    db=None,
    target_dir: Optional[Path] = None,
    scan_id: str | None = None,
    is_interrupted=None,
    lean_db_items: bool = False,
) -> AnalysisContext:
    context = AnalysisContext(
        root_path,
        progress_callback,
        db=db,
        target_dir=target_dir,
        scan_id=scan_id,
        lean_db_items=lean_db_items,
    )
    if is_interrupted is not None:
        context.is_interrupted = is_interrupted
    build_node_graph(root_path, context)
    return context


def build_discovery_data(context: AnalysisContext) -> Dict[str, Any]:
    entries = []
    for node in context.nodes.values():
        if node.node_type != NodeType.FILE:
            continue
        entries.append(
            {
                "path": node.path.as_posix(),
                "name": node.name,
                "tokens": sorted(tokenize(node.name)),
            }
        )
    return {
        "source_root": context.root_path.as_posix(),
        "entries": entries,
    }
