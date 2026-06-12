from pathlib import Path
from unittest import mock

from unshuffle.logic.coherence.models import (
    ANCHOR_CANDIDATE,
    ANCHOR_IGNORED,
    ANCHOR_VERIFIED,
    AnchorProfile,
    CoherenceResult,
    RefinementCandidate,
    REFINEMENT_AUTO_STAGED,
)
from unshuffle.logic.coherence.anchor_profiles import build_anchor_payload, validate_anchor_payload
from unshuffle.core.features import CURRENT_FEATURE_SCHEMA, FEATURE_VECTOR_SIZE
from unshuffle.persistence import UnshuffleDB


def test_coherence_results_and_refinement_candidate_states_round_trip(tmp_path: Path):
    db = UnshuffleDB(tmp_path / "coherence.db")
    try:
        db.upsert_coherence_results(
            "s1",
            [
                CoherenceResult(
                    record_id="1",
                    category="Bass",
                    subcategory="Sub",
                    coherence_status="low_coherence",
                    coherence_score=0.2,
                    is_outlier=True,
                    review_reason="far from cluster",
                )
            ],
        )
        assert db.list_coherence_results("s1")[0]["coherence_status"] == "low_coherence"

        candidate = RefinementCandidate(
            candidate_id="c1",
            record_id="1",
            current_audio_type="Loops",
            current_category="Bass",
            current_subcategory="Sub",
            suggested_audio_type="Oneshots",
            suggested_category="Kicks",
            suggested_subcategory="Kick",
            evidence="neighbors fit kicks",
            confidence_score=0.8,
        )
        db.upsert_refinement_candidates("s1", [candidate])
        assert len(db.list_refinement_candidates("s1", "pending")) == 1
        assert db.count_refinement_candidates("s1", "pending") == 1
        assert db.list_refinement_candidates("s1", "pending")[0]["suggested_audio_type"] == "Oneshots"
        db.set_refinement_candidate_state("s1", ["c1"], "accepted")
        assert db.count_refinement_candidates("s1", "pending") == 0
        assert db.count_refinement_candidates("s1", "accepted") == 1
        assert db.list_refinement_candidates("s1", "accepted")[0]["candidate_id"] == "c1"
        db.upsert_refinement_candidates("s1", [candidate])
        assert db.list_refinement_candidates("s1", "accepted")[0]["candidate_id"] == "c1"
    finally:
        db.close()


def test_auto_staged_refinement_candidate_state_round_trips(tmp_path: Path):
    db = UnshuffleDB(tmp_path / "coherence_auto_staged.db")
    try:
        candidate = RefinementCandidate(
            candidate_id="c1",
            record_id="1",
            current_category="Uncategorized",
            current_subcategory="",
            suggested_category="Kicks",
            suggested_subcategory="Generic",
            evidence="neighbors fit kicks",
            confidence_score=0.8,
            state=REFINEMENT_AUTO_STAGED,
        )

        db.upsert_refinement_candidates("s1", [candidate])

        rows = db.list_refinement_candidates("s1", REFINEMENT_AUTO_STAGED)
        assert len(rows) == 1
        assert rows[0]["suggested_category"] == "Kicks"
    finally:
        db.close()


def test_coherence_audit_persistence_rolls_back_as_one_unit(tmp_path: Path):
    db = UnshuffleDB(tmp_path / "coherence_atomic.db")
    try:
        result = CoherenceResult(
            record_id="1",
            category="Bass",
            subcategory="Sub",
            coherence_status="low_coherence",
            coherence_score=0.2,
            is_outlier=True,
        )

        with mock.patch(
            "unshuffle.persistence.storage.coherence_store.upsert_refinement_candidates",
            side_effect=RuntimeError("forced failure"),
        ):
            try:
                db.upsert_coherence_audit("s1", [result], [], [])
            except RuntimeError:
                pass
            else:
                raise AssertionError("expected forced failure")

        assert db.list_coherence_results("s1") == []
    finally:
        db.close()


