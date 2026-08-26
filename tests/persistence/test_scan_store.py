from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from unshuffle.persistence import UnshuffleDB
from unshuffle.persistence.stores import staging_store
from unshuffle.persistence.schema.schema import migrations_up


def _directory_rows(count: int):
    for index in range(count):
        yield (
            index,
            index,
            index - 1 if index else None,
            index,
            Path(f"C:/Samples/dir-{index}"),
            f"dir-{index}",
            False,
            False,
        )


def _item_rows(count: int):
    for index in range(count):
        yield (
            index,
            index + 1,
            0,
            Path(f"C:/Samples/item-{index}.wav"),
            f"item-{index}.wav",
            ".wav",
            1000,
            123.0,
            123_000_000_000 + index,
            False,
            False,
            True,
        )


def test_scan_schema_and_state_transitions_are_additive(tmp_path):
    db = UnshuffleDB(tmp_path / "scan.db")
    try:
        tables = {
            str(row[0])
            for row in db.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        assert {"scan_runs", "scan_directories", "scan_items"} <= tables
        assert db.get_schema_version() >= 10
        index_columns = [
            str(row[2])
            for row in db.conn.execute(
                "PRAGMA index_info(idx_custom_tree_memberships_session_row)"
            )
        ]
        assert index_columns == ["session_id", "row_id"]

        db.create_scan_run(
            scan_id="scan-1",
            session_id="session-1",
            target_root=tmp_path,
            roots=[Path("C:/Samples")],
            versions={"hash": "segmd5-v1", "feature": "features-v1"},
        )
        assert db.insert_scan_directories("scan-1", _directory_rows(1), batch_size=1) == 1
        assert db.insert_scan_items("scan-1", _item_rows(5), batch_size=2) == 5
        assert db.count_scan_items("scan-1", "hash", "pending") == 5

        claimed = db.claim_scan_items(
            "scan-1",
            "hash",
            "worker-1",
            limit=2,
            columns="item_id, normalized_path, hash_state",
        )
        assert [row["item_id"] for row in claimed] == [0, 1]
        assert all(row["hash_state"] == "in_progress" for row in claimed)

        assert db.update_scan_items(
            "scan-1",
            [
                (0, {"hash_state": "done", "fast_hash": "segmd5-v1:a", "effective_hash": "segmd5-v1:a"}),
                (1, {"hash_state": "done", "fast_hash": "segmd5-v1:a", "effective_hash": "segmd5-v1:a"}),
            ],
        ) == 2
        assert db.count_scan_items("scan-1", "hash", "done") == 2
        assert db.update_scan_analysis_by_hash(
            "scan-1",
            [("segmd5-v1:a", "failed", "decode_error", "Corrupted", "ffmpeg failed to decode")],
        ) == 1
        error_rows = db.conn.execute(
            "SELECT analysis_error_code, analysis_error_text FROM scan_items WHERE scan_id = ? AND effective_hash = ?",
            ("scan-1", "segmd5-v1:a"),
        ).fetchall()
        assert [tuple(row) for row in error_rows] == [
            ("decode_error", "ffmpeg failed to decode"),
            ("decode_error", "ffmpeg failed to decode"),
        ]
        assert list(db.fast_hash_collision_groups("scan-1")) == [
            {"size": 1000, "fast_hash": "segmd5-v1:a", "item_count": 2},
        ]

        db.update_scan_run("scan-1", state="paused", phase="hash", completed_count=2)
        run = db.get_scan_run("scan-1")
        assert run is not None
        assert run["state"] == "paused"
        assert run["phase"] == "hash"
        assert run["completed_count"] == 2
        assert db.newest_resumable_scan(tmp_path)["scan_id"] == "scan-1"

        db.create_scan_run(
            scan_id="scan-1",
            session_id="session-1",
            target_root=tmp_path,
            roots=[Path("C:/Samples")],
        )
        assert db.get_scan_run("scan-1")["state"] == "running"
        assert db.update_session_scan_runs("session-1", state="ready", phase="ready") == 1
        assert db.get_scan_run("scan-1")["state"] == "ready"
        assert db.get_scan_run("scan-1")["phase"] == "ready"
    finally:
        db.close()


def test_classified_scan_stats_count_duplicate_shadows_as_their_own_category(tmp_path):
    db = UnshuffleDB(tmp_path / "scan-summary.db")
    try:
        db.create_scan_run(
            scan_id="scan",
            session_id="session",
            target_root=tmp_path,
            roots=[Path("C:/Samples")],
        )
        db.insert_scan_directories("scan", _directory_rows(1))
        db.insert_scan_items("scan", _item_rows(3))
        db.conn.execute(
            """
            UPDATE scan_items SET
                classification_state = 'done',
                category = CASE item_id WHEN 0 THEN 'Kicks' WHEN 1 THEN 'Snares' ELSE 'FX' END,
                effective_hash = CASE WHEN item_id < 2 THEN 'same-hash' ELSE 'unique-hash' END
            WHERE scan_id = 'scan'
            """
        )

        stats = db.classified_scan_session_stats("session")

        assert stats["total"] == 3
        assert stats["duplicates"] == 1
        assert stats["category_counts"] == {"Duplicates": 1, "FX": 1, "Kicks": 1}
    finally:
        db.close()


def test_scan_analysis_hash_update_does_not_mark_non_audio_with_same_hash(tmp_path):
    db = UnshuffleDB(tmp_path / "scan-analysis.db")
    try:
        db.create_scan_run(
            scan_id="scan-1",
            session_id="session-1",
            target_root=tmp_path,
            roots=[Path("C:/Samples")],
        )
        db.insert_scan_directories("scan-1", _directory_rows(1))
        rows = list(_item_rows(2))
        rows[1] = (*rows[1][:-1], False)
        assert db.insert_scan_items("scan-1", rows) == 2
        db.update_scan_items(
            "scan-1",
            [
                (0, {"effective_hash": "empty-hash"}),
                (1, {"effective_hash": "empty-hash"}),
            ],
        )

        db.update_scan_analysis_by_hash(
            "scan-1",
            [("empty-hash", "failed", "decode_error", "Corrupted", "decode failed")],
        )

        states = db.conn.execute(
            "SELECT analysis_status FROM scan_items WHERE scan_id = ? ORDER BY item_id",
            ("scan-1",),
        ).fetchall()
        assert [row[0] for row in states] == ["Corrupted", None]
    finally:
        db.close()


def test_version_nine_database_without_scan_tables_receives_additive_schema(tmp_path):
    import sqlite3

    path = tmp_path / "legacy-nine.db"
    conn = sqlite3.connect(path)
    try:
        migrations = Path(__file__).parents[2] / "unshuffle" / "persistence" / "migrations"
        for migration in sorted(migrations.glob("00[1-8]_*.sql")):
            conn.executescript(migration.read_text(encoding="utf-8"))
        conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        conn.execute("INSERT INTO schema_version VALUES (9)")
        migrations_up(conn)
        tables = {
            str(row[0])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        assert {"scan_runs", "scan_directories", "scan_items"} <= tables
        assert conn.execute("SELECT version FROM schema_version").fetchone()[0] >= 10
        index_columns = [
            str(row[2])
            for row in conn.execute(
                "PRAGMA index_info(idx_custom_tree_memberships_session_row)"
            )
        ]
        assert index_columns == ["session_id", "row_id"]
    finally:
        conn.close()


def test_compatible_resumable_session_requires_same_roots_and_versions(tmp_path, monkeypatch):
    from gui.core.workflow_scan_start import compatible_resumable_session_id
    import unshuffle.persistence

    target = tmp_path / "target"
    roots = [tmp_path / "A", tmp_path / "B"]
    db = UnshuffleDB(tmp_path / "resume-selection.db")
    for index, root in enumerate(roots):
        db.create_scan_run(
            scan_id=f"scan-{index}",
            session_id="resumable",
            target_root=target,
            roots=[root],
            versions={
                "hash": "segmd5-v1",
                "feature": "unshuffle-audio-v1",
                "taxonomy": "current",
                "classification": "current",
            },
        )
        db.update_scan_run(f"scan-{index}", state="paused")
    monkeypatch.setattr(unshuffle.persistence, "get_db", lambda _target: db)

    assert compatible_resumable_session_id(target, roots) == "resumable"

    mismatch_db = UnshuffleDB(tmp_path / "resume-mismatch.db")
    mismatch_db.create_scan_run(
        scan_id="scan",
        session_id="resumable",
        target_root=target,
        roots=[roots[0]],
        versions={"hash": "obsolete"},
    )
    mismatch_db.update_scan_run("scan", state="paused")
    monkeypatch.setattr(unshuffle.persistence, "get_db", lambda _target: mismatch_db)
    assert compatible_resumable_session_id(target, [roots[0]]) == ""


def test_stale_claims_are_released(tmp_path):
    db = UnshuffleDB(tmp_path / "stale.db")
    try:
        db.create_scan_run(
            scan_id="scan",
            session_id="session",
            target_root=tmp_path,
            roots=[Path("C:/Samples")],
        )
        db.insert_scan_directories("scan", _directory_rows(1))
        db.insert_scan_items("scan", _item_rows(1))
        assert db.claim_scan_items("scan", "analysis", "dead-worker", limit=1)
        old = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(timespec="seconds")
        db.conn.execute(
            "UPDATE scan_items SET claimed_at = ? WHERE scan_id = ?",
            (old, "scan"),
        )
        db.conn.commit()
        assert db.reset_stale_scan_claims("scan", "analysis", stale_after_seconds=60) == 1
        assert db.count_scan_items("scan", "analysis", "pending") == 1
    finally:
        db.close()


def test_collision_promotion_iterator_is_bounded(tmp_path):
    db = UnshuffleDB(tmp_path / "collisions.db")
    try:
        db.create_scan_run(
            scan_id="scan",
            session_id="session",
            target_root=tmp_path,
            roots=[Path("C:/Samples")],
        )
        db.insert_scan_directories("scan", _directory_rows(1))
        db.insert_scan_items("scan", _item_rows(5))
        db.update_scan_item_hashes(
            "scan",
            [
                (index, "segmd5-v1:same", "segmd5-v1:same", "fast_new")
                for index in range(5)
            ],
        )
        assert db.count_fast_hash_collision_items("scan") == 5
        batches = list(db.iter_fast_hash_collision_items("scan", batch_size=2))
        assert [len(batch) for batch in batches] == [2, 2, 1]
        assert db.finalize_fast_hashes("scan") == 5
        assert db.count_scan_items("scan", "hash", "done") == 5
    finally:
        db.close()


def test_classified_session_rows_stream_canonical_duplicate_shadow_fields(tmp_path):
    from gui.utils.state import iter_scan_item_staging_rows

    db = UnshuffleDB(tmp_path / "session-rows.db")
    try:
        db.create_scan_run(
            scan_id="scan",
            session_id="session",
            target_root=tmp_path,
            roots=[Path("C:/Samples")],
        )
        db.insert_scan_directories("scan", _directory_rows(1))
        db.insert_scan_items("scan", _item_rows(2))
        db.update_scan_item_hashes(
            "scan",
            [
                (0, "segmd5-v1:same", "full-same", "done"),
                (1, "segmd5-v1:same", "full-same", "done"),
            ],
        )
        db.update_scan_item_classifications_by_path(
            "scan",
            [
                {
                    "source_path": Path("C:/Samples/item-0.wav"),
                    "pack": "Canonical Pack",
                    "category": "Kicks",
                    "subcategory": "Punchy",
                    "audio_type": "Oneshots",
                    "confidence": "0.9",
                    "duration": 1.0,
                    "tags": '["120bpm"]',
                    "pack_candidates": "[]",
                    "evidence_json": '{"source": "canonical"}',
                    "analysis_tags_json": "[]",
                },
                {
                    "source_path": Path("C:/Samples/item-1.wav"),
                    "pack": "Other Pack",
                    "category": "FX",
                    "subcategory": "",
                    "audio_type": "Loops",
                    "confidence": "0.2",
                    "duration": 2.0,
                    "tags": '["c#"]',
                    "pack_candidates": "[]",
                    "evidence_json": '{"source": "shadow"}',
                    "analysis_tags_json": "[]",
                },
            ],
        )
        tuples = list(iter_scan_item_staging_rows(db.iter_classified_scan_session_items("session", batch_size=1)))
        assert len(tuples) == 2
        assert tuples[1][3:7] == ("Canonical Pack", "Kicks", "Punchy", "Oneshots")
        assert tuples[1][7] == "c# duplicate"
        evidence = json.loads(tuples[1][13])
        assert evidence["source"] == "shadow"
        assert evidence["duplicate_shadow"]["duplicate_of_hash"] == "full-same"
        assert evidence["duplicate_shadow"]["duplicate_of_path"].endswith("item-0.wav")
    finally:
        db.close()


def test_append_rows_inherit_existing_staging_canonical_without_hydrating_session(tmp_path):
    from gui.utils.state import iter_scan_item_staging_rows

    db = UnshuffleDB(tmp_path / "append.db")
    try:
        db.register_session("session", Path("C:/Old"), tmp_path / "target", "pending")
        db.add_staging_records_iter(
            "session",
            [(
                0, "C:/Old/original.wav", "original.wav", "Existing Pack", "Kicks", "Punchy",
                "Oneshots", "", "0.9", 1.0, "full-same", "segmd5-v1:same", "[]", "{}",
                None, None, None, "ok", "[]", None, False,
            )],
        )
        db.create_scan_run(
            scan_id="append",
            session_id="session",
            target_root=tmp_path / "target",
            roots=[Path("C:/New")],
            mode="append",
        )
        row = list(_item_rows(1))[0]
        new_path = Path("C:/New/copy.wav")
        db.insert_scan_directories("append", _directory_rows(1))
        db.insert_scan_items("append", [(*row[:3], new_path, new_path.name, *row[5:])])
        db.update_scan_item_hashes("append", [(0, "segmd5-v1:same", "full-same", "done")])
        db.update_scan_item_classifications_by_path(
            "append",
            [{
                "source_path": new_path,
                "pack": "New Pack",
                "category": "FX",
                "subcategory": "",
                "audio_type": "Loops",
                "confidence": "0.2",
                "duration": 2.0,
                "tags": "[]",
                "pack_candidates": "[]",
                "evidence_json": "{}",
                "analysis_status": "ok",
                "analysis_tags_json": "[]",
            }],
        )

        tuples = list(iter_scan_item_staging_rows(db.iter_classified_append_items("session", ["append"])))

        assert len(tuples) == 1
        assert tuples[0][3:7] == ("Existing Pack", "Kicks", "Punchy", "Oneshots")
        assert "duplicate" in tuples[0][7]
        assert json.loads(tuples[0][13])["duplicate_shadow"]["duplicate_of_path"] == "C:/Old/original.wav"
    finally:
        db.close()


def test_append_fast_hash_match_promotes_existing_and_new_before_dedupe(tmp_path):
    from unshuffle.logic.analysis.scan_hashing import promote_scan_against_staging

    old_path = tmp_path / "old.wav"
    new_path = tmp_path / "new.wav"
    old_path.write_bytes(b"old")
    new_path.write_bytes(b"new")
    db = UnshuffleDB(tmp_path / "append-collision.db")
    try:
        db.register_session("session", tmp_path, tmp_path / "target", "pending")
        db.add_staging_records_iter(
            "session",
            [(
                0, str(old_path), old_path.name, "Old", "Kicks", "", "Oneshots", "", "1.0",
                1.0, "segmd5-v1:same", "segmd5-v1:same", "[]", "{}", None, None, None,
                "ok", "[]", None, False,
            )],
        )
        db.create_scan_run(
            scan_id="append",
            session_id="session",
            target_root=tmp_path / "target",
            roots=[tmp_path],
            mode="append",
        )
        db.insert_scan_directories("append", _directory_rows(1))
        row = list(_item_rows(1))[0]
        db.insert_scan_items("append", [(*row[:3], new_path, new_path.name, *row[5:])])
        db.update_scan_item_hashes(
            "append",
            [(0, "segmd5-v1:same", "segmd5-v1:same", "done")],
        )
        with mock.patch(
            "unshuffle.logic.analysis.scan_hashing.get_file_hash",
            side_effect=lambda path: f"full-{Path(path).stem}",
        ):
            assert promote_scan_against_staging(db, "session", "append") == 2

        assert db.conn.execute(
            "SELECT hash FROM staging_records WHERE session_id = 'session'"
        ).fetchone()[0] == "full-old"
        assert db.conn.execute(
            "SELECT effective_hash FROM scan_items WHERE scan_id = 'append'"
        ).fetchone()[0] == "full-new"
    finally:
        db.close()


def test_session_wide_fast_hash_match_is_full_hash_confirmed_across_roots(tmp_path):
    from unshuffle.logic.analysis.scan_hashing import promote_session_fast_hash_collisions

    db = UnshuffleDB(tmp_path / "cross-root.db")
    try:
        for scan_id, root_name in (("scan-a", "A"), ("scan-b", "B")):
            db.create_scan_run(
                scan_id=scan_id,
                session_id="session",
                target_root=tmp_path,
                roots=[Path(f"C:/{root_name}")],
            )
            db.insert_scan_directories(scan_id, _directory_rows(1))
            row = list(_item_rows(1))[0]
            path = Path(f"C:/{root_name}/sample.wav")
            db.insert_scan_items(scan_id, [(*row[:3], path, path.name, *row[5:])])
            db.update_scan_item_hashes(
                scan_id,
                [(0, "segmd5-v1:same", "segmd5-v1:same", "done")],
            )
            db.update_scan_item_classifications_by_path(
                scan_id,
                [{
                    "source_path": path,
                    "pack": root_name,
                    "category": "Kicks",
                    "subcategory": "",
                    "audio_type": "Oneshots",
                    "confidence": "1.0",
                    "duration": 1.0,
                    "tags": "[]",
                    "pack_candidates": "[]",
                    "evidence_json": "{}",
                    "analysis_tags_json": "[]",
                }],
            )
        with mock.patch(
            "unshuffle.logic.analysis.scan_hashing.get_file_hash",
            side_effect=lambda path: f"full-{path.parent.name}",
        ):
            assert promote_session_fast_hash_collisions(db, "session") == 2
        hashes = [
            row[0]
            for row in db.conn.execute(
                "SELECT effective_hash FROM scan_items ORDER BY scan_id"
            )
        ]
        assert hashes == ["full-A", "full-B"]
        assert db.classified_scan_session_stats("session")["duplicates"] == 0
    finally:
        db.close()


def test_completed_hashes_are_reused_from_resumable_scan_manifest(tmp_path):
    from unshuffle.logic.planning.service import run_plan

    source = tmp_path / "source"
    source.mkdir()
    for index in range(3):
        (source / f"sample-{index}.txt").write_text(str(index), encoding="utf-8")
    db = UnshuffleDB(tmp_path / "resume.db")
    try:
        hash_value = lambda path: f"segmd5-v1:{path.name}"
        with mock.patch("unshuffle.logic.analysis.scan_hashing.get_fast_hash", side_effect=hash_value) as first_hash:
            first = run_plan(source, tmp_path / "target", session_id="resume", db=db)
        assert first_hash.call_count == 3

        with mock.patch("unshuffle.logic.analysis.scan_hashing.get_fast_hash") as second_hash:
            second = run_plan(source, tmp_path / "target", session_id="resume", db=db)
        second_hash.assert_not_called()
        assert [record.hash for record in second] == [record.hash for record in first]
        scan_id = "resume:" + hashlib.sha1(str(source.resolve()).encode()).hexdigest()[:12]
        assert db.count_scan_items(scan_id, "hash", "done") == 3
        assert db.count_scan_items(scan_id, "classification", "done") == 3
        persisted = [
            row
            for batch in db.iter_scan_items(
                scan_id,
                columns="normalized_path, pack, category, audio_type, confidence",
                where_sql="classification_state = 'done'",
            )
            for row in batch
        ]
        assert [(row["category"], row["audio_type"]) for row in persisted] == [
            (record.category, record.audio_type) for record in first
        ]
    finally:
        db.close()


def test_interrupted_hash_batch_persists_progress_and_resumes_pending_only(tmp_path):
    from unshuffle.logic.analysis.scan_discovery import discover_to_scan_store
    from unshuffle.logic.analysis.scan_hashing import hash_scan_items

    source = tmp_path / "source"
    source.mkdir()
    for index in range(8):
        (source / f"sample-{index}.txt").write_text(str(index), encoding="utf-8")
    db = UnshuffleDB(tmp_path / "interrupted.db")
    try:
        db.create_scan_run(
            scan_id="scan",
            session_id="session",
            target_root=tmp_path / "target",
            roots=[source],
        )
        discover_to_scan_store(db, "scan", source)
        hashed = []

        def fake_hash(path):
            hashed.append(Path(path).name)
            return f"segmd5-v1:{Path(path).stem:0>32}"[-42:]

        with mock.patch("unshuffle.logic.analysis.scan_hashing.get_fast_hash", side_effect=fake_hash), \
             mock.patch("unshuffle.logic.analysis.scan_hashing.max_scan_workers", return_value=1):
            hash_scan_items(db, "scan", is_interrupted=lambda: len(hashed) >= 3)

        completed = db.count_scan_items("scan", "hash", "fast_new") + db.count_scan_items("scan", "hash", "done")
        assert 0 < completed < 8
        assert db.get_scan_run("scan")["state"] == "paused"
        first_calls = len(hashed)

        db.create_scan_run(
            scan_id="scan",
            session_id="session",
            target_root=tmp_path / "target",
            roots=[source],
        )
        with mock.patch("unshuffle.logic.analysis.scan_hashing.get_fast_hash", side_effect=fake_hash), \
             mock.patch("unshuffle.logic.analysis.scan_hashing.max_scan_workers", return_value=1):
            hash_scan_items(db, "scan")

        assert len(hashed) - first_calls == 8 - completed
        assert db.count_scan_items("scan", "hash", "done") == 8
        assert db.get_scan_run("scan")["state"] == "running"
        assert db.get_scan_run("scan")["phase"] == "structure"
    finally:
        db.close()


def test_hash_scan_items_does_not_emit_empty_hashing_phase(tmp_path):
    from unshuffle.logic.analysis.scan_discovery import discover_to_scan_store
    from unshuffle.logic.analysis.scan_hashing import hash_scan_items

    source = tmp_path / "source"
    source.mkdir()
    (source / "sample.wav").write_bytes(b"sample")
    db = UnshuffleDB(tmp_path / "empty-hashing-progress.db")
    try:
        db.create_scan_run(
            scan_id="scan",
            session_id="session",
            target_root=tmp_path / "target",
            roots=[source],
        )
        discover_to_scan_store(db, "scan", source)
        with mock.patch(
            "unshuffle.logic.analysis.scan_hashing.get_fast_hash",
            return_value="segmd5-v1:" + "1" * 32,
        ):
            hash_scan_items(db, "scan")

        progress = []
        hash_scan_items(db, "scan", progress_callback=progress.append)

        assert not any(payload.get("phase") == "Hashing" for payload in progress)
    finally:
        db.close()


def test_discovery_flushes_parent_directories_before_large_file_batches(tmp_path):
    from unshuffle.logic.analysis.scan_discovery import discover_to_scan_store

    source = tmp_path / "wide"
    for directory_index in range(3):
        directory = source / f"pack-{directory_index}"
        directory.mkdir(parents=True)
        for file_index in range(750):
            (directory / f"sample-{file_index}.txt").touch()
    db = UnshuffleDB(tmp_path / "wide.db")
    try:
        db.create_scan_run(
            scan_id="wide",
            session_id="session",
            target_root=tmp_path / "target",
            roots=[source],
        )
        discover_to_scan_store(db, "wide", source)
        assert db.count_scan_items("wide") == 2250
        assert db.conn.execute(
            "SELECT COUNT(*) FROM scan_items WHERE scan_id = ? AND parent_directory_id IS NULL",
            ("wide",),
        ).fetchone()[0] == 0
    finally:
        db.close()


def test_resume_discovery_reuses_unchanged_manifest_and_rebuilds_changed_manifest(tmp_path):
    from unshuffle.logic.analysis.scan_discovery import discover_to_scan_store

    source = tmp_path / "source"
    source.mkdir()
    (source / "one.txt").write_text("one", encoding="utf-8")
    db = UnshuffleDB(tmp_path / "manifest.db")
    try:
        db.create_scan_run(
            scan_id="scan",
            session_id="session",
            target_root=tmp_path / "target",
            roots=[source],
        )
        assert discover_to_scan_store(db, "scan", source) == 2
        first_signature = db.get_scan_run("scan")["source_signature"]
        db.update_scan_run("scan", phase="hash")
        assert discover_to_scan_store(db, "scan", source) == 2
        assert db.get_scan_run("scan")["source_signature"] == first_signature

        (source / "two.txt").write_text("two", encoding="utf-8")
        assert discover_to_scan_store(db, "scan", source) == 3
        assert db.count_scan_items("scan") == 2
        assert db.get_scan_run("scan")["source_signature"] != first_signature
    finally:
        db.close()


def test_iterator_staging_insert_consumes_bounded_batches(tmp_path):
    db = UnshuffleDB(tmp_path / "staging.db")
    consumed = 0

    def rows():
        nonlocal consumed
        for index in range(7):
            consumed += 1
            yield (
                index,
                f"C:/Samples/{index}.wav",
                f"{index}.wav",
                "Pack",
                "Kicks",
                "",
                "Oneshots",
                "",
                "0.9",
                0.5,
                f"hash-{index}",
                None,
                "[]",
                "{}",
                None,
                None,
                None,
                None,
                None,
                None,
                False,
            )

    try:
        db.register_session("session", Path("C:/Samples"), tmp_path, "pending")
        inserted = staging_store.add_staging_records_iter(
            db.conn,
            "session",
            rows(),
            batch_size=3,
        )
        assert inserted == consumed == 7
        assert db.conn.execute("SELECT COUNT(*) FROM staging_records").fetchone()[0] == 7
    finally:
        db.close()
