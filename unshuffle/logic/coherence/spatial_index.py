from __future__ import annotations

from typing import Any
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
        self.vectors = np.asarray(vectors, dtype=np.float32)
        self.num_elements, self.dim = self.vectors.shape
        if hnswlib is None:
            raise ModuleNotFoundError("hnswlib")
        
        self.index = hnswlib.Index(space="l2", dim=self.dim)
        self.index.init_index(
            max_elements=self.num_elements,
            ef_construction=ef_construction,
            M=M
        )
        
        if self.num_elements > 0:
            self.index.add_items(self.vectors, np.arange(self.num_elements))

        self.index.set_ef(50)

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
        labels, distances = self.index.knn_query(query_vector, k=k)
        return labels, distances


class SparsePairwiseDistances:
    """
    A NumPy-like sparse representation of a pairwise distance matrix.
    Computes exact custom distances for local neighborhoods (retrieved via HNSW)
    and computes other requested pairwise distances on-the-fly.
    """
    def __init__(self, records: list[Any], engine: Any, nearest_k: int, M: int = 100) -> None:
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
            
        self.spatial_index = SpatialIndex(vectors)

        self.nearest = np.zeros((self.n, nearest_k), dtype=int)
        self._cache: dict[tuple[int, int], float] = {}
        

        actual_M = min(M, self.n)
        

        for i in range(self.n):
            if actual_M <= 1:
                continue
                
            labels, _ = self.spatial_index.query(vectors[i], k=actual_M)
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
                    self._cache[(i, c_idx)] = float(dist)
                    self._cache[(c_idx, i)] = float(dist)
                        
    def __getitem__(self, key: Any) -> Any:
        import math
        if isinstance(key, tuple):
            row, col = key
            

            if isinstance(row, (int, np.integer)) and isinstance(col, (int, np.integer)):
                row_idx = int(row)
                col_idx = int(col)
                if row_idx == col_idx:
                    return 0.0
                if (row_idx, col_idx) in self._cache:
                    return self._cache[(row_idx, col_idx)]
                
                dist = float(self.engine.similarity_engine.calculate_distance(
                    self.records[row_idx].vector, self.records[col_idx].vector
                ))
                if not math.isfinite(dist):
                    dist = 1e9
                self._cache[(row_idx, col_idx)] = dist
                self._cache[(col_idx, row_idx)] = dist
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
