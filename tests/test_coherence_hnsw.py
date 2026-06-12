import pytest
import numpy as np
import struct
from unshuffle.core.features import FEATURE_VECTOR_SIZE
from unshuffle.logic.coherence import CoherenceEngine
from unshuffle.logic.coherence.models import CoherenceRecord

def _vec(x):
    base = [float(x), 0.2, 0.2, 0.1, 0.1, *([0.0] * 12), 0.5]
    return base + [0.0] * (FEATURE_VECTOR_SIZE - len(base))

def _record(record_id, x, *, category="Bass", subcategory="Sub", audio_type="Oneshots"):
    return CoherenceRecord(
        record_id=str(record_id),
        category=category,
        subcategory=subcategory,
        vector=_vec(x),
        classification_confidence=0.8,
        audio_type=audio_type,
    )

def test_coherence_engine_hnsw_large_scale():
    # 1. Generate 3100 synthetic records to trigger the HNSW path (>= 3000)
    records = []
    for i in range(3100):
        # Slightly vary vectors
        val = i / 3100.0
        records.append(_record(i, val))
        
    engine = CoherenceEngine()
    
    # 2. Audit the records (this will construct and query the SparsePairwiseDistances with HNSW)
    results, candidates = engine.audit(records)
    
    # 3. Verify we have results for all records
    assert len(results) == 3100
    
    # 4. Verify that global neighbors also works at this scale
    query_record = records[0]
    neighbors = engine._global_neighbors_vectorized(query_record, records, limit=5)
    
    assert neighbors is not None
    assert len(neighbors) == 5
    # The closest records should have similar values (i % 10 == 0)
    for neighbor, dist in neighbors:
        assert neighbor.record_id != query_record.record_id
        assert dist >= 0
