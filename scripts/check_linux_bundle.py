"""Validate actual Linux bundles on the host CPU, without AVX, and on an X11 desktop."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time


HEADLESS_MESSAGE = "Unshuffle GUI requires a graphical desktop session on Linux"


def isolated_environment(root: Path) -> dict[str, str]:
    env = dict(os.environ)
    for key in ("HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME", "XDG_RUNTIME_DIR", "TMPDIR"):
        directory = root / key.lower()
        directory.mkdir(mode=0o700)
        env[key] = str(directory)
    for key in ("QT_QPA_PLATFORM", "PYTHONPATH", "PYTHONHOME"):
        env.pop(key, None)
    return env


def native_probe(app: Path, env: dict[str, str], *, emulate: bool) -> dict:
    command = [str(app), "--self-test-native"]
    if emulate:
        command = ["qemu-x86_64", "-cpu", "Nehalem"] + command
    try:
        result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=90, check=True)
    except subprocess.CalledProcessError as exc:
        print(f"Native probe failed: {exc.stdout}\n{exc.stderr}", file=sys.stderr, flush=True)
        raise
    report = json.loads(result.stdout)
    if report.get("status") != "ok" or not report.get("hnswlib_sha256"):
        raise RuntimeError(f"Native self-test returned an invalid report: {report}")
    print(json.dumps({"emulated": emulate, **report}), flush=True)
    return report


def desktop_probe(app: Path, env: dict[str, str], root: Path) -> None:
    """Called inside xvfb-run; wait for a real launcher window and dismiss it."""
    log_path = root / "desktop.log"
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen([str(app)], env=env, cwd=root, stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(f"GUI exited before its launcher appeared: {process.returncode}")
                found = subprocess.run(
                    ["xdotool", "search", "--onlyvisible", "--pid", str(process.pid), "--name", "^Unshuffle Launcher$"],
                    env=env, capture_output=True, text=True, timeout=5, check=False,
                )
                if found.returncode == 0 and found.stdout.strip():
                    window_id = found.stdout.splitlines()[0]
                    subprocess.run(["xdotool", "key", "--window", window_id, "Escape"], env=env, timeout=5, check=True)
                    if process.wait(timeout=15) != 0:
                        raise RuntimeError(f"GUI failed after closing the launcher: {process.returncode}")
                    print("Desktop launcher smoke passed", flush=True)
                    return
                time.sleep(0.2)
            raise TimeoutError("GUI did not display its launcher within 30 seconds")
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            print(log_path.read_text(encoding="utf-8", errors="replace"), flush=True)


def check_bundle(app: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="unshuffle-smoke-") as temporary:
        root = Path(temporary)
        env = isolated_environment(root)
        native = native_probe(app, env, emulate=False)
        portable = native_probe(app, env, emulate=True)
        if native["hnswlib_sha256"] != portable["hnswlib_sha256"]:
            raise RuntimeError("Host and emulated probes loaded different HNSW extensions")
        headless_env = dict(env)
        for key in ("DISPLAY", "WAYLAND_DISPLAY"):
            headless_env.pop(key, None)
        result = subprocess.run([str(app)], env=headless_env, cwd=root, capture_output=True, text=True, timeout=30)
        if result.returncode != 1 or HEADLESS_MESSAGE not in result.stdout:
            raise RuntimeError(f"Headless diagnostic failed ({result.returncode}): {result.stdout}\n{result.stderr}")
        print(result.stdout, flush=True)
        subprocess.run(
            ["xvfb-run", "-a", sys.executable, str(Path(__file__).resolve()), "--desktop-child", str(app)],
            env=env, cwd=root, timeout=60, check=True,
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--app", type=Path)
    target.add_argument("--appimage", type=Path)
    target.add_argument("--desktop-child", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.desktop_child:
        desktop_probe(args.desktop_child.resolve(), dict(os.environ), Path.cwd())
    elif args.appimage:
        with tempfile.TemporaryDirectory(prefix="unshuffle-appimage-") as temporary:
            root = Path(temporary)
            subprocess.run([str(args.appimage.resolve()), "--appimage-extract"], cwd=root, timeout=90, check=True,
                           stdout=subprocess.DEVNULL)
            check_bundle(root / "squashfs-root" / "Unshuffle")
    else:
        check_bundle(args.app.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
