from __future__ import annotations

import os
from pathlib import Path
import stat
from types import SimpleNamespace
from unittest import mock

import pytest

from unshuffle.logic.execution.service import ExecutionMixin
from unshuffle.logic.analysis.service import AnalysisContext, build_node_graph
from unshuffle.persistence import UnshuffleDB
from unshuffle.runtime.engine import RuntimeUnshuffler
from unshuffle.runtime.cache import CacheMixin
from unshuffle.core.hashing import get_file_hash
from unshuffle.core.paths import DB_FILE_NAME, get_local_system_dir


class _ExecutionHarness(ExecutionMixin):
    def __init__(self, target_dir: Path, session_id: str = "safety-session") -> None:
        self.target_dir = target_dir
        self.session_id = session_id
        self.session_source_roots: list[Path] = []
        self.moved_preserved_roots: set[Path] = set()
        self.prefix_map = {}
        self.seen_hashes = {}
        self.interrupted = False
        self.logs: list[str] = []

    def log(self, message: str, **_kwargs) -> None:
        self.logs.append(message)


class _UnsafeResolver:
    def __init__(self, outside_path: Path) -> None:
        self.outside_path = outside_path

    def resolve(self, *_args, **_kwargs):
        return SimpleNamespace(
            dest_path=self.outside_path,
            dest_folder=self.outside_path.parent,
            final_name=self.outside_path.name,
            relative_path=Path("..") / self.outside_path.name,
            used_custom_tree=False,
        )


class _UndoDB:
    def __init__(self, records: list[dict], target_dir: Path, *, mode: str = "copy", source_roots: list[Path] | None = None) -> None:
        self.records = records
        self.target_dir = target_dir
        self.mode = mode
        self.source_roots = list(source_roots or [])
        self.deleted: list[str] = []
        self.marked_undone: list[str] = []
        self.removed_cache_paths: list[list[str]] = []

    def get_session_records(self, session_id: str):
        return self.records

    def get_session(self, session_id: str):
        return {"session_id": session_id, "target_root": str(self.target_dir), "mode": self.mode}

    def get_session_sources(self, session_id: str):
        return [str(root) for root in self.source_roots]

    def remove_from_cache_by_paths(self, paths):
        self.removed_cache_paths.append(list(paths))

    def delete_session(self, session_id: str):
        self.deleted.append(session_id)
        self.records = []

    def mark_session_undone(self, session_id: str):
        self.marked_undone.append(session_id)
        for record in self.records:
            if record.get("status") in {"copied", "duplicate"} and record.get("step_status") in {None, "COMMITTED"}:
                record["step_status"] = "UNDONE"

    def get_all_hashes(self):
        return {}


