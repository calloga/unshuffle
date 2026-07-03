from pathlib import Path
import importlib.util
import sys
import types
from types import SimpleNamespace
from unittest import mock


FAST_HASH = "segmd5-v1:" + ("a" * 32)
FULL_HASH = "b" * 32


def _load_transfer_module():
    root = Path(__file__).parents[1]
    logic_pkg = sys.modules.setdefault("unshuffle.logic", types.ModuleType("unshuffle.logic"))
    logic_pkg.__path__ = [str(root / "unshuffle" / "logic")]
    execution_pkg = sys.modules.setdefault("unshuffle.logic.execution", types.ModuleType("unshuffle.logic.execution"))
    execution_pkg.__path__ = [str(root / "unshuffle" / "logic" / "execution")]

    module_name = "unshuffle.logic.execution.transfer"
    module_path = root / "unshuffle" / "logic" / "execution" / "transfer.py"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load transfer execution module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _owner(target_dir: Path):
    return SimpleNamespace(
        target_dir=target_dir,
        interrupted=False,
        _last_effective_action="copy",
        _last_record_hash=None,
        _last_record_error="",
        log=lambda *args, **kwargs: None,
    )


def test_execute_file_transfer_verifies_fast_hash_kind(tmp_path):
    transfer = _load_transfer_module()
    source = tmp_path / "source.wav"
    target = tmp_path / "target"
    dest = target / "copied.wav"
    target.mkdir()
    source.write_bytes(b"audio")
    owner = _owner(target)

    with mock.patch(
        "unshuffle.logic.execution.transfer.hash_for_verification",
        return_value=FAST_HASH,
    ) as verify_mock:
        result = transfer.execute_file_transfer(owner, source, dest, target, move=False, source_hash=FAST_HASH)

    assert result == dest
    assert dest.read_bytes() == b"audio"
    assert owner._last_record_hash == FAST_HASH
    assert verify_mock.call_count == 2
    assert verify_mock.call_args_list[0].args[1] == FAST_HASH
    assert verify_mock.call_args_list[1].args[1] == FAST_HASH


def test_execute_file_transfer_verifies_full_hash_kind(tmp_path):
    transfer = _load_transfer_module()
    source = tmp_path / "source.wav"
    target = tmp_path / "target"
    dest = target / "copied.wav"
    target.mkdir()
    source.write_bytes(b"audio")
    owner = _owner(target)

    with mock.patch(
        "unshuffle.logic.execution.transfer.hash_for_verification",
        return_value=FULL_HASH,
    ) as verify_mock:
        result = transfer.execute_file_transfer(owner, source, dest, target, move=False, source_hash=FULL_HASH)

    assert result == dest
    assert owner._last_record_hash == FULL_HASH
    assert verify_mock.call_count == 2
    assert verify_mock.call_args_list[0].args[1] == FULL_HASH
    assert verify_mock.call_args_list[1].args[1] == FULL_HASH