def test_coherence_review_decisions_round_trip_by_path_and_hash(tmp_path: Path):
    db = UnshuffleDB(tmp_path / "coherence_review_decisions.db")
    try:
        db.upsert_coherence_review_decisions(
            "s1",
            [
                {
                    "source_path": "D:/Samples/Pack/kick.wav",
                    "file_hash": "hash-kick",
                    "decision_type": "target",
                    "current_audio_type": "Loops",
                    "current_category": "Percussion",
                    "current_subcategory": "",
                    "target_audio_type": "Oneshots",
                    "target_category": "Kicks",
                    "target_subcategory": "Generic",
                }
            ],
        )

        by_path = db.list_coherence_review_decisions(source_paths=["D:/Samples/Pack/kick.wav"])
        by_hash = db.list_coherence_review_decisions(file_hashes=["hash-kick"])

        assert len(by_path) == 1
        assert by_path[0]["target_category"] == "Kicks"
        assert by_path[0]["created_session_id"] == "s1"
        assert by_hash[0]["source_path"] == "D:/Samples/Pack/kick.wav"
    finally:
        db.close()


def test_coherence_review_decision_lookup_chunks_large_inputs(tmp_path: Path):
    db = UnshuffleDB(tmp_path / "coherence_review_decision_chunks.db")
    try:
        db.upsert_coherence_review_decisions(
            "s1",
            [
                {
                    "source_path": "D:/Samples/Pack/target.wav",
                    "file_hash": "hash-target",
                    "decision_type": "target",
                    "target_audio_type": "Oneshots",
                    "target_category": "Kicks",
                    "target_subcategory": "",
                }
            ],
        )
        paths = [f"D:/Samples/Pack/file_{idx}.wav" for idx in range(1200)]
        hashes = [f"hash-{idx}" for idx in range(1200)]
        paths.append("D:/Samples/Pack/target.wav")

        rows = db.list_coherence_review_decisions(source_paths=paths, file_hashes=hashes)

        assert len(rows) == 1
        assert rows[0]["target_category"] == "Kicks"
    finally:
        db.close()


def test_anchor_payload_privacy_validation_rejects_private_fields():
    vector = [0.1] * FEATURE_VECTOR_SIZE
    payload = build_anchor_payload(
        cluster_id="bass_sub_001",
        category="Bass",
        subcategory="Sub",
        medoid_vector=vector,
        cluster_centroid=vector,
        cluster_std=[0.01] * FEATURE_VECTOR_SIZE,
        coherence_radius=0.2,
        n_reference_items=5,
    )
    ok, reason = validate_anchor_payload(payload, {"Bass"})
    assert ok, reason

    payload["source_path"] = "D:/Private/file.wav"
    ok, reason = validate_anchor_payload(payload, {"Bass"})
    assert not ok
    assert "private metadata" in reason


def test_anchor_payload_validation_requires_current_schema_and_required_vectors():
    vector = [0.1] * FEATURE_VECTOR_SIZE
    payload = build_anchor_payload(
        cluster_id="bass_sub_001",
        category="Bass",
        subcategory="Sub",
        medoid_vector=vector,
        cluster_centroid=vector,
        cluster_std=[0.01] * FEATURE_VECTOR_SIZE,
        coherence_radius=0.2,
        n_reference_items=5,
    )

    missing_id = dict(payload)
    missing_id.pop("anchor_id")
    ok, reason = validate_anchor_payload(missing_id, {"Bass"})
    assert not ok
    assert "anchor_id" in reason

    old_schema = dict(payload)
    old_schema["features"] = dict(payload["features"])
    old_schema["features"]["vector_schema"] = ["old"]
    ok, reason = validate_anchor_payload(old_schema, {"Bass"})
    assert not ok
    assert "schema" in reason

    missing_std = dict(payload)
    missing_std["features"] = dict(payload["features"])
    missing_std["features"].pop("cluster_std")
    ok, reason = validate_anchor_payload(missing_std, {"Bass"})
    assert not ok
    assert "vector" in reason


