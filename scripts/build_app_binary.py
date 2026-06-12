from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


APP_NAME = "Unshuffle"
ASSET_PATHS = (
    ("data/config.json", "data"),
    ("data/anchors", "data/anchors"),
    ("data/metadata", "data/metadata"),
    ("data/taxonomy", "data/taxonomy"),
    ("icons", "icons"),
    ("bin", "bin"),
)
ICON_CANDIDATES = {
    "win32": ("icons/app_logo.ico", "icons/icon.png", "icons/app_icon.ico"),
    "darwin": ("build/app_icon.icns", "icons/app_icon.icns", "icons/icon.png"),
    "linux": ("icons/app_logo.png", "icons/icon.png", "icons/app_icon.ico"),
}


def _data_separator() -> str:
    return ";" if os.name == "nt" else ":"


def _add_data_arg(source: Path, destination: str) -> str:
    return f"{source}{_data_separator()}{destination}"


def _asset_args(repo_root: Path) -> list[str]:
    args: list[str] = []
    for source_name, destination in ASSET_PATHS:
        source = repo_root / source_name
        if source.exists():
            args.extend(["--add-data", _add_data_arg(source, destination)])
    return args


def _icon_arg(repo_root: Path) -> list[str]:
    candidates = ICON_CANDIDATES.get(sys.platform, ICON_CANDIDATES["linux"])
    for relative_path in candidates:
        icon_path = repo_root / relative_path
        if icon_path.exists():
            return ["--icon", str(icon_path)]
    return []


def app_entrypoint(repo_root: Path) -> Path:
    return repo_root / "scripts" / "app_binary_entrypoint.py"


def expected_app_path(repo_root: Path, name: str, dist_dir: Path | None = None) -> Path:
    dist_root = dist_dir or repo_root / "dist"
    if sys.platform == "win32":
        return dist_root / name / f"{name}.exe"
    if sys.platform == "darwin":
        return dist_root / f"{name}.app"
    return dist_root / name / name


def pyinstaller_command(
    repo_root: Path,
    *,
    name: str = APP_NAME,
    dist_dir: Path | None = None,
    work_dir: Path | None = None,
    spec_dir: Path | None = None,
    clean: bool = True,
) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--windowed",
        "--name",
        name,
        "--paths",
        str(repo_root),
        "--collect-binaries",
        "numpy",
        "--collect-submodules",
        "numpy",
    ]
    if clean:
        command.append("--clean")
    if dist_dir is not None:
        command.extend(["--distpath", str(dist_dir)])
    if work_dir is not None:
        command.extend(["--workpath", str(work_dir)])
    if spec_dir is not None:
        command.extend(["--specpath", str(spec_dir)])
    command.extend(_icon_arg(repo_root))
    command.extend(_asset_args(repo_root))
    command.append(str(app_entrypoint(repo_root)))
    return command


def _run(command: list[str], *, cwd: Path) -> None:
    subprocess.run(command, cwd=cwd, check=True)


def _validate_app_binary(repo_root: Path, *, name: str, dist_dir: Path | None) -> Path:
    app_path = expected_app_path(repo_root, name, dist_dir)
    if not app_path.exists():
        raise FileNotFoundError(f"Expected app binary was not created: {app_path}")
    return app_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the standalone Unshuffle GUI app for the current platform.")
    parser.add_argument("--name", default=APP_NAME, help="Application/binary name.")
    parser.add_argument("--dist-dir", type=Path, default=None, help="PyInstaller output directory.")
    parser.add_argument("--work-dir", type=Path, default=None, help="PyInstaller work directory.")
    parser.add_argument("--spec-dir", type=Path, default=None, help="PyInstaller spec output directory.")
    parser.add_argument("--no-clean", action="store_true", help="Do not pass --clean to PyInstaller.")
    parser.add_argument("--skip-output-check", action="store_true", help="Skip checking that the app output exists.")
    args = parser.parse_args(argv)

    repo_root = Path(__file__).resolve().parents[1]
    command = pyinstaller_command(
        repo_root,
        name=args.name,
        dist_dir=args.dist_dir,
        work_dir=args.work_dir,
        spec_dir=args.spec_dir,
        clean=not args.no_clean,
    )
    _run(command, cwd=repo_root)
    if not args.skip_output_check:
        app_path = _validate_app_binary(repo_root, name=args.name, dist_dir=args.dist_dir)
        print(f"Built {app_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
