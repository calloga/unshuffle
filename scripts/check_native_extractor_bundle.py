from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import tempfile
import wave
from pathlib import Path
from struct import pack

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from unshuffle.core.features import (
    CURRENT_EXTRACTOR_VERSION,
    CURRENT_FEATURE_SCHEMA,
    CURRENT_FEATURE_SPACE_VERSION,
    FEATURE_VECTOR_SIZE,
)


EXPECTED_VERSION = "unshuffle_extractor 1.0.0"
PLATFORM_BINARIES = {
    "windows": Path("bin/windows/unshuffle_extractor.exe"),
    "macos": Path("bin/macos/unshuffle_extractor"),
    "linux": Path("bin/linux/unshuffle_extractor"),
}


def _current_platform() -> str:
    if os.name == "nt":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def _check_binary(path: Path, *, execute: bool = True) -> tuple[bool, str]:
    if not path.exists():
        return False, f"missing: {path}"
    if not path.is_file():
        return False, f"not a file: {path}"
    if not execute:
        return True, f"present: {path} (version check skipped on this platform)"
    try:
        result = subprocess.run(
            [str(path), "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except OSError as exc:
        return False, f"could not execute {path}: {exc}"
    except subprocess.TimeoutExpired:
        return False, f"version probe timed out: {path}"

    version = (result.stdout or "").strip()
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        return False, f"{path} --version failed with {result.returncode}: {stderr}"
    if version != EXPECTED_VERSION:
        return False, f"{path} reports {version!r}, expected {EXPECTED_VERSION!r}"
    schema_ok, schema_message = _check_schema_smoke(path)
    if not schema_ok:
        return False, schema_message
    return True, f"ok: {path} ({version}; schema smoke passed)"


def _write_probe_wav(path: Path) -> None:
    sample_rate = 44100
    duration_seconds = 0.25
    frame_count = int(sample_rate * duration_seconds)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        frames = bytearray()
        for idx in range(frame_count):
            value = int(12000 * math.sin(2.0 * math.pi * 440.0 * (idx / sample_rate)))
            frames.extend(pack("<h", value))
        handle.writeframes(bytes(frames))


def _check_schema_smoke(path: Path) -> tuple[bool, str]:
    with tempfile.TemporaryDirectory() as tmp:
        probe = Path(tmp) / "probe.wav"
        _write_probe_wav(probe)
        try:
            result = subprocess.run(
                [str(path), "--file", str(probe)],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except OSError as exc:
            return False, f"could not execute schema smoke for {path}: {exc}"
        except subprocess.TimeoutExpired:
            return False, f"schema smoke timed out: {path}"

    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        return False, f"{path} schema smoke failed with {result.returncode}: {stderr}"
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return False, f"{path} schema smoke emitted invalid JSON: {exc}"
    if payload.get("feature_space_version") != CURRENT_FEATURE_SPACE_VERSION:
        return False, f"{path} feature_space_version mismatch"
    if payload.get("extractor_version") != CURRENT_EXTRACTOR_VERSION:
        return False, f"{path} extractor_version mismatch"
    if tuple(payload.get("feature_schema") or ()) != CURRENT_FEATURE_SCHEMA:
        return False, f"{path} feature_schema mismatch"
    vector = payload.get("vector")
    if not isinstance(vector, list) or len(vector) != FEATURE_VECTOR_SIZE:
        return False, f"{path} vector length mismatch"
    return True, f"schema ok: {path}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate bundled native extractor artifacts.")
    parser.add_argument(
        "--all",
        action="store_true",
        help="Require Windows, macOS, and Linux extractor artifacts instead of only the current platform.",
    )
    args = parser.parse_args(argv)

    current_platform = _current_platform()
    names = PLATFORM_BINARIES.keys() if args.all else [current_platform]
    ok = True
    for name in names:
        passed, message = _check_binary(PLATFORM_BINARIES[name], execute=(name == current_platform))
        print(message)
        ok = ok and passed
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