def test_anchor_profile_audio_type_round_trips(tmp_path: Path):
    db = UnshuffleDB(tmp_path / "anchors.db")
    try:
        vector = [0.1] * FEATURE_VECTOR_SIZE
        anchor = AnchorProfile(
            anchor_id="anchor-loops",
            audio_type="Loops",
            category="Bass",
            subcategory="Sub",
            cluster_id="loops_bass_sub_000",
            feature_space_version="test",
            extractor_version="test",
            vector_schema=CURRENT_FEATURE_SCHEMA,
            medoid_vector=vector,
            cluster_centroid=vector,
            cluster_std=[0.01] * FEATURE_VECTOR_SIZE,
            coherence_radius=0.2,
            n_reference_items=8,
            state=ANCHOR_CANDIDATE,
            profile_payload={"audio_type": "Loops"},
        )

        db.upsert_anchor_candidates("s1", [anchor])

        rows = db.list_anchor_candidates("s1", ANCHOR_CANDIDATE)
        assert rows[0]["audio_type"] == "Loops"
    finally:
        db.close()


def test_upsert_anchor_profiles_preserves_candidate_rows(tmp_path: Path):
    db = UnshuffleDB(tmp_path / "anchors_import.db")
    try:
        vector = [0.1] * FEATURE_VECTOR_SIZE
        candidate = AnchorProfile(
            anchor_id="anchor-candidate",
            audio_type="Oneshots",
            category="Bass",
            subcategory="Sub",
            cluster_id="candidate_cluster",
            feature_space_version="test",
            extractor_version="test",
            vector_schema=CURRENT_FEATURE_SCHEMA,
            medoid_vector=vector,
            cluster_centroid=vector,
            cluster_std=[0.01] * FEATURE_VECTOR_SIZE,
            coherence_radius=0.2,
            n_reference_items=8,
            state=ANCHOR_CANDIDATE,
            profile_payload={"anchor_id": "anchor-candidate"},
        )
        imported = AnchorProfile(
            anchor_id="anchor-imported",
            audio_type="Oneshots",
            category="Kicks",
            subcategory="Punchy",
            cluster_id="imported_cluster",
            feature_space_version="test",
            extractor_version="test",
            vector_schema=CURRENT_FEATURE_SCHEMA,
            medoid_vector=vector,
            cluster_centroid=vector,
            cluster_std=[0.01] * FEATURE_VECTOR_SIZE,
            coherence_radius=0.2,
            n_reference_items=8,
            state=ANCHOR_VERIFIED,
            profile_payload={"anchor_id": "anchor-imported"},
        )

        db.upsert_anchor_candidates("s1", [candidate])
        db.upsert_anchor_profiles("s1", [imported])

        assert [row["anchor_id"] for row in db.list_anchor_candidates("s1", ANCHOR_CANDIDATE)] == ["anchor-candidate"]
        assert [row["anchor_id"] for row in db.list_anchor_candidates("s1", ANCHOR_VERIFIED)] == ["anchor-imported"]
    finally:
        db.close()


def test_generated_anchor_candidate_does_not_demote_verified_anchor(tmp_path: Path):
    db = UnshuffleDB(tmp_path / "anchors_verified_preserve.db")
    try:
        vector = [0.1] * FEATURE_VECTOR_SIZE
        verified = AnchorProfile(
            anchor_id="anchor-shared",
            audio_type="Oneshots",
            category="Bass",
            subcategory="Sub",
            cluster_id="shared_cluster",
            feature_space_version="test",
            extractor_version="test",
            vector_schema=CURRENT_FEATURE_SCHEMA,
            medoid_vector=vector,
            cluster_centroid=vector,
            cluster_std=[0.01] * FEATURE_VECTOR_SIZE,
            coherence_radius=0.2,
            n_reference_items=8,
            state=ANCHOR_VERIFIED,
            profile_payload={"anchor_id": "anchor-shared"},
        )
        regenerated = AnchorProfile(
            anchor_id="anchor-shared",
            audio_type="Oneshots",
            category="Bass",
            subcategory="Sub",
            cluster_id="shared_cluster",
            feature_space_version="test",
            extractor_version="test",
            vector_schema=CURRENT_FEATURE_SCHEMA,
            medoid_vector=vector,
            cluster_centroid=vector,
            cluster_std=[0.01] * FEATURE_VECTOR_SIZE,
            coherence_radius=0.3,
            n_reference_items=9,
            state=ANCHOR_CANDIDATE,
            profile_payload={"anchor_id": "anchor-shared"},
        )

        db.upsert_anchor_profiles("s1", [verified])
        db.upsert_anchor_candidates("s1", [regenerated])

        assert db.list_anchor_candidates("s1", ANCHOR_CANDIDATE) == []
        rows = db.list_anchor_candidates("s1", ANCHOR_VERIFIED)
        assert len(rows) == 1
        assert rows[0]["anchor_id"] == "anchor-shared"
        assert rows[0]["n_reference_items"] == 9
    finally:
        db.close()


