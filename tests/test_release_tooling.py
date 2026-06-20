from __future__ import annotations

import sys
from pathlib import Path


def test_native_extractor_build_validation_uses_bundle_checker(monkeypatch) -> None:
    from scripts import build_native_extractor

    calls = []

    def fake_run(command: list[str], *, cwd: Path) -> None:
        calls.append((command, cwd))

    repo_root = Path("repo")
    monkeypatch.setattr(build_native_extractor, "_run", fake_run)

    build_native_extractor._validate_copied_binary(repo_root)

    assert calls == [
        (
            [sys.executable, str(repo_root / "scripts" / "check_native_extractor_bundle.py")],
            repo_root,
        )
    ]


def test_app_binary_builder_wraps_gui_launcher_and_assets(monkeypatch) -> None:
    from scripts import build_app_binary

    monkeypatch.setattr(build_app_binary.os, "name", "nt")
    repo_root = Path("repo")

    def fake_exists(path: Path) -> bool:
        return path.parts[-1] in {
            "config.json",
            "anchors",
            "metadata",
            "taxonomy",
            "icons",
            "bin",
            "app_logo.ico",
        }

    monkeypatch.setattr(Path, "exists", fake_exists)

    command = build_app_binary.pyinstaller_command(
        repo_root,
        dist_dir=repo_root / "release-dist",
        work_dir=repo_root / "release-build",
        spec_dir=repo_root / "release-spec",
    )

    assert command[:7] == [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--windowed",
        "--name",
        "Unshuffle",
    ]
    assert str(repo_root / "scripts" / "app_binary_entrypoint.py") == command[-1]
    assert "--paths" in command
    assert str(repo_root) == command[command.index("--paths") + 1]
    assert ["--collect-binaries", "numpy"] == command[
        command.index("--collect-binaries"):command.index("--collect-binaries") + 2
    ]
    assert ["--collect-submodules", "numpy"] == command[
        command.index("--collect-submodules"):command.index("--collect-submodules") + 2
    ]
    assert ["--icon", str(repo_root / "icons" / "app_logo.ico")] == command[command.index("--icon"):command.index("--icon") + 2]
    assert ["--distpath", str(repo_root / "release-dist")] == command[command.index("--distpath"):command.index("--distpath") + 2]
    add_data_values = [
        command[index + 1]
        for index, value in enumerate(command)
        if value == "--add-data"
    ]
    assert str(repo_root / "data" / "config.json") + ";data" in add_data_values
    assert str(repo_root / "data" / "anchors") + ";data/anchors" in add_data_values
    assert str(repo_root / "data" / "metadata") + ";data/metadata" in add_data_values
    assert str(repo_root / "data" / "taxonomy") + ";data/taxonomy" in add_data_values
    assert str(repo_root / "icons") + ";icons" in add_data_values
    assert str(repo_root / "bin") + ";bin" in add_data_values
    assert str(repo_root / "data") + ";data" not in add_data_values


def test_app_binary_builder_uses_platform_icons(monkeypatch) -> None:
    from scripts import build_app_binary

    repo_root = Path("repo")

    def fake_exists(path: Path) -> bool:
        return str(path).replace("\\", "/") in {
            "repo/build/app_icon.icns",
            "repo/icons/app_logo.png",
            "repo/icons/icon.png",
        }

    monkeypatch.setattr(Path, "exists", fake_exists)

    monkeypatch.setattr(build_app_binary.sys, "platform", "darwin")
    assert build_app_binary._icon_arg(repo_root) == ["--icon", str(repo_root / "build" / "app_icon.icns")]

    monkeypatch.setattr(build_app_binary.sys, "platform", "linux")
    assert build_app_binary._icon_arg(repo_root) == ["--icon", str(repo_root / "icons" / "app_logo.png")]


def test_app_binary_expected_path_matches_platform(monkeypatch) -> None:
    from scripts import build_app_binary

    repo_root = Path("repo")
    monkeypatch.setattr(build_app_binary.sys, "platform", "win32")
    assert build_app_binary.expected_app_path(repo_root, "Unshuffle") == repo_root / "dist" / "Unshuffle" / "Unshuffle.exe"
    monkeypatch.setattr(build_app_binary.sys, "platform", "darwin")
    assert build_app_binary.expected_app_path(repo_root, "Unshuffle") == repo_root / "dist" / "Unshuffle.app"
    monkeypatch.setattr(build_app_binary.sys, "platform", "linux")
    assert build_app_binary.expected_app_path(repo_root, "Unshuffle") == repo_root / "dist" / "Unshuffle" / "Unshuffle"


