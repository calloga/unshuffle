"""Install release/test dependencies and rebuild HNSW for portable distribution."""
from __future__ import annotations

import argparse
import hashlib
from importlib import metadata, util
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
HNSWLIB_VERSION = "0.8.0"
NATIVE_TARGET = re.compile(r"-(?:march|mcpu|mtune)(?:=|\s+)[\"']?native\b")


def build_environment(environment: dict[str, str], system: str, machine: str) -> dict[str, str]:
    env = dict(environment)
    if system != "Windows":
        env["HNSWLIB_NO_NATIVE"] = "1"
    if system == "Linux" and machine.lower() in {"x86_64", "amd64"}:
        env["CFLAGS"] = "-march=x86-64 -mtune=generic"
        env["CXXFLAGS"] = "-march=x86-64 -mtune=generic"
    return env


def validate_build_log(log: str) -> None:
    if NATIVE_TARGET.search(log):
        raise RuntimeError("HNSW build contains native CPU targeting; refusing to package it")
    if not any("bindings.cpp" in line and (" -c " in line or " /c " in line) for line in log.splitlines()):
        raise RuntimeError("HNSW source compilation was not recorded in the build log")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata-dir", type=Path, default=ROOT / "dist" / "build-metadata")
    args = parser.parse_args(argv)
    args.metadata_dir.mkdir(parents=True, exist_ok=True)
    env = build_environment(dict(os.environ), platform.system(), platform.machine())
    pip = [sys.executable, "-m", "pip"]
    subprocess.run(
        pip + ["install", "--no-cache-dir", "--no-binary=hnswlib", "-r", str(ROOT / "requirements-dev.txt"),
               f"hnswlib=={HNSWLIB_VERSION}"],
        env=env, cwd=ROOT, check=True,
    )
    # Force a rebuild even if a developer environment already contains HNSW.
    command = pip + ["install", "--verbose", "--force-reinstall", "--no-deps", "--no-cache-dir",
                     "--no-binary=hnswlib", f"hnswlib=={HNSWLIB_VERSION}"]
    log_path = args.metadata_dir / "hnsw-build.log"
    with log_path.open("w", encoding="utf-8") as log_file:
        result = subprocess.run(command, env=env, cwd=ROOT, stdout=log_file, stderr=subprocess.STDOUT, check=False)
    log = log_path.read_text(encoding="utf-8", errors="replace")
    print(log, flush=True)
    result.check_returncode()
    validate_build_log(log)
    spec = util.find_spec("hnswlib")
    if spec is None or spec.origin is None:
        raise RuntimeError("Rebuilt HNSW extension could not be located")
    extension = Path(spec.origin)
    report = {
        "python": sys.version,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "build_command": command,
        "build_environment": {key: env.get(key, "") for key in ("HNSWLIB_NO_NATIVE", "CFLAGS", "CXXFLAGS")},
        "hnswlib_path": str(extension),
        "hnswlib_sha256": hashlib.sha256(extension.read_bytes()).hexdigest(),
        "dependencies": dict(sorted((dist.metadata["Name"], dist.version) for dist in metadata.distributions())),
    }
    (args.metadata_dir / "dependencies.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