def test_clear_staging_preserves_verified_anchors(tmp_path: Path):
    db = UnshuffleDB(tmp_path / "anchors_clear.db")
    try:
        vector = [0.1] * FEATURE_VECTOR_SIZE
        verified = AnchorProfile(
            anchor_id="anchor-verified",
            audio_type="Oneshots",
            category="Bass",
            subcategory="Sub",
            cluster_id="verified_cluster",
            feature_space_version="test",
            extractor_version="test",
            vector_schema=CURRENT_FEATURE_SCHEMA,
            medoid_vector=vector,
            cluster_centroid=vector,
            cluster_std=[0.01] * FEATURE_VECTOR_SIZE,
            coherence_radius=0.2,
            n_reference_items=8,
            state=ANCHOR_VERIFIED,
            profile_payload={"anchor_id": "anchor-verified"},
        )
        candidate = AnchorProfile(
            anchor_id="anchor-candidate",
            audio_type="Oneshots",
            category="Bass",
            subcategory="Sub",
            cluster_id="candidate_cluster",
            feature_space_version="test",
            extractor_version="test",
            vector_schema=CURRENT_FEATURE_SCHEMA,
            medoid_vector=vector,
            cluster_centroid=vector,
            cluster_std=[0.01] * FEATURE_VECTOR_SIZE,
            coherence_radius=0.2,
            n_reference_items=8,
            state=ANCHOR_CANDIDATE,
            profile_payload={"anchor_id": "anchor-candidate"},
        )

        db.upsert_anchor_profiles("s1", [verified])
        db.upsert_anchor_candidates("s1", [candidate])
        db.clear_staging()

        assert [row["anchor_id"] for row in db.list_anchor_candidates("s1", ANCHOR_VERIFIED)] == ["anchor-verified"]
        assert db.list_anchor_candidates("s1", ANCHOR_CANDIDATE) == []
    finally:
        db.close()


def test_clear_staging_preserves_system_anchors(tmp_path: Path):
    db = UnshuffleDB(tmp_path / "system_anchors_clear.db")
    try:
        db.conn.execute(
            """
            INSERT INTO anchor_profiles (session_id, anchor_id, state)
            VALUES (?, ?, ?)
            """,
            ("__system__", "system-anchor", "system"),
        )
        db.conn.execute(
            """
            INSERT INTO anchor_profiles (session_id, anchor_id, state)
            VALUES (?, ?, ?)
            """,
            ("s1", "candidate-anchor", "candidate"),
        )
        db.conn.commit()

        db.clear_staging()

        rows = db.conn.execute("SELECT anchor_id, state FROM anchor_profiles").fetchall()
        states_by_id = {row["anchor_id"]: row["state"] for row in rows}
        assert states_by_id["system-anchor"] == "system"
        assert "candidate-anchor" not in states_by_id
    finally:
        db.close()


def test_verified_anchors_copy_forward_to_new_session(tmp_path: Path):
    db = UnshuffleDB(tmp_path / "anchors_copy_forward.db")
    try:
        vector = [0.1] * FEATURE_VECTOR_SIZE
        verified = AnchorProfile(
            anchor_id="anchor-verified",
            audio_type="Oneshots",
            category="Bass",
            subcategory="Sub",
            cluster_id="verified_cluster",
            feature_space_version="test",
            extractor_version="test",
            vector_schema=CURRENT_FEATURE_SCHEMA,
            medoid_vector=vector,
            cluster_centroid=vector,
            cluster_std=[0.01] * FEATURE_VECTOR_SIZE,
            coherence_radius=0.2,
            n_reference_items=8,
            state=ANCHOR_VERIFIED,
            profile_payload={"anchor_id": "anchor-verified"},
        )

        db.upsert_anchor_profiles("old-session", [verified])

        from unshuffle.persistence.system_anchor_loader import load_system_anchors
        num_system = len(load_system_anchors())

        copied = db.ensure_verified_anchors_for_session("new-session")
        second_copy = db.ensure_verified_anchors_for_session("new-session")

        assert copied == 1 + num_system
        assert second_copy == 0
        rows = db.list_anchor_candidates("new-session", ANCHOR_VERIFIED)
        assert len(rows) == 1
        assert rows[0]["anchor_id"] == "anchor-verified"
        assert rows[0]["category"] == "Bass"
    finally:
        db.close()


