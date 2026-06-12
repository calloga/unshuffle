# Contributing

Thanks for helping improve Unshuffle. Small, well-scoped changes are easiest to review, especially when they include focused validation.

## Setup

```bash
python -m pip install -r requirements-dev.txt
python -m gui
```

`requirements-dev.txt` includes the runtime dependencies plus test/type-check tooling. 

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
- Native extractor source lives in `unshuffle_extractor/`; public release artifacts must include Windows, macOS, and Linux extractors. Build the current platform with `python scripts/build_native_extractor.py --copy-to-bin`; that command copies the binary and validates the current platform artifact.

## Pull Request Checklist

- Explain the user-facing behavior change.
- Include tests or explain why tests are not practical for the change.
- Run the relevant validation commands.
- Note any binary/native-extractor implications.
