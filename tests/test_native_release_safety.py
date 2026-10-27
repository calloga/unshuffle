from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest import mock

import numpy as np
import pytest

from scripts import app_binary_entrypoint, check_linux_bundle, install_release_dependencies


def test_native_entrypoint_runs_without_gui_imports_or_user_data(tmp_path):
    root = Path(__file__).resolve().parents[1]
    code = '''
import importlib.abc, sys
class NoGui(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'gui', 'PySide6'}:
            raise RuntimeError('Native probe must not import the GUI: ' + fullname)
sys.meta_path.insert(0, NoGui())
from scripts.app_binary_entrypoint import main
sys.argv = ['Unshuffle', '--self-test-native']
raise SystemExit(main())
'''
    env = dict(os.environ)
    for key in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME", "XDG_DATA_HOME"):
        env[key] = str(tmp_path)
    result = subprocess.run([sys.executable, "-c", code], cwd=root, env=env,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["status"] == "ok"
    assert report["labels"] == [[0, 1], [1, 0]]
    assert len(report["hnswlib_sha256"]) == 64
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("labels,distances", [
    ([[1, 0], [0, 1]], [[0, 20], [0, 20]]),
    ([[0, 1], [1, 0]], [[0, float("nan")], [0, 20]]),
    ([[0, 1], [1, 0]], [[0, 1], [0, 1]]),
])
def test_native_self_test_rejects_invalid_results(monkeypatch, capsys, labels, distances):
    import hnswlib
    index = mock.Mock()
    index.knn_query.return_value = np.array(labels), np.array(distances)
    monkeypatch.setattr(hnswlib, "Index", mock.Mock(return_value=index))
    monkeypatch.setattr(sys, "argv", ["Unshuffle", "--self-test-native"])
    assert app_binary_entrypoint.main() == 1
    assert json.loads(capsys.readouterr().err)["status"] == "error"


def test_normal_entrypoint_preserves_launcher_dispatch(monkeypatch):
    launcher = SimpleNamespace(main=mock.Mock(return_value=7))
    monkeypatch.setitem(sys.modules, "gui.main.launcher", launcher)
    monkeypatch.setattr(sys, "argv", ["Unshuffle"])
    assert app_binary_entrypoint.main() == 7
    launcher.main.assert_called_once_with()


def test_native_entrypoint_reports_missing_extension(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "hnswlib", None)
    monkeypatch.setattr(sys, "argv", ["Unshuffle", "--self-test-native"])
    assert app_binary_entrypoint.main() == 1
    error = json.loads(capsys.readouterr().err)
    assert error["status"] == "error"
    assert "hnswlib" in error["error"]


@pytest.mark.parametrize("system,machine,flags", [
    ("Linux", "x86_64", "-march=x86-64 -mtune=generic"),
    ("Linux", "AMD64", "-march=x86-64 -mtune=generic"),
    ("Linux", "aarch64", "-O2"),
    ("Darwin", "arm64", "-O2"),
    ("Windows", "AMD64", "-O2"),
])
def test_portable_build_environment(system, machine, flags):
    original = {"CFLAGS": "-O2", "CXXFLAGS": "-O2"}
    env = install_release_dependencies.build_environment(original, system, machine)
    assert env["CFLAGS"] == env["CXXFLAGS"] == flags
    assert (env.get("HNSWLIB_NO_NATIVE") == "1") == (system != "Windows")
    assert original == {"CFLAGS": "-O2", "CXXFLAGS": "-O2"}


@pytest.mark.parametrize("log", [
    "g++ -c python_bindings/bindings.cpp -march=native",
    "g++ -c python_bindings/bindings.cpp -mcpu=native",
    "g++ -c python_bindings/bindings.cpp -mtune native",
    "Using cached hnswlib.whl",
])
def test_portable_build_rejects_native_targeting_and_skipped_compilation(log):
    with pytest.raises(RuntimeError):
        install_release_dependencies.validate_build_log(log)


@pytest.mark.parametrize("log", [
    "g++ -march=x86-64 -mtune=generic -c python_bindings/bindings.cpp -o build/bindings.o",
    "cl.exe /c /EHsc python_bindings/bindings.cpp /Fobuild/bindings.obj",
])
def test_portable_build_accepts_recorded_source_compilation(log):
    install_release_dependencies.validate_build_log(log)


def test_dependency_installer_forces_rebuild_and_records_provenance(monkeypatch, tmp_path):
    extension = tmp_path / "hnswlib.so"
    extension.write_bytes(b"portable-extension")
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        if "stdout" in kwargs:
            kwargs["stdout"].write("g++ -c python_bindings/bindings.cpp -march=x86-64 -mtune=generic\n")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(install_release_dependencies.subprocess, "run", run)
    monkeypatch.setattr(install_release_dependencies.platform, "system", lambda: "Linux")
    monkeypatch.setattr(install_release_dependencies.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(install_release_dependencies.util, "find_spec", lambda _: SimpleNamespace(origin=str(extension)))
    monkeypatch.setattr(install_release_dependencies.metadata, "distributions", lambda: [
        SimpleNamespace(metadata={"Name": "hnswlib"}, version="0.8.0"),
    ])
    output = tmp_path / "metadata"
    assert install_release_dependencies.main(["--metadata-dir", str(output)]) == 0
    rebuild = calls[-1][0]
    for flag in ("--force-reinstall", "--no-deps", "--no-cache-dir", "--no-binary=hnswlib", "hnswlib==0.8.0"):
        assert flag in rebuild
    assert all(kwargs["env"]["HNSWLIB_NO_NATIVE"] == "1" for _, kwargs in calls)
    report = json.loads((output / "dependencies.json").read_text())
    assert report["hnswlib_sha256"] == hashlib.sha256(extension.read_bytes()).hexdigest()
    assert report["dependencies"] == {"hnswlib": "0.8.0"}
    assert "bindings.cpp" in (output / "hnsw-build.log").read_text()


@pytest.mark.parametrize("failure", ["crash", "timeout", "bad-json", "bad-status"])
def test_linux_native_probe_fails_closed(monkeypatch, failure):
    def run(command, **kwargs):
        assert kwargs["timeout"] == 90
        assert kwargs["check"] is True
        if failure == "crash":
            raise subprocess.CalledProcessError(-4, command)
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, 90)
        return SimpleNamespace(stdout="invalid" if failure == "bad-json" else '{"status":"error"}')

    monkeypatch.setattr(check_linux_bundle.subprocess, "run", run)
    with pytest.raises((subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError, RuntimeError)):
        check_linux_bundle.native_probe(Path("app"), {}, emulate=True)


def test_linux_bundle_checks_host_emulator_headless_and_desktop(monkeypatch):
    calls = []
    app = Path("bundle/Unshuffle").resolve()

    def run(command, **kwargs):
        calls.append((command, kwargs))
        if command[-1] == "--self-test-native":
            return SimpleNamespace(stdout='{"status":"ok","hnswlib_sha256":"abc"}')
        if command[0] == "xvfb-run":
            assert "--desktop-child" in command
            assert kwargs["timeout"] == 60
            return SimpleNamespace(returncode=0)
        assert "DISPLAY" not in kwargs["env"]
        assert "WAYLAND_DISPLAY" not in kwargs["env"]
        return SimpleNamespace(returncode=1, stdout=check_linux_bundle.HEADLESS_MESSAGE, stderr="")

    monkeypatch.setattr(check_linux_bundle.subprocess, "run", run)
    check_linux_bundle.check_bundle(app)
    assert calls[0][0] == [str(app), "--self-test-native"]
    assert calls[1][0] == ["qemu-x86_64", "-cpu", "Nehalem", str(app), "--self-test-native"]
    assert len(calls) == 4
    assert all(kwargs["env"]["HOME"] != os.environ.get("HOME") for _, kwargs in calls)


def test_linux_bundle_rejects_wrong_headless_exit(monkeypatch):
    monkeypatch.setattr(check_linux_bundle, "native_probe", lambda *a, **kw: {"hnswlib_sha256": "abc"})
    monkeypatch.setattr(check_linux_bundle.subprocess, "run", lambda *a, **kw: SimpleNamespace(
        returncode=-4, stdout="", stderr="Illegal instruction"))
    with pytest.raises(RuntimeError, match="Headless diagnostic failed"):
        check_linux_bundle.check_bundle(Path("app"))


def test_linux_desktop_requires_visible_window_and_clean_exit(monkeypatch, tmp_path):
    process = mock.Mock()
    process.pid = 123
    process.poll.return_value = None
    process.wait.return_value = 0
    monkeypatch.setattr(check_linux_bundle.subprocess, "Popen", mock.Mock(return_value=process))
    run = mock.Mock(return_value=SimpleNamespace(returncode=0, stdout="456\n"))
    monkeypatch.setattr(check_linux_bundle.subprocess, "run", run)
    check_linux_bundle.desktop_probe(Path("app"), {}, tmp_path)
    assert run.call_args_list[0].args[0][1:5] == ["search", "--onlyvisible", "--pid", "123"]
    assert run.call_args_list[1].args[0] == ["xdotool", "key", "--window", "456", "Escape"]


def test_linux_desktop_rejects_early_exit(monkeypatch, tmp_path):
    process = mock.Mock(returncode=0)
    process.poll.return_value = 0
    monkeypatch.setattr(check_linux_bundle.subprocess, "Popen", lambda *a, **kw: process)
    with pytest.raises(RuntimeError, match="before its launcher appeared"):
        check_linux_bundle.desktop_probe(Path("app"), {}, tmp_path)


def test_linux_desktop_times_out_and_kills_unresponsive_app(monkeypatch, tmp_path):
    process = mock.Mock()
    process.poll.return_value = None
    monkeypatch.setattr(check_linux_bundle.subprocess, "Popen", lambda *a, **kw: process)
    monkeypatch.setattr(check_linux_bundle.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=1))
    ticks = iter([0, 0, 31])
    monkeypatch.setattr(check_linux_bundle, "time", SimpleNamespace(monotonic=lambda: next(ticks), sleep=lambda _: None))
    with pytest.raises(TimeoutError, match="within 30 seconds"):
        check_linux_bundle.desktop_probe(Path("app"), {}, tmp_path)
    process.kill.assert_called_once_with()
    process.wait.assert_called_once_with(timeout=5)


def test_linux_checker_extracts_final_appimage(monkeypatch, tmp_path):
    run = mock.Mock()
    check = mock.Mock()
    monkeypatch.setattr(check_linux_bundle.subprocess, "run", run)
    monkeypatch.setattr(check_linux_bundle, "check_bundle", check)
    image = tmp_path / "Unshuffle.AppImage"
    assert check_linux_bundle.main(["--appimage", str(image)]) == 0
    assert run.call_args.args[0] == [str(image.resolve()), "--appimage-extract"]
    assert check.call_args.args[0] == run.call_args.kwargs["cwd"] / "squashfs-root" / "Unshuffle"


def test_frozen_ci_uses_release_python_and_dependency_policy():
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github/workflows/test.yml").read_text()
    assert 'python-version: ["3.12"]' not in workflow
    assert workflow.count('python-version: ["3.14"]') == 2
    assert workflow.count("python scripts/install_release_dependencies.py") == 2
    assert "python scripts/check_linux_bundle.py --app dist/Unshuffle/Unshuffle" in workflow