def test_removed_verified_anchor_does_not_copy_forward_to_new_scan_session(tmp_path: Path):
    db = UnshuffleDB(tmp_path / "anchors_removed_copy_forward.db")
    try:
        vector = [0.1] * FEATURE_VECTOR_SIZE
        verified = AnchorProfile(
            anchor_id="anchor-removed",
            audio_type="Oneshots",
            category="Bass",
            subcategory="Sub",
            cluster_id="verified_cluster",
            feature_space_version="test",
            extractor_version="test",
            vector_schema=CURRENT_FEATURE_SCHEMA,
            medoid_vector=vector,
            cluster_centroid=vector,
            cluster_std=[0.01] * FEATURE_VECTOR_SIZE,
            coherence_radius=0.2,
            n_reference_items=8,
            state=ANCHOR_VERIFIED,
            profile_payload={"anchor_id": "anchor-removed"},
        )

        db.upsert_anchor_profiles("old-session", [verified])
        db.ensure_verified_anchors_for_session("scan-session")

        assert [row["anchor_id"] for row in db.list_anchor_candidates("scan-session", ANCHOR_VERIFIED)] == ["anchor-removed"]

        db.remove_verified_anchor_profiles("scan-session", ["anchor-removed"])
        db.ensure_verified_anchors_for_session("next-scan-session")

        assert db.list_anchor_candidates("next-scan-session", ANCHOR_VERIFIED) == []
        ignored = db.list_anchor_candidates("scan-session", ANCHOR_IGNORED)
        assert [row["anchor_id"] for row in ignored] == ["anchor-removed"]
        tombstone = db.list_anchor_candidates("__removed_verified_anchors__", ANCHOR_IGNORED)
        assert [row["anchor_id"] for row in tombstone] == ["anchor-removed"]
    finally:
        db.close()


def test_ignored_candidate_does_not_block_verified_anchor_copy_forward(tmp_path: Path):
    db = UnshuffleDB(tmp_path / "anchors_candidate_ignore_does_not_block.db")
    try:
        vector = [0.1] * FEATURE_VECTOR_SIZE
        verified = AnchorProfile(
            anchor_id="anchor-shared",
            audio_type="Oneshots",
            category="Bass",
            subcategory="Sub",
            cluster_id="verified_cluster",
            feature_space_version="test",
            extractor_version="test",
            vector_schema=CURRENT_FEATURE_SCHEMA,
            medoid_vector=vector,
            cluster_centroid=vector,
            cluster_std=[0.01] * FEATURE_VECTOR_SIZE,
            coherence_radius=0.2,
            n_reference_items=8,
            state=ANCHOR_VERIFIED,
            profile_payload={"anchor_id": "anchor-shared"},
        )

        db.upsert_anchor_profiles("old-session", [verified])
        db.conn.execute(
            "INSERT INTO anchor_profiles (session_id, anchor_id, state) VALUES (?, ?, ?)",
            ("review-session", "anchor-shared", ANCHOR_IGNORED),
        )
        db.conn.commit()

        db.ensure_verified_anchors_for_session("new-session")

        rows = db.list_anchor_candidates("new-session", ANCHOR_VERIFIED)
        assert [row["anchor_id"] for row in rows] == ["anchor-shared"]
    finally:
        db.close()


