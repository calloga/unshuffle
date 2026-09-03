from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


APP_NAME = "Unshuffle"
PACKAGE_NAME = "unshuffle"


def _copy_tree(source: Path, destination: Path) -> None:
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source, destination)


def _write_text(path: Path, text: str, *, executable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    if executable:
        path.chmod(0o755)


def find_appimagetool(custom_path: Path | None = None, repo_root: Path | None = None) -> Path | None:
    if custom_path:
        if custom_path.exists():
            return custom_path
        print(f"Warning: Custom appimagetool path does not exist: {custom_path}", file=sys.stderr)

    # Check system PATH
    tool = shutil.which("appimagetool")
    if tool:
        return Path(tool)

    # Check local bin/linux paths
    if repo_root:
        local_candidates = [
            repo_root / "bin" / "linux" / "appimagetool-x86_64.AppImage",
            repo_root / "bin" / "linux" / "appimagetool",
        ]
        for candidate in local_candidates:
            if candidate.exists():
                return candidate

    return None


def build_appimage(
    repo_root: Path,
    *,
    version: str,
    source_dir: Path,
    output_dir: Path,
    appimagetool_path: Path | None = None,
) -> Path:
    source = source_dir if source_dir.is_absolute() else repo_root / source_dir
    if not source.exists():
        raise FileNotFoundError(f"App bundle not found at: {source}")

    tool_bin = find_appimagetool(appimagetool_path, repo_root)
    if not tool_bin:
        print("\n" + "=" * 80, file=sys.stderr)
        print("ERROR: 'appimagetool' was not found on your system.", file=sys.stderr)
        print("To package Unshuffle as an AppImage, you must either:", file=sys.stderr)
        print("  1. Install it via your package manager (e.g., sudo apt install appimagetool)", file=sys.stderr)
        print("  2. Download it from: https://github.com/AppImage/AppImageKit/releases", file=sys.stderr)
        print("     and make it executable, then place it in your PATH or under:", file=sys.stderr)
        print("     bin/linux/appimagetool-x86_64.AppImage", file=sys.stderr)
        print("  3. Pass its path directly via: --appimagetool-path /path/to/appimagetool", file=sys.stderr)
        print("=" * 80 + "\n", file=sys.stderr)
        raise RuntimeError("appimagetool not found")

    appdir = output_dir / "AppDir"
    if appdir.exists():
        shutil.rmtree(appdir)
    appdir.mkdir(parents=True, exist_ok=True)

    # Copy PyInstaller bundle contents directly to AppDir root
    print(f"Copying app bundle to {appdir}...")
    for item in source.iterdir():
        if item.is_dir():
            shutil.copytree(item, appdir / item.name)
        else:
            shutil.copy2(item, appdir / item.name)

    # 1. Create AppRun script at root
    apprun_content = """#!/bin/sh
SELF=$(readlink -f "$0")
HERE=$(dirname "$SELF")
exec "$HERE/Unshuffle" "$@"
"""
    _write_text(appdir / "AppRun", apprun_content, executable=True)

    # 2. Create desktop entry at root
    desktop_content = """[Desktop Entry]
Type=Application
Name=Unshuffle
Comment=Producer-first sample-library staging and migration tool
Exec=Unshuffle
Icon=unshuffle
Terminal=false
Categories=Audio;AudioVideo;Utility;
StartupWMClass=Unshuffle
"""
    _write_text(appdir / "unshuffle.desktop", desktop_content)

    # 3. Copy app logo to root
    icon_source = repo_root / "icons" / "app_logo.png"
    if icon_source.exists():
        shutil.copy2(icon_source, appdir / "unshuffle.png")

    output_dir.mkdir(parents=True, exist_ok=True)
    appimage_name = f"Unshuffle-{version}-x86_64.AppImage"
    appimage_path = output_dir / appimage_name

    # appimagetool requires ARCH env variable to be set
    env = os.environ.copy()
    if "ARCH" not in env:
        env["ARCH"] = "x86_64"

    print(f"Running appimagetool from: {tool_bin}")
    subprocess.run([str(tool_bin), str(appdir), str(appimage_path)], env=env, check=True)

    # Clean up AppDir on success
    shutil.rmtree(appdir)
    return appimage_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the Linux AppImage package for Unshuffle.")
    parser.add_argument("--version", default="1.1.1")
    parser.add_argument("--source-dir", type=Path, default=Path("dist") / APP_NAME)
    parser.add_argument("--output-dir", type=Path, default=Path("dist") / "installer")
    parser.add_argument("--appimagetool-path", type=Path, default=None, help="Path to appimagetool binary.")
    args = parser.parse_args(argv)

    repo_root = Path(__file__).resolve().parents[1]
    try:
        appimage_path = build_appimage(
            repo_root,
            version=args.version,
            source_dir=args.source_dir,
            output_dir=repo_root / args.output_dir,
            appimagetool_path=args.appimagetool_path,
        )
        print(f"Built {appimage_path}")
        return 0
    except Exception as exc:
        print(f"Error building AppImage: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
