from __future__ import annotations

from typing import Any
from collections import OrderedDict
import logging
import time
import numpy as np
try:
    import hnswlib
except ModuleNotFoundError:
    hnswlib = None

class SpatialIndex:
    """
    HNSW-based spatial index for fast approximate nearest neighbor (ANN) searches
    on current-schema acoustic vectors.
    """
    def __init__(self, vectors: np.ndarray, M: int = 16, ef_construction: int = 200) -> None:
        started = time.perf_counter()
        self.vectors = np.asarray(vectors, dtype=np.float32)
        self.num_elements, self.dim = self.vectors.shape
        if hnswlib is None:
            raise ModuleNotFoundError("hnswlib")
        
        self.index = hnswlib.Index(space="l2", dim=self.dim)
        self.index.init_index(
            max_elements=self.num_elements,
            ef_construction=ef_construction,
            M=M,
            random_seed=100,
        )
        
        if self.num_elements > 0:
            self.index.add_items(
                self.vectors,
                np.arange(self.num_elements),
                num_threads=1,
            )

        self.index.set_ef(50)
        logging.getLogger("unshuffle").debug(
            "Spatial index built: elements=%d dimensions=%d elapsed=%.3fs",
            self.num_elements,
            self.dim,
            time.perf_counter() - started,
        )

    def query(self, query_vector: np.ndarray, k: int = 10) -> tuple[np.ndarray, np.ndarray]:
        """
        Query the closest k neighbors for a given vector.
        Returns:
            labels: Array of nearest neighbor indices.
            distances: Array of L2 distances to those neighbors.
        """
        query_vector = np.asarray(query_vector, dtype=np.float32)
        if query_vector.ndim == 1:
            query_vector = query_vector[None, :]
            

        k = min(k, self.num_elements)
        if k <= 0:
            return np.array([[]], dtype=int), np.array([[]], dtype=np.float32)
            
        self.index.set_ef(max(50, k))
        labels, distances = self.index.knn_query(query_vector, k=k, num_threads=1)
        return labels, distances


class SparseSimilarityGraph:
    """Compact symmetric k-NN graph without a SciPy runtime dependency."""

    def __init__(self, neighbors: np.ndarray, weights: np.ndarray) -> None:
        self.neighbors = np.asarray(neighbors, dtype=np.int32)
        self.weights = np.asarray(weights, dtype=np.float64)
        self.shape = (self.neighbors.shape[0], self.neighbors.shape[0])
        valid = self.neighbors >= 0
        self._valid = valid
        self._rows = np.repeat(np.arange(self.shape[0], dtype=np.int32), self.neighbors.shape[1])[valid.ravel()]
        self._cols = self.neighbors[valid]
        self._edge_weights = self.weights[valid]
        self.degree = self.weights.sum(axis=1)
        self.degree += np.bincount(self._cols, weights=self._edge_weights, minlength=self.shape[0])

    def sum(self, axis=None):
        if axis == 1:
            return self.degree.copy()
        return float(self.degree.sum())

    def normalized_matmul(self, values: np.ndarray) -> np.ndarray:
        array = np.asarray(values, dtype=np.float64)
        was_vector = array.ndim == 1
        if was_vector:
            array = array[:, None]
        safe_degree = np.where(self.degree > 1e-12, self.degree, 1.0)
        scaled = array / np.sqrt(safe_degree)[:, None]
        output = np.zeros_like(scaled)
        edge_values = self._edge_weights[:, None]
        np.add.at(output, self._rows, edge_values * scaled[self._cols])
        np.add.at(output, self._cols, edge_values * scaled[self._rows])
        output /= np.sqrt(safe_degree)[:, None]
        return output[:, 0] if was_vector else output