def test_removed_verified_anchor_tombstone_survives_clear_staging(tmp_path: Path):
    db = UnshuffleDB(tmp_path / "anchors_removed_clear_staging.db")
    try:
        db.conn.execute(
            "INSERT INTO anchor_profiles (session_id, anchor_id, state) VALUES (?, ?, ?)",
            ("__removed_verified_anchors__", "anchor-removed", ANCHOR_IGNORED),
        )
        db.conn.execute(
            "INSERT INTO anchor_profiles (session_id, anchor_id, state) VALUES (?, ?, ?)",
            ("scan-session", "candidate-anchor", ANCHOR_CANDIDATE),
        )
        db.conn.commit()

        db.clear_staging()

        tombstone = db.list_anchor_candidates("__removed_verified_anchors__", ANCHOR_IGNORED)
        assert [row["anchor_id"] for row in tombstone] == ["anchor-removed"]
        assert db.list_anchor_candidates("scan-session", ANCHOR_CANDIDATE) == []
    finally:
        db.close()


def test_removed_verified_anchor_does_not_delete_system_anchor_source(tmp_path: Path):
    db = UnshuffleDB(tmp_path / "anchors_removed_system_source.db")
    try:
        db.conn.execute(
            "INSERT INTO anchor_profiles (session_id, anchor_id, state) VALUES (?, ?, ?)",
            ("__system__", "system-anchor", "system"),
        )
        db.conn.execute(
            "INSERT INTO anchor_profiles (session_id, anchor_id, state) VALUES (?, ?, ?)",
            ("scan-session", "system-anchor", "verified"),
        )
        db.conn.commit()

        db.remove_verified_anchor_profiles("scan-session", ["system-anchor"])

        system_row = db.conn.execute(
            "SELECT state FROM anchor_profiles WHERE session_id = '__system__' AND anchor_id = 'system-anchor'",
        ).fetchone()
        assert system_row["state"] == "system"
        assert db.list_anchor_candidates("scan-session", ANCHOR_VERIFIED) == []
    finally:
        db.close()


def test_verified_anchor_copy_forward_upgrades_existing_candidate(tmp_path: Path):
    db = UnshuffleDB(tmp_path / "anchors_copy_existing_candidate.db")
    try:
        vector = [0.1] * FEATURE_VECTOR_SIZE
        verified = AnchorProfile(
            anchor_id="anchor-shared",
            audio_type="Oneshots",
            category="Bass",
            subcategory="Sub",
            cluster_id="verified_cluster",
            feature_space_version="test",
            extractor_version="test",
            vector_schema=CURRENT_FEATURE_SCHEMA,
            medoid_vector=vector,
            cluster_centroid=vector,
            cluster_std=[0.01] * FEATURE_VECTOR_SIZE,
            coherence_radius=0.2,
            n_reference_items=8,
            state=ANCHOR_VERIFIED,
            profile_payload={"anchor_id": "anchor-shared"},
        )
        candidate = AnchorProfile(
            anchor_id="anchor-shared",
            audio_type="Oneshots",
            category="Bass",
            subcategory="Sub",
            cluster_id="candidate_cluster",
            feature_space_version="test",
            extractor_version="test",
            vector_schema=CURRENT_FEATURE_SCHEMA,
            medoid_vector=vector,
            cluster_centroid=vector,
            cluster_std=[0.01] * FEATURE_VECTOR_SIZE,
            coherence_radius=0.4,
            n_reference_items=10,
            state=ANCHOR_CANDIDATE,
            profile_payload={"anchor_id": "anchor-shared"},
        )

        db.upsert_anchor_profiles("old-session", [verified])
        db.upsert_anchor_candidates("new-session", [candidate])

        from unshuffle.persistence.system_anchor_loader import load_system_anchors
        num_system = len(load_system_anchors())

        copied = db.ensure_verified_anchors_for_session("new-session")

        assert copied == 1 + num_system
        assert db.list_anchor_candidates("new-session", ANCHOR_CANDIDATE) == []
        rows = db.list_anchor_candidates("new-session", ANCHOR_VERIFIED)
        assert len(rows) == 1
        assert rows[0]["anchor_id"] == "anchor-shared"
        assert rows[0]["cluster_id"] == "verified_cluster"
    finally:
        db.close()


