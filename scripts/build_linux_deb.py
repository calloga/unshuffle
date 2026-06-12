from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path


APP_NAME = "Unshuffle"
PACKAGE_NAME = "unshuffle"
MAINTAINER = "UmU"


def _copy_tree(source: Path, destination: Path) -> None:
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source, destination)


def _write_text(path: Path, text: str, *, executable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    if executable:
        path.chmod(0o755)


def build_deb(repo_root: Path, *, version: str, source_dir: Path, output_dir: Path, arch: str = "amd64") -> Path:
    source = source_dir if source_dir.is_absolute() else repo_root / source_dir
    if not source.exists():
        raise FileNotFoundError(f"App bundle not found: {source}")

    package_root = output_dir / "deb" / f"{PACKAGE_NAME}_{version}_{arch}"
    if package_root.exists():
        shutil.rmtree(package_root)

    app_dest = package_root / "opt" / PACKAGE_NAME
    _copy_tree(source, app_dest)

    control = f"""Package: {PACKAGE_NAME}
Version: {version}
Section: utils
Priority: optional
Architecture: {arch}
Maintainer: {MAINTAINER}
Description: Producer-first sample-library staging and migration tool.
"""
    _write_text(package_root / "DEBIAN" / "control", control)

    launcher = """#!/usr/bin/env sh
exec /opt/unshuffle/Unshuffle "$@"
"""
    _write_text(package_root / "usr" / "bin" / "unshuffle", launcher, executable=True)

    desktop = """[Desktop Entry]
Type=Application
Name=Unshuffle
Comment=Producer-first sample-library staging and migration tool
Exec=/opt/unshuffle/Unshuffle
Icon=unshuffle
Terminal=false
Categories=Audio;AudioVideo;Utility;
StartupWMClass=Unshuffle
"""
    _write_text(package_root / "usr" / "share" / "applications" / "unshuffle.desktop", desktop)

    icon_source = repo_root / "icons" / "app_logo.png"
    icon_dest = package_root / "usr" / "share" / "icons" / "hicolor" / "256x256" / "apps" / "unshuffle.png"
    icon_dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(icon_source, icon_dest)

    output_dir.mkdir(parents=True, exist_ok=True)
    deb_path = output_dir / f"{PACKAGE_NAME}_{version}_{arch}.deb"
    subprocess.run(["dpkg-deb", "--build", str(package_root), str(deb_path)], cwd=repo_root, check=True)
    return deb_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the Linux .deb installer package for Unshuffle.")
    parser.add_argument("--version", default="1.0.0")
    parser.add_argument("--source-dir", type=Path, default=Path("dist") / APP_NAME)
    parser.add_argument("--output-dir", type=Path, default=Path("dist") / "installer")
    parser.add_argument("--arch", default="amd64")
    args = parser.parse_args(argv)

    repo_root = Path(__file__).resolve().parents[1]
    deb_path = build_deb(
        repo_root,
        version=args.version,
        source_dir=args.source_dir,
        output_dir=repo_root / args.output_dir,
        arch=args.arch,
    )
    print(f"Built {deb_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
