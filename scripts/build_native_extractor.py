from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


def _platform_name() -> str:
    if os.name == "nt":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def _executable_name() -> str:
    return "unshuffle_extractor.exe" if os.name == "nt" else "unshuffle_extractor"


def _run(command: list[str], *, cwd: Path) -> None:
    subprocess.run(command, cwd=cwd, check=True)


def _built_binary(build_dir: Path, config: str) -> Path:
    name = _executable_name()
    candidates = [
        build_dir / config / name,
        build_dir / name,
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"Could not find built native extractor in {build_dir}")


def _validate_copied_binary(repo_root: Path) -> None:
    checker = repo_root / "scripts" / "check_native_extractor_bundle.py"
    _run([sys.executable, str(checker)], cwd=repo_root)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the native extractor for the current platform.")
    parser.add_argument("--config", default="Release", help="CMake build config to use.")
    parser.add_argument("--build-dir", default="unshuffle_extractor/build", help="CMake build directory.")
    parser.add_argument("--copy-to-bin", action="store_true", help="Copy the built binary to bin/<platform>/ for release validation.")
    args = parser.parse_args(argv)

    repo_root = Path(__file__).resolve().parents[1]
    source_dir = repo_root / "unshuffle_extractor"
    build_dir = repo_root / args.build_dir
    build_dir.mkdir(parents=True, exist_ok=True)

    _run(["cmake", "-S", str(source_dir), "-B", str(build_dir)], cwd=repo_root)
    _run(["cmake", "--build", str(build_dir), "--config", args.config], cwd=repo_root)

    binary = _built_binary(build_dir, args.config)
    print(binary)

    if args.copy_to_bin:
        dest_dir = repo_root / "bin" / _platform_name()
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / _executable_name()
        shutil.copy2(binary, dest)
        print(dest)
        _validate_copied_binary(repo_root)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