def test_anchor_profile_rows_round_trip_preserves_verified_state(tmp_path: Path):
    db = UnshuffleDB(tmp_path / "anchors_rows.db")
    try:
        vector = [0.1] * FEATURE_VECTOR_SIZE
        verified = AnchorProfile(
            anchor_id="anchor-verified",
            audio_type="Oneshots",
            category="Bass",
            subcategory="Sub",
            cluster_id="verified_cluster",
            feature_space_version="test",
            extractor_version="test",
            vector_schema=CURRENT_FEATURE_SCHEMA,
            medoid_vector=vector,
            cluster_centroid=vector,
            cluster_std=[0.01] * FEATURE_VECTOR_SIZE,
            coherence_radius=0.2,
            n_reference_items=8,
            state=ANCHOR_VERIFIED,
            profile_payload={"anchor_id": "anchor-verified"},
        )

        db.upsert_anchor_profiles("source-session", [verified])
        rows = db.list_anchor_candidates("source-session")
        db.upsert_anchor_profile_rows("target-session", rows)

        copied = db.list_anchor_candidates("target-session", ANCHOR_VERIFIED)
        assert len(copied) == 1
        assert copied[0]["anchor_id"] == "anchor-verified"
    finally:
        db.close()


def test_system_anchors_lifecycle(tmp_path: Path):
    db = UnshuffleDB(tmp_path / "system_anchors_test.db")
    try:
        from unshuffle.persistence.system_anchor_loader import load_system_anchors
        system_rows = load_system_anchors()
        # Seed a dummy system anchor if loader returned empty list (e.g. in test env)
        if not system_rows:
            system_rows = [{
                "anchor_id": "system-test-anchor",
                "audio_type": "Oneshots",
                "category": "Bass",
                "subcategory": "Sub",
                "cluster_id": "system_cluster",
                "feature_space_version": "test",
                "extractor_version": "test",
                "feature_schema_json": "[]",
                "medoid_vector": [0.1] * FEATURE_VECTOR_SIZE,
                "cluster_centroid": [0.1] * FEATURE_VECTOR_SIZE,
                "cluster_std": [0.01] * FEATURE_VECTOR_SIZE,
                "coherence_radius": 0.2,
                "n_reference_items": 8,
                "state": "system",
                "profile_json": "{}",
            }]

        db.seed_system_anchors(system_rows)

        # Assert seeded anchors are in the __system__ session with 'system' state
        seeded = db.conn.execute(
            "SELECT anchor_id, state FROM anchor_profiles WHERE session_id = '__system__'"
        ).fetchall()
        assert len(seeded) == len(system_rows)
        assert seeded[0]["state"] == "system"

        # Call ensure_verified_anchors_for_session for a new session
        copied = db.ensure_verified_anchors_for_session("new-session")
        assert copied == len(system_rows)

        # Assert they are copied in the new session with 'system' state
        in_new_session = db.conn.execute(
            "SELECT anchor_id, state FROM anchor_profiles WHERE session_id = 'new-session'"
        ).fetchall()
        assert len(in_new_session) == len(system_rows)
        assert in_new_session[0]["state"] == "system"

        # Assert they are NOT visible as verified candidates
        verified_candidates = db.list_anchor_candidates("new-session", ANCHOR_VERIFIED)
        assert len(verified_candidates) == 0

        # Assert prune_ephemeral_state does not delete system anchors or __system__ rows
        # Register a dummy session so it has something to prune
        db.register_session("old-ephemeral-session", Path("d:/old"), Path("d:/old_tgt"), "restore")
        db.register_session("new-session", Path("d:/new"), Path("d:/new_tgt"), "restore")
        
        # Add some candidate anchor profile to prune in old session
        db.conn.execute(
            "INSERT INTO anchor_profiles (session_id, anchor_id, state) VALUES ('old-ephemeral-session', 'candidate-to-prune', 'candidate')"
        )

        db.prune_ephemeral_state(keep_session_ids=["new-session"])

        # Check that '__system__' and 'new-session' anchor profiles are preserved
        assert db.conn.execute(
            "SELECT COUNT(1) FROM anchor_profiles WHERE session_id = '__system__'"
        ).fetchone()[0] == len(system_rows)
        
        assert db.conn.execute(
            "SELECT COUNT(1) FROM anchor_profiles WHERE session_id = 'new-session'"
        ).fetchone()[0] == len(system_rows)

        # Check that the candidate row in the old session was deleted
        assert db.conn.execute(
            "SELECT COUNT(1) FROM anchor_profiles WHERE session_id = 'old-ephemeral-session'"
        ).fetchone()[0] == 0
    finally:
        db.close()