def test_linux_deb_builder_writes_desktop_launcher_and_icon(tmp_path, monkeypatch) -> None:
    from scripts import build_linux_deb

    repo_root = tmp_path / "repo"
    source_dir = repo_root / "dist" / "Unshuffle"
    icon_dir = repo_root / "icons"
    source_dir.mkdir(parents=True)
    icon_dir.mkdir()
    (source_dir / "Unshuffle").write_text("binary", encoding="utf-8")
    (icon_dir / "app_logo.png").write_bytes(b"png")

    calls = []

    def fake_run(command: list[str], *, cwd: Path, check: bool) -> None:
        calls.append((command, cwd, check))

    monkeypatch.setattr(build_linux_deb.subprocess, "run", fake_run)

    deb_path = build_linux_deb.build_deb(
        repo_root,
        version="1.0.0",
        source_dir=Path("dist") / "Unshuffle",
        output_dir=repo_root / "dist" / "installer",
    )

    package_root = repo_root / "dist" / "installer" / "deb" / "unshuffle_1.0.0_amd64"
    assert (package_root / "opt" / "unshuffle" / "Unshuffle").exists()
    assert (package_root / "usr" / "bin" / "unshuffle").read_text(encoding="utf-8").startswith("#!/usr/bin/env sh")
    desktop_text = (package_root / "usr" / "share" / "applications" / "unshuffle.desktop").read_text(encoding="utf-8")
    assert "Name=Unshuffle" in desktop_text
    assert "Icon=unshuffle" in desktop_text
    assert (package_root / "usr" / "share" / "icons" / "hicolor" / "256x256" / "apps" / "unshuffle.png").exists()
    assert deb_path == repo_root / "dist" / "installer" / "unshuffle_1.0.0_amd64.deb"
    assert calls == [
        (
            ["dpkg-deb", "--build", str(package_root), str(deb_path)],
            repo_root,
            True,
        )
    ]


def test_windows_installer_script_registers_app_shortcuts_and_logo() -> None:
    script = Path("../packaging/windows/unshuffle.iss").read_text(encoding="utf-8")

    assert "AppId={{9D84E78F-9EB3-47A7-A42C-86C9AD5F0E46}" in script
    assert "SetupIconFile=..\\..\\icons\\app_logo.ico" in script
    assert "AppVersion={#AppVersion}" in script
    assert "AppVerName={#AppName} {#AppVersion}" in script
    assert "OutputBaseFilename=UnshuffleWinSetup" in script
    assert "VersionInfoVersion={#AppVersionInfo}" in script
    assert "VersionInfoProductVersion={#AppVersionInfo}" in script
    assert "VersionInfoProductTextVersion={#AppVersion}" in script
    assert "UninstallDisplayIcon={app}\\_internal\\icons\\app_logo.ico" in script
    assert "UninstallDisplayName={#AppName}" in script
    assert 'Name: "{group}\\{#AppName}"' in script
    assert 'Name: "{autodesktop}\\{#AppName}"' in script
    assert 'Filename: "{app}\\Unshuffle.exe"' in script
    assert 'IconFilename: "{app}\\_internal\\icons\\app_logo.ico"' in script


def test_app_binary_workflow_uploads_installable_platform_artifacts() -> None:
    workflow = Path("../.github/workflows/app-binaries.yml").read_text(encoding="utf-8")

    assert "dist/installer/UnshuffleWinSetup.exe" in workflow
    assert "dist/installer/Unshuffle-macos.pkg" in workflow
    assert "dist/installer/unshuffle_1.0.0_amd64.deb" in workflow
    assert "choco install innosetup" in workflow
    assert "brew install ffmpeg" in workflow
    assert "sudo apt-get update && sudo apt-get install -y ffmpeg" in workflow
    assert "bash scripts/build_macos_pkg.sh" in workflow
    assert "python scripts/build_linux_deb.py" in workflow


def test_platform_packages_use_cropped_circular_app_logo() -> None:
    linux_builder = Path("../scripts/build_linux_deb.py").read_text(encoding="utf-8")
    macos_icon_script = Path("../scripts/prepare_macos_icon.sh").read_text(encoding="utf-8")

    assert 'repo_root / "icons" / "app_logo.png"' in linux_builder
    assert 'SOURCE_ICON="${1:-icons/app_logo.png}"' in macos_icon_script


def test_cropped_app_logo_fills_icon_canvas() -> None:
    from PIL import Image

    for path in (Path("../icons/app_logo.png"), Path("../icons/app_logo.ico")):
        image = Image.open(path).convert("RGBA")
        bbox = image.getbbox()
        assert bbox is not None
        width = bbox[2] - bbox[0]
        height = bbox[3] - bbox[1]
        assert width / image.width >= 0.90
        assert height / image.height >= 0.90