class SparsePairwiseDistances:
    """
    A NumPy-like sparse representation of a pairwise distance matrix.
    Computes exact custom distances for local neighborhoods (retrieved via HNSW)
    and computes other requested pairwise distances on-the-fly.
    """
    def __init__(
        self,
        records: list[Any],
        engine: Any,
        nearest_k: int,
        M: int = 100,
        on_demand_cache_size: int = 8192,
    ) -> None:
        self.records = records
        self.engine = engine
        self.n = len(records)
        self.shape = (self.n, self.n)
        self.ndim = 2
        self.dtype = np.float32
        

        inputs = engine._vectorized_inputs(records)
        if inputs is not None:
            vectors = inputs["vectors"]
        else:
            from ...core.features import normalize_distance_vector
            vectors = np.array([normalize_distance_vector(r.vector) for r in records], dtype=np.float32)
            
        spatial_index = SpatialIndex(vectors)

        self.nearest = np.full((self.n, nearest_k), -1, dtype=np.int32)
        self.nearest_distances = np.full((self.n, nearest_k), np.inf, dtype=np.float32)
        self._on_demand_cache: OrderedDict[tuple[int, int], float] = OrderedDict()
        self._on_demand_cache_size = max(0, int(on_demand_cache_size))
        

        actual_M = min(M, self.n)
        

        for i in range(self.n):
            if actual_M <= 1:
                continue
                
            labels, _ = spatial_index.query(vectors[i], k=actual_M)
            candidate_indices = labels[0]
            

            candidate_indices = [c for c in candidate_indices if c != i]
            
            if candidate_indices:
                cand_records = [records[c] for c in candidate_indices]
                exact_dists = engine._distances_from_vectorized(records[i].vector, cand_records)
                if exact_dists is None:
                    exact_dists = np.array([
                        float(engine.similarity_engine.calculate_distance(records[i].vector, records[c].vector))
                        for c in candidate_indices
                    ], dtype=np.float32)
                    
                paired = sorted(zip(candidate_indices, exact_dists), key=lambda x: x[1])
                top_k = paired[:nearest_k]
                
                for rank, (c_idx, dist) in enumerate(top_k):
                    self.nearest[i, rank] = c_idx
                    self.nearest_distances[i, rank] = float(dist)
        del spatial_index
                        
    def __getitem__(self, key: Any) -> Any:
        import math
        if isinstance(key, tuple):
            row, col = key
            

            if isinstance(row, (int, np.integer)) and isinstance(col, (int, np.integer)):
                row_idx = int(row)
                col_idx = int(col)
                if row_idx == col_idx:
                    return 0.0
                positions = np.flatnonzero(self.nearest[row_idx] == col_idx)
                if positions.size:
                    return float(self.nearest_distances[row_idx, int(positions[0])])
                reverse_positions = np.flatnonzero(self.nearest[col_idx] == row_idx)
                if reverse_positions.size:
                    return float(self.nearest_distances[col_idx, int(reverse_positions[0])])
                cache_key = (min(row_idx, col_idx), max(row_idx, col_idx))
                cached = self._on_demand_cache.get(cache_key)
                if cached is not None:
                    self._on_demand_cache.move_to_end(cache_key)
                    return cached
                
                dist = float(self.engine.similarity_engine.calculate_distance(
                    self.records[row_idx].vector, self.records[col_idx].vector
                ))
                if not math.isfinite(dist):
                    dist = 1e9
                if self._on_demand_cache_size:
                    self._on_demand_cache[cache_key] = dist
                    self._on_demand_cache.move_to_end(cache_key)
                    while len(self._on_demand_cache) > self._on_demand_cache_size:
                        self._on_demand_cache.popitem(last=False)
                return dist
                

            if isinstance(row, (int, np.integer)) and isinstance(col, np.ndarray):
                return np.array([self[row, c] for c in col], dtype=float)
                

            if isinstance(row, np.ndarray) and isinstance(col, (int, np.integer)):
                return np.array([self[r, col] for r in row], dtype=float)
                

            if isinstance(row, np.ndarray) and isinstance(col, np.ndarray):
                r_flat = row.flatten()
                c_flat = col.flatten()
                
                if row.ndim == 2 and col.ndim == 2:
                    res = np.zeros((len(r_flat), len(c_flat)), dtype=float)
                    for i, r in enumerate(r_flat):
                        for j, c in enumerate(c_flat):
                            res[i, j] = self[r, c]
                    return res
                else:
                    res = np.zeros((len(r_flat), len(c_flat)), dtype=float)
                    for i, r in enumerate(r_flat):
                        for j, c in enumerate(c_flat):
                            res[i, j] = self[r, c]
                    return res
                    
        raise NotImplementedError(f"Indexing pattern not implemented: {type(key)}")
