from pathlib import Path
import importlib.util
import sys
import types
from types import SimpleNamespace
from unittest import mock


FAST_HASH = "segmd5-v1:" + ("a" * 32)


def _load_duplicates_module():
    root = Path(__file__).parents[1]
    logic_pkg = sys.modules.setdefault("unshuffle.logic", types.ModuleType("unshuffle.logic"))
    logic_pkg.__path__ = [str(root / "unshuffle" / "logic")]
    execution_pkg = sys.modules.setdefault("unshuffle.logic.execution", types.ModuleType("unshuffle.logic.execution"))
    execution_pkg.__path__ = [str(root / "unshuffle" / "logic" / "execution")]

    module_name = "unshuffle.logic.execution.duplicates"
    module_path = root / "unshuffle" / "logic" / "execution" / "duplicates.py"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load duplicate execution module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _owner(target_dir: Path):
    return SimpleNamespace(
        target_dir=target_dir,
        seen_hashes={FAST_HASH: "existing.wav"},
        db=None,
        interrupted=False,
        log=lambda *args, **kwargs: None,
        _last_record_hash=FAST_HASH,
        _last_duplicate_trash_path=None,
        _last_effective_action="copy",
    )


def test_fast_hash_duplicate_match_is_confirmed_with_full_hash(tmp_path):
    duplicates = _load_duplicates_module()
    target = tmp_path / "target"
    target.mkdir()
    existing = target / "existing.wav"
    source = tmp_path / "source.wav"
    existing.write_bytes(b"same")
    source.write_bytes(b"same")
    owner = _owner(target)
    record = SimpleNamespace(source_path=source, hash=FAST_HASH)

    with mock.patch(
        "unshuffle.logic.execution.duplicates.get_file_hash",
        side_effect=lambda path, interrupted_check=None: "full-same",
    ) as hash_mock:
        result = duplicates.handle_duplicate_record(
            owner,
            record,
            FAST_HASH,
            move=False,
            dry_run=False,
            move_file=lambda _src, _dst: None,
        )

    assert result == "duplicate"
    assert record.hash == "full-same"
    assert owner._last_record_hash == "full-same"
    assert hash_mock.call_count == 2


def test_fast_hash_collision_is_not_treated_as_duplicate(tmp_path):
    duplicates = _load_duplicates_module()
    target = tmp_path / "target"
    target.mkdir()
    existing = target / "existing.wav"
    source = tmp_path / "source.wav"
    existing.write_bytes(b"existing")
    source.write_bytes(b"source")
    owner = _owner(target)
    record = SimpleNamespace(source_path=source, hash=FAST_HASH)

    def fake_full_hash(path, interrupted_check=None):
        return "full-source" if Path(path) == source else "full-existing"

    with mock.patch("unshuffle.logic.execution.duplicates.get_file_hash", side_effect=fake_full_hash):
        result = duplicates.handle_duplicate_record(
            owner,
            record,
            FAST_HASH,
            move=False,
            dry_run=False,
            move_file=lambda _src, _dst: None,
        )

    assert result is None
    assert record.hash == FAST_HASH
    assert owner._last_record_hash == FAST_HASH