def _record(source_path: Path, **overrides):
    defaults = {
        "audio_type": "Oneshots",
        "category": "Kicks",
        "subcategory": "",
        "pack": "Pack",
        "source_path": source_path,
        "hash": None,
        "is_preserved": False,
        "preserved_root": None,
        "confidence": 1.0,
        "tags": [],
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _runtime_for_undo(target: Path, db: _UndoDB):
    engine = RuntimeUnshuffler.__new__(RuntimeUnshuffler)
    engine.target_dir = target
    engine.db = db  # type: ignore
    engine.local_db = db  # type: ignore
    engine.interrupted = False
    engine.progress_callback = None
    engine.logs = []
    engine.seen_hashes = {}
    engine.log = lambda message, **_kwargs: engine.logs.append(str(message))  # type: ignore
    return engine


def _runtime_for_split_undo_db(target: Path, global_db: _UndoDB | UnshuffleDB, local_db: _UndoDB | UnshuffleDB):
    engine = RuntimeUnshuffler.__new__(RuntimeUnshuffler)
    engine.target_dir = target
    engine.db = global_db  # type: ignore
    engine.local_db = local_db  # type: ignore
    engine.interrupted = False
    engine.progress_callback = None
    engine.logs = []
    engine.seen_hashes = {}
    engine.log = lambda message, **_kwargs: engine.logs.append(str(message))  # type: ignore
    return engine


def _runtime_for_real_undo_db(target: Path, db: UnshuffleDB):
    engine = RuntimeUnshuffler.__new__(RuntimeUnshuffler)
    engine.target_dir = target
    engine.db = db
    engine.local_db = db
    engine.interrupted = False
    engine.progress_callback = None
    engine.logs = []
    engine.seen_hashes = {}
    engine.lock_path = None
    engine.log = lambda message, **_kwargs: engine.logs.append(str(message))  # type: ignore
    return engine


def test_execution_refuses_resolved_destination_outside_target(tmp_path):
    target = tmp_path / "library"
    target.mkdir()
    outside = tmp_path / "outside" / "escaped.wav"
    source = tmp_path / "source.wav"
    source.write_bytes(b"sound")
    harness = _ExecutionHarness(target)
    harness.destination_resolver = _UnsafeResolver(outside)

    result, dest_path = harness._process_single_record(
        _record(source),
        move=True,
        dry_run=False,
        flat=False,
        no_prefix=False,
        csv_writer=None,
    )

    assert result == "error"
    assert not outside.exists()
    assert source.exists()


def test_preserved_record_must_remain_under_preserved_root(tmp_path):
    target = tmp_path / "library"
    target.mkdir()
    preserved_root = tmp_path / "source" / "HANDSOFF"
    preserved_root.mkdir(parents=True)
    unrelated = tmp_path / "other" / "file.wav"
    unrelated.parent.mkdir()
    unrelated.write_bytes(b"sound")
    harness = _ExecutionHarness(target)
    harness.session_source_roots = [tmp_path / "source"]

    result, dest_path = harness._process_single_record(
        _record(unrelated, is_preserved=True, preserved_root=preserved_root),
        move=True,
        dry_run=False,
        flat=False,
        no_prefix=False,
        csv_writer=None,
    )

    assert result == "error"
    assert preserved_root.exists()
    assert unrelated.exists()


def test_preserved_root_must_remain_under_session_source_root(tmp_path):
    target = tmp_path / "library"
    target.mkdir()
    session_source = tmp_path / "source"
    preserved_root = tmp_path / "other" / "HANDSOFF"
    preserved_root.mkdir(parents=True)
    preserved_file = preserved_root / "file.wav"
    preserved_file.write_bytes(b"sound")
    harness = _ExecutionHarness(target)
    harness.session_source_roots = [session_source]

    result, dest_path = harness._process_single_record(
        _record(preserved_file, is_preserved=True, preserved_root=preserved_root),
        move=True,
        dry_run=False,
        flat=False,
        no_prefix=False,
        csv_writer=None,
    )

    assert result == "error"
    assert preserved_file.exists()
    assert not (target / "HANDSOFF").exists()


def test_preserved_root_under_session_source_copies_normally(tmp_path):
    target = tmp_path / "library"
    target.mkdir()
    session_source = tmp_path / "source"
    preserved_root = session_source / "HANDSOFF"
    preserved_root.mkdir(parents=True)
    preserved_file = preserved_root / "nested" / "file.wav"
    preserved_file.parent.mkdir()
    preserved_file.write_bytes(b"sound")
    harness = _ExecutionHarness(target)
    harness.session_source_roots = [session_source]

    result, dest_path = harness._process_single_record(
        _record(preserved_file, is_preserved=True, preserved_root=preserved_root),
        move=False,
        dry_run=False,
        flat=False,
        no_prefix=False,
        csv_writer=None,
    )

    assert result == "copied"
    assert (target / "HANDSOFF" / "nested" / "file.wav").exists()
    assert preserved_file.exists()


def test_preserved_folder_fast_path_refuses_child_symlink(tmp_path):
    target = tmp_path / "library"
    target.mkdir()
    session_source = tmp_path / "source"
    preserved_root = session_source / "HANDSOFF"
    preserved_root.mkdir(parents=True)
    real = tmp_path / "real.wav"
    real.write_bytes(b"sound")
    linked = preserved_root / "linked.wav"
    try:
        linked.symlink_to(real)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")
    harness = _ExecutionHarness(target)
    harness.session_source_roots = [session_source]

    result, dest_path = harness._process_single_record(
        _record(linked, is_preserved=True, preserved_root=preserved_root),
        move=False,
        dry_run=False,
        flat=False,
        no_prefix=False,
        csv_writer=None,
    )

    assert result == "error"
    assert linked.is_symlink()
    assert not (target / "HANDSOFF").exists()


def test_undo_refuses_tampered_target_outside_session_target(tmp_path):
    target = tmp_path / "library"
    target.mkdir()
    outside = tmp_path / "outside.wav"
    outside.write_bytes(b"do not delete")
    db = _UndoDB(
        [{"source_path": str(tmp_path / "source.wav"), "target_path": str(outside), "status": "copied"}],
        target,
        mode="copy",
    )
    engine = _runtime_for_undo(target, db)

    result = engine.undo_session("tampered")

    assert "error" in result
    assert outside.exists()


def test_undo_reanchors_to_session_target_after_restore(tmp_path):
    session_target = tmp_path / "library"
    restored_target = session_target / "Oneshots"
    target_file = session_target / "Non-Audio Assets" / "Pack" / "LICENSE.pdf"
    source_file = tmp_path / "source" / "LICENSE.pdf"
    target_file.parent.mkdir(parents=True)
    restored_target.mkdir()
    target_file.write_bytes(b"license")
    db = _UndoDB(
        [
            {
                "source_path": str(source_file),
                "target_path": str(target_file),
                "status": "copied",
                "hash": get_file_hash(target_file),
            }
        ],
        session_target,
        mode="copy",
    )
    engine = _runtime_for_undo(restored_target, db)

    result = engine.undo_session("restored-session")

    assert "error" not in result
    assert result["undone"] == 1
    assert result["target_root"] == str(session_target)
    assert engine.target_dir == session_target
    assert not target_file.exists()


def test_undo_uses_parent_library_when_session_root_is_category_folder(tmp_path):
    library = tmp_path / "library"
    category_root = library / "Oneshots"
    audio_file = library / "Oneshots" / "Kicks" / "kick.wav"
    loop_file = library / "Loops" / "Drums" / "loop.wav"
    doc_file = library / "Non-Audio Assets" / "Pack" / "LICENSE.pdf"
    for path, payload in ((audio_file, b"kick"), (loop_file, b"loop"), (doc_file, b"doc")):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    db = _UndoDB(
        [
            {
                "source_path": str(tmp_path / "source" / path.name),
                "target_path": str(path),
                "status": "copied",
                "hash": get_file_hash(path),
            }
            for path in (audio_file, loop_file, doc_file)
        ],
        category_root,
        mode="copy",
    )
    engine = _runtime_for_undo(category_root, db)

    result = engine.undo_session("category-root-session")

    assert "error" not in result
    assert result["undone"] == 3
    assert result["target_root"] == str(library)
    assert engine.target_dir == library
    assert not audio_file.exists()
    assert not loop_file.exists()
    assert not doc_file.exists()


def test_undo_does_not_broaden_category_root_for_outside_record(tmp_path):
    library = tmp_path / "library"
    category_root = library / "Oneshots"
    outside = tmp_path / "outside" / "escaped.wav"
    outside.parent.mkdir()
    outside.write_bytes(b"outside")
    db = _UndoDB(
        [
            {
                "source_path": str(tmp_path / "source" / "escaped.wav"),
                "target_path": str(outside),
                "status": "copied",
                "hash": get_file_hash(outside),
            }
        ],
        category_root,
        mode="copy",
    )
    engine = _runtime_for_undo(category_root, db)

    result = engine.undo_session("outside-category-root-session")

    assert "error" in result
    assert outside.exists()


def test_undo_prefers_local_sidecar_records_over_stale_global_records(tmp_path):
    target = tmp_path / "library" / "Oneshots"
    stale_target = tmp_path / "library" / "Non-Audio Assets" / "Pack" / "LICENSE.pdf"
    actual_target = target / "Non-Audio Assets" / "Pack" / "LICENSE.pdf"
    source = tmp_path / "source" / "LICENSE.pdf"
    actual_target.parent.mkdir(parents=True)
    actual_target.write_bytes(b"license")
    file_hash = get_file_hash(actual_target)
    stale_global_db = _UndoDB(
        [
            {
                "source_path": str(source),
                "target_path": str(stale_target),
                "status": "copied",
                "step_status": "COMMITTED",
                "original_action": "move",
                "hash": file_hash,
            }
        ],
        target,
        mode="move",
        source_roots=[source.parent],
    )
    local_db = _UndoDB(
        [
            {
                "source_path": str(source),
                "target_path": str(actual_target),
                "status": "copied",
                "step_status": "COMMITTED",
                "original_action": "move",
                "hash": file_hash,
            }
        ],
        target,
        mode="move",
        source_roots=[source.parent],
    )
    engine = _runtime_for_split_undo_db(target, stale_global_db, local_db)

    result = engine.undo_session("split-session")

    assert "error" not in result
    assert result["undone"] == 1
    assert source.read_bytes() == b"license"
    assert not actual_target.exists()
    assert stale_global_db.marked_undone == ["split-session"]
    assert local_db.deleted == ["split-session"]


def test_undo_refuses_ambiguous_duplicate_trash_restore(tmp_path):
    target = tmp_path / "library"
    trash = target / "DO_NOT_DELETE_unshuffle" / "trash" / "dupe-session"
    trash.mkdir(parents=True)
    trash_file = trash / "duplicate.wav"
    trash_file.write_bytes(b"only copy")
    source_a = tmp_path / "a" / "duplicate.wav"
    source_b = tmp_path / "b" / "duplicate.wav"
    db = _UndoDB(
        [
            {"source_path": str(source_a), "target_path": str(target / "existing.wav"), "status": "duplicate"},
            {"source_path": str(source_b), "target_path": str(target / "existing.wav"), "status": "duplicate"},
        ],
        target,
        mode="move",
    )
    engine = _runtime_for_undo(target, db)

    result = engine.undo_session("dupe-session")

    assert "error" in result
    assert trash_file.exists()
    assert not source_a.exists()
    assert not source_b.exists()


def test_undo_restores_same_name_duplicates_from_exact_trash_paths(tmp_path):
    target = tmp_path / "library"
    trash = target / "DO_NOT_DELETE_unshuffle" / "trash" / "dupe-session"
    trash.mkdir(parents=True)
    source_a = tmp_path / "a" / "duplicate.wav"
    source_b = tmp_path / "b" / "duplicate.wav"
    trash_a = trash / "duplicate.wav"
    trash_b = trash / "duplicate_1.wav"
    trash_a.write_bytes(b"a")
    trash_b.write_bytes(b"b")
    db = _UndoDB(
        [
            {"source_path": str(source_a), "target_path": str(target / "existing-a.wav"), "status": "duplicate", "trash_path": str(trash_a)},
            {"source_path": str(source_b), "target_path": str(target / "existing-b.wav"), "status": "duplicate", "trash_path": str(trash_b)},
        ],
        target,
        mode="move",
    )
    engine = _runtime_for_undo(target, db)

    result = engine.undo_session("dupe-session")

    assert result["undone"] == 2
    assert result["session_id"] == "dupe-session"
    assert result["target_root"] == str(target)
    assert source_a.read_bytes() == b"a"
    assert source_b.read_bytes() == b"b"
    assert not trash_a.exists()
    assert not trash_b.exists()


def test_undo_refuses_duplicate_trash_hash_mismatch(tmp_path):
    target = tmp_path / "library"
    trash = target / "DO_NOT_DELETE_unshuffle" / "trash" / "dupe-session"
    trash.mkdir(parents=True)
    source = tmp_path / "a" / "duplicate.wav"
    trash_file = trash / "duplicate.wav"
    trash_file.write_bytes(b"tampered")
    db = _UndoDB(
        [
            {
                "source_path": str(source),
                "target_path": str(target / "existing.wav"),
                "status": "duplicate",
                "trash_path": str(trash_file),
                "file_hash": "0" * 64,
            },
        ],
        target,
        mode="move",
    )
    engine = _runtime_for_undo(target, db)

    result = engine.undo_session("dupe-session")

    assert "error" in result
    assert trash_file.exists()
    assert not source.exists()


def test_undo_refuses_source_outside_recorded_session_roots(tmp_path):
    target = tmp_path / "library"
    target.mkdir()
    source_root = tmp_path / "source"
    source_root.mkdir()
    outside_source = tmp_path / "other" / "kick.wav"
    target_file = target / "Oneshots" / "Kicks" / "kick.wav"
    target_file.parent.mkdir(parents=True)
    target_file.write_bytes(b"do not move")
    db = _UndoDB(
        [{"source_path": str(outside_source), "target_path": str(target_file), "status": "copied", "original_action": "move"}],
        target,
        mode="move",
        source_roots=[source_root],
    )
    engine = _runtime_for_undo(target, db)

    result = engine.undo_session("tampered-source")

    assert "error" in result
    assert target_file.exists()
    assert not outside_source.exists()


def test_undo_refuses_mismatched_record_action(tmp_path):
    target = tmp_path / "library"
    target.mkdir()
    source_root = tmp_path / "source"
    source = source_root / "kick.wav"
    target_file = target / "Oneshots" / "Kicks" / "kick.wav"
    target_file.parent.mkdir(parents=True)
    target_file.write_bytes(b"do not delete")
    db = _UndoDB(
        [{"source_path": str(source), "target_path": str(target_file), "status": "copied", "original_action": "move"}],
        target,
        mode="copy",
        source_roots=[source_root],
    )
    engine = _runtime_for_undo(target, db)

    result = engine.undo_session("mismatched-action")

    assert "error" in result
    assert target_file.exists()


def test_copy_undo_requires_matching_target_hash(tmp_path):
    target = tmp_path / "library"
    target.mkdir()
    source = tmp_path / "source" / "kick.wav"
    target_file = target / "Oneshots" / "Kicks" / "kick.wav"
    target_file.parent.mkdir(parents=True)
    target_file.write_bytes(b"built")
    db = _UndoDB(
        [{
            "source_path": str(source),
            "target_path": str(target_file),
            "status": "copied",
            "file_hash": get_file_hash(target_file),
            "step_status": "COMMITTED",
            "original_action": "copy",
        }],
        target,
        mode="copy",
    )
    engine = _runtime_for_undo(target, db)

    result = engine.undo_session("copy-session")

    assert result["undone"] == 1
    assert result["session_id"] == "copy-session"
    assert result["target_root"] == str(target)
    assert not target_file.exists()
    assert db.marked_undone == ["copy-session"]


def test_undo_ignores_failed_build_records_and_reverts_committed_rows(tmp_path):
    target = tmp_path / "library"
    target.mkdir()
    source = tmp_path / "source" / "kick.wav"
    failed_source = tmp_path / "source" / "failed.wav"
    target_file = target / "Oneshots" / "Kicks" / "kick.wav"
    target_file.parent.mkdir(parents=True)
    target_file.write_bytes(b"built")
    db = _UndoDB(
        [
            {
                "source_path": str(source),
                "target_path": str(target_file),
                "status": "copied",
                "file_hash": get_file_hash(target_file),
                "step_status": "COMMITTED",
                "original_action": "move",
            },
            {
                "source_path": str(failed_source),
                "target_path": str(target / "failed.wav"),
                "status": "error",
                "file_hash": "",
                "step_status": "FAILED",
                "original_action": "move",
            },
        ],
        target,
        mode="move",
        source_roots=[source.parent],
    )
    engine = _runtime_for_undo(target, db)

    result = engine.undo_session("partial-move")

    assert result["undone"] == 1
    assert result["skipped_non_committed"] == 1
    assert source.exists()
    assert not target_file.exists()
    assert db.marked_undone == ["partial-move"]


def test_copy_undo_refuses_legacy_record_without_hash(tmp_path):
    target = tmp_path / "library"
    target.mkdir()
    target_file = target / "kick.wav"
    target_file.write_bytes(b"built")
    db = _UndoDB(
        [{"source_path": str(tmp_path / "source.wav"), "target_path": str(target_file), "status": "copied"}],
        target,
        mode="copy",
    )
    engine = _runtime_for_undo(target, db)

    result = engine.undo_session("legacy-copy")

    assert "error" in result
    assert target_file.exists()
    assert db.deleted == []


def test_copy_undo_refuses_modified_target_and_preserves_history(tmp_path):
    target = tmp_path / "library"
    target.mkdir()
    target_file = target / "kick.wav"
    target_file.write_bytes(b"modified")
    db = _UndoDB(
        [{
            "source_path": str(tmp_path / "source.wav"),
            "target_path": str(target_file),
            "status": "copied",
            "file_hash": "0" * 64,
            "step_status": "COMMITTED",
            "original_action": "copy",
        }],
        target,
        mode="copy",
    )
    engine = _runtime_for_undo(target, db)

    result = engine.undo_session("tampered-copy")

    assert "error" in result
    assert target_file.exists()
    assert db.deleted == []


def test_move_undo_refuses_recreated_source_path(tmp_path):
    target = tmp_path / "library"
    target.mkdir()
    source_root = tmp_path / "source"
    source_root.mkdir()
    source = source_root / "kick.wav"
    source.write_bytes(b"new source")
    target_file = target / "kick.wav"
    target_file.write_bytes(b"built")
    db = _UndoDB(
        [{
            "source_path": str(source),
            "target_path": str(target_file),
            "status": "copied",
            "file_hash": get_file_hash(target_file),
            "step_status": "COMMITTED",
            "original_action": "move",
        }],
        target,
        mode="move",
        source_roots=[source_root],
    )
    engine = _runtime_for_undo(target, db)

    result = engine.undo_session("move-source-conflict")

    assert "error" in result
    assert source.read_bytes() == b"new source"
    assert target_file.exists()
    assert db.deleted == []


def test_move_session_copy_record_undoes_as_copy(tmp_path):
    target = tmp_path / "library"
    target.mkdir()
    source = tmp_path / "source" / "kick.wav"
    source.parent.mkdir()
    source.write_bytes(b"source remains")
    target_file = target / "kick.wav"
    target_file.write_bytes(b"built")
    db = _UndoDB(
        [{
            "source_path": str(source),
            "target_path": str(target_file),
            "status": "copied",
            "file_hash": get_file_hash(target_file),
            "step_status": "COMMITTED",
            "original_action": "copy",
        }],
        target,
        mode="move",
        source_roots=[source.parent],
    )
    engine = _runtime_for_undo(target, db)

    result = engine.undo_session("hardlink-fallback")

    assert result["undone"] == 1
    assert result["session_id"] == "hardlink-fallback"
    assert result["target_root"] == str(target)
    assert source.read_bytes() == b"source remains"
    assert not target_file.exists()


def test_duplicate_move_refuses_recreated_source_path(tmp_path):
    target = tmp_path / "library"
    trash = target / "DO_NOT_DELETE_unshuffle" / "trash" / "dupe-session"
    trash.mkdir(parents=True)
    source = tmp_path / "source" / "duplicate.wav"
    source.parent.mkdir()
    source.write_bytes(b"new source")
    trash_file = trash / "duplicate.wav"
    trash_file.write_bytes(b"trash")
    db = _UndoDB(
        [{
            "source_path": str(source),
            "target_path": str(target / "existing.wav"),
            "status": "duplicate",
            "trash_path": str(trash_file),
            "file_hash": get_file_hash(trash_file),
            "step_status": "COMMITTED",
            "original_action": "move",
        }],
        target,
        mode="move",
        source_roots=[source.parent],
    )
    engine = _runtime_for_undo(target, db)

    result = engine.undo_session("dupe-session")

    assert "error" in result
    assert source.read_bytes() == b"new source"
    assert trash_file.exists()
    assert db.deleted == []


def test_failed_undo_preserves_session_history(tmp_path):
    target = tmp_path / "library"
    target.mkdir()
    source = tmp_path / "source.wav"
    target_file = target / "kick.wav"
    target_file.write_bytes(b"built")
    db = _UndoDB(
        [{
            "source_path": str(source),
            "target_path": str(target_file),
            "status": "copied",
            "file_hash": get_file_hash(target_file),
            "step_status": "COMMITTED",
            "original_action": "copy",
        }],
        target,
        mode="copy",
    )
    engine = _runtime_for_undo(target, db)

    with mock.patch.object(os, "remove", side_effect=OSError("nope")):
        result = engine.undo_session("copy-fail")

    assert result["error"]
    assert db.deleted == []


def test_copy_undo_deletes_readonly_target(tmp_path):
    target = tmp_path / "library"
    target.mkdir()
    target_file = target / "readonly.wav"
    target_file.write_bytes(b"built")
    target_file.chmod(stat.S_IREAD)
    db = _UndoDB(
        [{
            "source_path": str(tmp_path / "source.wav"),
            "target_path": str(target_file),
            "status": "copied",
            "file_hash": get_file_hash(target_file),
            "step_status": "COMMITTED",
            "original_action": "copy",
        }],
        target,
        mode="copy",
    )
    engine = _runtime_for_undo(target, db)

    result = engine.undo_session("readonly-copy")

    assert result["undone"] == 1
    assert result["session_id"] == "readonly-copy"
    assert result["target_root"] == str(target)
    assert not target_file.exists()
    assert db.marked_undone == ["readonly-copy"]


def test_copy_undo_retry_treats_missing_targets_as_already_undone(tmp_path):
    target = tmp_path / "library"
    target.mkdir()
    missing_target = target / "already-gone.wav"
    remaining_target = target / "remaining.wav"
    remaining_target.write_bytes(b"built")
    db = _UndoDB(
        [
            {
                "source_path": str(tmp_path / "already-gone-source.wav"),
                "target_path": str(missing_target),
                "status": "copied",
                "file_hash": "0" * 32,
                "step_status": "COMMITTED",
                "original_action": "copy",
            },
            {
                "source_path": str(tmp_path / "remaining-source.wav"),
                "target_path": str(remaining_target),
                "status": "copied",
                "file_hash": get_file_hash(remaining_target),
                "step_status": "COMMITTED",
                "original_action": "copy",
            },
        ],
        target,
        mode="copy",
    )
    engine = _runtime_for_undo(target, db)

    result = engine.undo_session("retry-copy")

    assert result["undone"] == 1
    assert result["already_undone"] == 1
    assert result["session_id"] == "retry-copy"
    assert result["target_root"] == str(target)
    assert not remaining_target.exists()
    assert db.marked_undone == ["retry-copy"]


def test_successful_copy_undo_preserves_global_history_and_removes_disposable_target_sidecar(tmp_path):
    target = tmp_path / "library"
    target.mkdir()
    source = tmp_path / "source.wav"
    target_file = target / "built.wav"
    target_file.write_bytes(b"built")
    global_db = UnshuffleDB(tmp_path / "global.db")
    db_path = get_local_system_dir(target) / DB_FILE_NAME
    local_db = UnshuffleDB(db_path)
    local_db.register_session("copy-clean", source, target, "copy")
    local_db.add_records_bulk(
        "copy-clean",
        [{
            "source_path": str(source),
            "target_path": str(target_file),
            "category": "Kicks",
            "subcategory": "",
            "pack": "Pack",
            "hash": get_file_hash(target_file),
            "confidence": 1.0,
            "status": "copied",
            "tags": "",
            "step_status": "COMMITTED",
            "original_action": "copy",
        }],
    )
    sidecar = db_path.parent
    assert sidecar.exists()
    engine = _runtime_for_split_undo_db(target, global_db, local_db)

    try:
        result = engine.undo_session("copy-clean")

        assert result["undone"] == 1
        assert not target_file.exists()
        assert not sidecar.exists()
        with UnshuffleDB(tmp_path / "global.db") as reopened_global:
            rows = reopened_global.get_session_records("copy-clean")
            assert rows
            assert {row["step_status"] for row in rows} == {"UNDONE"}
    finally:
        global_db.close()


def test_local_only_undo_session_is_mirrored_to_global_as_undone(tmp_path):
    target = tmp_path / "library"
    target.mkdir()
    source = tmp_path / "source.wav"
    target_file = target / "built.wav"
    target_file.write_bytes(b"built")
    global_db = UnshuffleDB(tmp_path / "global.db")
    local_db = UnshuffleDB(tmp_path / "local.db")
    local_db.register_session("local-only", source, target, "copy")
    local_db.add_records_bulk(
        "local-only",
        [{
            "source_path": str(source),
            "target_path": str(target_file),
            "category": "Kicks",
            "subcategory": "",
            "pack": "Pack",
            "hash": get_file_hash(target_file),
            "confidence": 1.0,
            "status": "copied",
            "tags": "",
            "step_status": "COMMITTED",
            "original_action": "copy",
        }],
    )
    engine = _runtime_for_split_undo_db(target, global_db, local_db)

    try:
        result = engine.undo_session("local-only")

        assert result["undone"] == 1
        with UnshuffleDB(tmp_path / "global.db") as reopened_global:
            global_rows = reopened_global.get_session_records("local-only")
            assert global_rows
            assert {row["step_status"] for row in global_rows} == {"UNDONE"}
        with UnshuffleDB(tmp_path / "local.db") as reopened_local:
            local_rows = reopened_local.get_session_records("local-only")
            assert not local_rows
    finally:
        global_db.close()
        local_db.close()


def test_preserved_directory_undo_requests_confirmation(tmp_path):
    target = tmp_path / "library"
    preserved_target = target / "HANDSOFF"
    preserved_target.mkdir(parents=True)
    db = _UndoDB(
        [{
            "source_path": str(tmp_path / "source" / "HANDSOFF"),
            "target_path": str(preserved_target),
            "status": "copied",
            "step_status": "COMMITTED",
            "original_action": "copy",
            "is_preserved": 1,
            "file_hash": "PRESERVED_SESS_SKIPPED",
        }],
        target,
        mode="copy",
    )
    engine = _runtime_for_undo(target, db)

    result = engine.undo_session("preserved")

    assert result["requires_preserved_confirmation"] is True
    assert isinstance(result["items"], list)
    assert result["items"][0]["action"] == "delete_from_target"
    assert preserved_target.exists()


def test_confirmed_preserved_copy_undo_deletes_target_folder(tmp_path):
    target = tmp_path / "library"
    preserved_target = target / "HANDSOFF"
    nested = preserved_target / "nested" / "file.wav"
    nested.parent.mkdir(parents=True)
    nested.write_bytes(b"sound")
    db = _UndoDB(
        [{
            "source_path": str(tmp_path / "source" / "HANDSOFF"),
            "target_path": str(preserved_target),
            "status": "copied",
            "step_status": "COMMITTED",
            "original_action": "copy",
            "is_preserved": 1,
            "file_hash": "PRESERVED_SESS_SKIPPED",
        }],
        target,
        mode="copy",
    )
    engine = _runtime_for_undo(target, db)

    result = engine.undo_session("preserved", confirm_preserved=True)

    assert result["undone"] == 1
    assert result["session_id"] == "preserved"
    assert result["target_root"] == str(target)
    assert not preserved_target.exists()


def test_confirmed_preserved_move_undo_restores_folder(tmp_path):
    target = tmp_path / "library"
    source_root = tmp_path / "source"
    source_preserved = source_root / "HANDSOFF"
    preserved_target = target / "HANDSOFF"
    nested = preserved_target / "nested" / "file.wav"
    nested.parent.mkdir(parents=True)
    nested.write_bytes(b"sound")
    db = _UndoDB(
        [{
            "source_path": str(source_preserved),
            "target_path": str(preserved_target),
            "status": "copied",
            "step_status": "COMMITTED",
            "original_action": "move",
            "is_preserved": 1,
            "file_hash": "PRESERVED_SESS_SKIPPED",
        }],
        target,
        mode="move",
        source_roots=[source_root],
    )
    engine = _runtime_for_undo(target, db)

    result = engine.undo_session("preserved", confirm_preserved=True)

    assert result["undone"] == 1
    assert result["session_id"] == "preserved"
    assert result["target_root"] == str(target)
    assert not preserved_target.exists()
    assert (source_preserved / "nested" / "file.wav").read_bytes() == b"sound"


def test_confirmed_preserved_move_refuses_existing_source(tmp_path):
    target = tmp_path / "library"
    source_root = tmp_path / "source"
    source_preserved = source_root / "HANDSOFF"
    source_preserved.mkdir(parents=True)
    preserved_target = target / "HANDSOFF"
    preserved_target.mkdir(parents=True)
    db = _UndoDB(
        [{
            "source_path": str(source_preserved),
            "target_path": str(preserved_target),
            "status": "copied",
            "step_status": "COMMITTED",
            "original_action": "move",
            "is_preserved": 1,
            "file_hash": "PRESERVED_SESS_SKIPPED",
        }],
        target,
        mode="move",
        source_roots=[source_root],
    )
    engine = _runtime_for_undo(target, db)

    result = engine.undo_session("preserved", confirm_preserved=True)

    assert "Undo source already exists" in result["error"]
    assert preserved_target.exists()
    assert source_preserved.exists()


def test_move_refuses_symlink_source_before_copy_or_delete(tmp_path):
    real_source = tmp_path / "real.wav"
    real_source.write_bytes(b"sound")
    link = tmp_path / "link.wav"
    try:
        link.symlink_to(real_source)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")
    target = tmp_path / "library"
    target.mkdir()
    harness = _ExecutionHarness(target)

    result = harness._execute_file_transfer(link, target / "copied.wav", target, move=True, source_hash=None)

    assert result is None
    assert link.is_symlink()
    assert real_source.exists()
    assert not (target / "copied.wav").exists()


def test_file_transfer_uses_collision_safe_temporary_path(tmp_path):
    target = tmp_path / "library"
    target.mkdir()
    source = tmp_path / "source.wav"
    source.write_bytes(b"sound")
    stale_temp = target / "copied.wav.unshuffletmp"
    stale_temp.write_bytes(b"keep me")
    harness = _ExecutionHarness(target)

    result = harness._execute_file_transfer(source, target / "copied.wav", target, move=False, source_hash=None)

    assert result == target / "copied.wav"
    assert (target / "copied.wav").read_bytes() == b"sound"
    assert stale_temp.read_bytes() == b"keep me"


def test_move_cleanup_does_not_remove_folder_outside_session_roots(tmp_path):
    target = tmp_path / "library"
    target.mkdir()
    session_source = tmp_path / "source"
    session_source.mkdir()
    outside_folder = tmp_path / "outside"
    outside_folder.mkdir()
    outside_file = outside_folder / "kick.wav"
    outside_file.write_bytes(b"sound")
    harness = _ExecutionHarness(target)
    harness.session_source_roots = [session_source]

    result, dest_path = harness._process_single_record(
        _record(outside_file),
        move=True,
        dry_run=False,
        flat=False,
        no_prefix=False,
        csv_writer=None,
    )

    assert result == "copied"
    assert dest_path.exists()
    assert outside_folder.exists()
    assert not outside_file.exists()


def test_analysis_skips_symlink_entries(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    real = source / "real.wav"
    real.write_bytes(b"sound")
    linked = source / "linked.wav"
    try:
        linked.symlink_to(real)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")

    context = AnalysisContext(source)
    build_node_graph(source, context)

    assert real in context.nodes
    assert linked not in context.nodes


def test_cache_rebuild_skips_symlink_audio(tmp_path):
    class _CacheHarness(CacheMixin):
        def __init__(self, target_dir: Path) -> None:
            self.target_dir = target_dir
            self.interrupted = False
            self.progress_callback = None

    target = tmp_path / "library"
    target.mkdir()
    real = target / "real.wav"
    real.write_bytes(b"sound")
    linked = target / "linked.wav"
    try:
        linked.symlink_to(real)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")

    files = list(_CacheHarness(target)._find_audio_files(target))

    assert real in files
    assert linked not in files


def test_remove_staging_by_source_does_not_delete_prefix_sibling(tmp_path):
    db = UnshuffleDB(tmp_path / "prefix.db")
    try:
        session_id = "prefix-session"
        db.register_session(session_id, tmp_path / "src", tmp_path / "target", "copy")
        db.add_staging_records_bulk(
            session_id,
            [
                (1, (tmp_path / "samples" / "kick.wav").as_posix(), "Kick", "Pack", "Drums", "Kick", "Oneshot", "[]", 1.0, 0.1, "h1", "[]", None, None, 0),
                (2, (tmp_path / "samples_extra" / "snare.wav").as_posix(), "Snare", "Pack", "Drums", "Snare", "Oneshot", "[]", 1.0, 0.1, "h2", "[]", None, None, 0),
            ],
        )

        db.remove_staging_by_source(session_id, (tmp_path / "samples").as_posix())

        rows = db.conn.execute("SELECT source_path FROM staging_records ORDER BY row_id").fetchall()
        assert [row["source_path"] for row in rows] == [(tmp_path / "samples_extra" / "snare.wav").as_posix()]
    finally:
        db.close()
