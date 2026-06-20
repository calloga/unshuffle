# Unshuffle

Unshuffle is a producer-first sample-library staging and migration tool.

It scans messy source folders, classifies files into a persistent virtual library, lets you review and edit that library in a GUI, and then optionally builds it onto your hard drive, so you can access your organized samples in and out of the app.

## Core Capabilities

- Scans one or multiple directories and returns one persistent library.
- Classifies samples by `category`, `subcategory`, and `audio type`.
- Allows for custom re-organization of your samples after classification, so you may choose the structure of your library.
- Surfaces possible duplicates, confirmed duplicates, and corrupt/silent files in your library.
- Provides multiple ways to interact with your organized library (through Table, Tree and Map library views).
- Enables quick structured text-and-similarity-based exploration of your samples.
- Allows Compare-first build dialog (`Current Directories` vs `After Migration`) before execution. Building a virtual library persists your validated organization onto the target.
- Provides build history with undo actions, for safety.

## Install

Download the installer for your operating system:

- Windows: `UnshuffleWinSetup.exe`
- macOS: `Unshuffle-macos.pkg`
- Linux: `unshuffle_1.0.0_amd64.deb`

## Source Setup

You need libpulse0 library for development.
For Ubuntu\Linux:
```bash
sudo apt update
sudo apt install libpulse0
sudo apt install libva-x11-2
sudo apt install libva-drm2 
```

```bash
python -m pip install -r requirements-dev.txt
python -m gui
```
Entry points:
- GUI: `python -m gui`
- CLI: `python -m unshuffle.cli`

## Quick Workflow

1. Launch GUI and scan one or more source folders.
2. Review and update staged files in table, tree, or map views.
3. Refine with query filters, saved filters, category/type controls, and confidence range.
4. Open `Build`, choose a target directory, validate the compare view, then execute copy/move.
5. Use `History` for undo/maintenance actions.

## Search Basics

- AND: `,`, `AND`, `&`
- OR: `OR`, `|`
- Prefixes: `cat`, `sub`, `pack`, `file`, `tag`, `type`, `source`, `path`, `confidence`
- Similarity: `similar:<row_id>`

Examples:
- `cat:"Kicks", type:"Loops"`
- `cat:"Kicks" OR cat:"Snares"`
- `source:"D:/Samples/Drums"`
- `similar:12, cat:"Kicks"`

## Architecture (Current)

Layered backend:
- `unshuffle/core`
- `unshuffle/persistence`
- `unshuffle/audio`
- `unshuffle/logic`
- `unshuffle/runtime`
- `unshuffle/bridge`

App surfaces:
- GUI (`gui/`) uses bridge/runtime-backed orchestration.
- CLI uses the same backend seams.

## Similarity Extractor

Acoustic similarity depends on the native extractor (`unshuffle_extractor` on macOS/Linux, `unshuffle_extractor.exe` on Windows) and the source in `unshuffle_extractor/`.

- Classification/staging still work without it.
- Similarity degrades gracefully when vectors are unavailable.
- Packaged builds should include the extractor for the target platform under `bin/<platform>/`.
- Source builds can set `UNSHUFFLE_EXTRACTOR_PATH` to an explicit extractor path.

Build from source:

```bash
python scripts/build_native_extractor.py --copy-to-bin
```

The V1 extractor contract reports `unshuffle_extractor 1.0.0` and emits current-schema 20-feature vectors.

## Persistence and Locking

Global metadata dir:
- Windows: `%APPDATA%\\Unshuffle`
- macOS: `~/Library/Application Support/Unshuffle`
- Linux: `~/.config/Unshuffle`

Target sidecar:
- `DO_NOT_DELETE_unshuffle/unshuffle.db`

Locking:
- `<target>/.unshuffle/lock.json`
- Force takeover env: `UNSHUFFLE_FORCE_LOCK_TAKEOVER=1`
- Stale lock threshold env: `UNSHUFFLE_LOCK_STALE_MINUTES`

## Documentation Map

- User manual: maintained in the portfolio docs so readers always see the latest published information.
- [Contributing Guide](CONTRIBUTING.md)
- [Security Policy](SECURITY.md)
- [Changelog](CHANGELOG.md)
- [Notices](NOTICE.md)

## Validation Commands

- Compile: `python -m compileall gui unshuffle tests`
- Layer guard: `python scripts/check_layer_imports.py`
- Type check: `python -m pyright`
- Data/config gate: `python -m unshuffle.validation.data_config`
- Native bundle gate: `python scripts/check_native_extractor_bundle.py --all`
- Performance smoke: `python scripts/performance_baselines.py --quick`
- Tests: `python -m pytest -q`
