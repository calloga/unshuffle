from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

from unshuffle.logic.analysis.scan_discovery import discover_to_scan_store
from unshuffle.logic.analysis.scan_structure import (
    ROLE_CHILD_OF_DUPLICATE,
    ROLE_DUPLICATE,
    ROLE_LARGE,
    ROLE_LEAF,
    ROLE_PURE,
    ROLE_STANDARD,
    analyze_scan_structure,
)
from unshuffle.logic.analysis.service import AnalysisContext, build_node_graph
from unshuffle.persistence import UnshuffleDB


def test_db_structural_analysis_matches_legacy_graph_fields(tmp_path):
    source = tmp_path / "source"
    relative_files = [
        "Aden Pack/Aden Pack/Kicks/kick.wav",
        "Aden Pack/Aden Pack/Snares/snare.wav",
        "Large/One/Kicks/a.wav",
        "Large/Two/Kicks/b.wav",
        "Large/Three/Kicks/c.wav",
        "Large/Loose.wav",
        "CategoryOnly/Kicks/Brand Name/x.wav",
        "CategoryOnly/Snares/Other Brand/y.wav",
        "Identity/Identity/FX/identity_fx.wav",
        "Empty/filler/.keep",
    ]
    for relative in relative_files:
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"sample")

    with mock.patch(
        "unshuffle.logic.analysis.service.get_fast_hash",
        side_effect=lambda path: f"segmd5-v1:{path.as_posix()}",
    ):
        legacy = AnalysisContext(source)
        build_node_graph(source, legacy)

    db = UnshuffleDB(tmp_path / "structure.db")
    try:
        db.create_scan_run(
            scan_id="scan",
            session_id="session",
            target_root=tmp_path / "target",
            roots=[source],
        )
        discover_to_scan_store(db, "scan", source)
        analyze_scan_structure(db, "scan")

        for raw_row in db.conn.execute(
            "SELECT * FROM scan_directories WHERE scan_id = ? ORDER BY discovery_order",
            ("scan",),
        ):
            row = dict(raw_row)
            node = legacy.nodes[Path(row["normalized_path"])]
            flags = int(row["role_flags"] or 0)
            assert bool(flags & ROLE_LEAF) == (node.node_type.name == "LEAF")
            assert bool(flags & ROLE_PURE) == node.is_pure_container
            assert bool(flags & ROLE_DUPLICATE) == node.is_duplicate_container
            assert bool(flags & ROLE_CHILD_OF_DUPLICATE) == node.is_child_of_duplicate
            assert bool(flags & ROLE_LARGE) == node.is_large_container
            assert bool(flags & ROLE_STANDARD) == node.is_standard_container
            assert float(row["pack_weight"]) == node.pack_candidate_weight
            assert json.loads(row["weight_evidence_json"] or "{}") == node.weight_evidence
            descendants = [
                candidate
                for candidate in legacy.nodes.values()
                if candidate.path != node.path and node.path in candidate.path.parents
            ]
            assert int(row["descendant_count"]) == len(descendants)
            legacy_tokens = set(node.unweighted_tokens)
            for candidate in descendants:
                legacy_tokens.update(candidate.unweighted_tokens)
            assert set(json.loads(row["descendant_token_blob"] or "[]")) == legacy_tokens
            assert row["structure_state"] == "done"
    finally:
        db.close()
