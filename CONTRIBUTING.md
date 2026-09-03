# Contributing

Thanks for helping improve Unshuffle. Small, well-scoped changes are easiest to review, especially when they include focused validation.

## Setup

Use Python 3.11 or newer. Release binaries are currently built with Python 3.14.

```bash
python -m pip install -r requirements-dev.txt
python -m gui
```

`requirements-dev.txt` includes the runtime dependencies plus test/type-check tooling. 

The test-suite ownership map is documented in [`tests/README.md`](tests/README.md).

## Validation

Run the focused checks for the area you changed, then run the full gates before opening a larger pull request:

```bash
python -m compileall gui unshuffle tests
python scripts/check_layer_imports.py
python -m pyright
python -m unshuffle.validation.data_config
python scripts/check_native_extractor_bundle.py --all
python scripts/performance_baselines.py --quick
python -m pytest -q
```

## Development Notes

- Keep GUI code on the GUI side of the bridge/runtime boundary.
- Keep backend logic in `unshuffle/` rather than importing GUI modules into backend modules.
- Treat filesystem operations as safety-sensitive. Prefer dry-run/compare-first behavior and add tests for destructive paths.
- `python -m compileall ...` writes `__pycache__` directories; they are local generated files and should not be committed.
- Do not commit local databases, lock files, profiling output, build trees, virtual environments, or machine-local settings.
- Use `.git/info/exclude` for personal build or profiling paths that should remain untracked without changing the shared `.gitignore`.
- Native extractor source lives in `unshuffle_extractor/`; public release artifacts must include Windows, macOS, and Linux extractors. Build the current platform with `python scripts/build_native_extractor.py --copy-to-bin`; that command copies the binary and validates the current platform artifact.
- The GUI checks `https://api.github.com/repos/calloga/unshuffle/releases/latest` for updates. Publish platform installers as GitHub Release assets so installed apps can detect and link users to the newest installer: Windows prefers `.exe`/`.msi`, macOS prefers `.pkg`/`.dmg`, and Linux prefers `.deb`/`.rpm`/`.AppImage`/`.tar.gz`. Set `UNSHUFFLE_UPDATE_FEED_URL` to test a custom JSON feed with `version`, `url`, and optional `download_url`.

## Release Checklist

- Add the release notes and date to `CHANGELOG.md` and update versioned installer names in `README.md`.
- Keep the application version synchronized in `pyproject.toml`, `unshuffle/core/constants.py`, platform packaging scripts, the Windows installer definition, the app-binaries workflow, and their characterization tests.
- Do not bump the native extractor's `1.0.0` protocol version unless its command-line contract or feature-vector schema changes.
- Keep package identities stable across patch releases. The macOS app and package use bundle ID `com.umu.unshuffle`. The Windows Inno Setup App ID is `{9D84E78F-9EB3-47A7-A42C-86C9AD5F0E46}`; enter `{9D84E78F-9EB3-47A7-A42C-86C9AD5F0E46}_is1` as MuseHub's Windows application ID because MuseHub identifies the uninstall-registry key.
- Keep release support floors synchronized with packaging and CI: Windows 10 version 1809 (build 17763), macOS 13, and Ubuntu 24.04 x86-64 (or a compatible newer Linux distribution).
- Run the full validation suite and build each platform artifact before publishing the GitHub Release.

## Pull Request Checklist

- Explain the user-facing behavior change.
- Include tests or explain why tests are not practical for the change.
- Run the relevant validation commands.
- Note any binary/native-extractor implications.
- Note any database migration, sidecar compatibility, or release-packaging implications.
