import concurrent.futures
import os
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Optional, Set

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
from ...core.models import LibNode, NodeType
from ...core.path_safety import _is_protected_path_resolved, is_symlink_or_reparse
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
    def __init__(self, root_path: Path, progress_callback=None, db=None, target_dir: Optional[Path] = None):
        self.db = db
        self.target_dir = target_dir
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


def build_node_graph(root_path: Path, context: AnalysisContext) -> LibNode:
    root_path = root_path.resolve()
    context.root_path = root_path

    all_paths = [root_path]
    count = 0
    
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
            if context.progress_callback and count % 500 == 0:
                context.progress_callback({"message": f"Scanning discovery: {count} items found..."})

    total_found = len(all_paths)
    if context.progress_callback:
        context.progress_callback({"message": f"Mapping {total_found} items to graph..."})

    for index, path in enumerate(all_paths, 1):
        if context.is_interrupted():
            break
        name = path.name

        if _is_reserved_scan_name(path) or name.startswith("._"):
            continue

        if path == root_path:
            node_type = NodeType.ROOT
        elif path.is_file():
            node_type = NodeType.FILE
        else:
            node_type = NodeType.CONTAINER

        node = LibNode(path=path, name=name, node_type=node_type, extension=path.suffix.lower() if path.is_file() else None)
        if node_type == NodeType.FILE:
            node.hash = None
        if node_type in (NodeType.CONTAINER, NodeType.ROOT) and (path / PRESERVED_MARKER).exists():
            node.is_preserved = True
            node.preserved_root = path

        context.nodes[path] = node
        context.total_scanned += 1

        if node_type == NodeType.FILE:
            context.frequency_analyzer.feed_path(path)

        if context.progress_callback and index % 1000 == 0:
            context.progress_callback({"current": index, "total": total_found})

    all_file_nodes = [node for node in context.nodes.values() if node.node_type == NodeType.FILE and not node.name.startswith("._")]

    to_hash = []
    if all_file_nodes:
        file_stats = []
        statted_nodes = []
        if context.db and hasattr(context.db, "get_cached_hashes"):
            for node in all_file_nodes:
                try:
                    stat = node.path.stat()
                except OSError:
                    to_hash.append(node)
                    continue
                statted_nodes.append(node)
                file_stats.append((node.path, stat.st_size, stat.st_mtime))
            cached_hashes = context.db.get_cached_hashes(file_stats)
            for node in statted_nodes:
                cached = cached_hashes.get(node.path.as_posix())
                if cached:
                    node.hash = cached
                else:
                    to_hash.append(node)
            if context.progress_callback and cached_hashes:
                context.progress_callback({"message": f"Hash cache: {len(cached_hashes)} reused, {len(to_hash)} new."})
        else:
            for node in all_file_nodes:
                if context.db:
                    try:
                        stat = node.path.stat()
                        cached = context.db.get_cached_hash(node.path, stat.st_size, stat.st_mtime)
                        if cached:
                            node.hash = cached
                            continue
                    except OSError:
                        pass
                to_hash.append(node)

    if to_hash:
        from ...core.hashing import get_file_hash

        if len(to_hash) > 50:
            if context.progress_callback:
                context.progress_callback({"message": f"Hashing {len(to_hash)} files (Parallel)..."})
            paths = [node.path for node in to_hash]
            max_workers = min(8, max(2, os.cpu_count() or 2), len(to_hash))
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                hashes = list(executor.map(get_file_hash, paths))
                if context.is_interrupted():
                    return context.nodes[root_path]
                for idx, (node, file_hash) in enumerate(zip(to_hash, hashes), 1):
                    node.hash = file_hash
                    if context.progress_callback and idx % 100 == 0:
                        context.progress_callback({"current": idx, "total": len(to_hash)})
        else:
            if context.progress_callback:
                context.progress_callback({"message": f"Hashing {len(to_hash)} files (Serial)..."})
            for idx, node in enumerate(to_hash, 1):
                node.hash = get_file_hash(node.path)
                if context.progress_callback and idx % 10 == 0:
                    context.progress_callback({"current": idx, "total": len(to_hash)})

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
    return context.nodes[root_path]


def run_analysis(root_path: Path, progress_callback=None, db=None, target_dir: Optional[Path] = None) -> AnalysisContext:
    context = AnalysisContext(root_path, progress_callback, db=db, target_dir=target_dir)
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
