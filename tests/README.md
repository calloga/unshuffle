# Test Characterization Map

This test suite is organized by subsystem so the reorg can preserve behavior with clearer failure signals.

- `test_algorithm.py`: end-to-end planning/classification characterization on synthetic directory trees
- `test_audio_characterization.py`: audio-type heuristics and vector decoding/distance behavior
- `test_cli_validation.py`: CLI argument validation rules
- `test_db.py`: database CRUD and transaction behavior
- `test_diagnostics_characterization.py`: crash-log and native-component reporting
- `test_destructive_path_safety.py`: destination containment, undo tamper, duplicate trash, preserved-root, symlink, and prefix-sibling safety
- `test_discovery.py`: discovery helper behavior and import workflow
- `test_engine.py`: targeted engine execution and undo behaviors
- `test_extractor_similarity.py`: extractor similarity smoke coverage
- `test_file_diagnostics.py`: file-level scoring explanation module
- `test_gui_model_characterization.py`: staging/table/tree model behavior
- `test_gui_search_characterization.py`: search controllers, proxy-only filters, and UI search wiring
- `test_gui_shortcuts_characterization.py`: keyboard shortcut and drag-fill behaviors
- `test_gui_workflow_characterization.py`: main-window, workflow restore, and view-state behavior
- `test_hashing.py`: file hashing behavior and interruption safety
- `test_imports.py`: import smoke tests and circular dependency checks
- `test_models.py`: dataclass defaults and coercion behavior
- `test_persistence.py`: persistence path and V1 cache contract behavior
- `test_persistence_characterization.py`: DB-backed persistence, config sync, and data-access characterization
- `test_query_characterization.py`: FTS/search contract characterization
- `test_release_data_validation.py`: release data/config/taxonomy validation gate
- `test_performance_baselines.py`: synthetic performance-baseline command smoke coverage
- `test_runtime_characterization.py`: engine lock, session persistence, and undo safety
- `test_search_prefixes.py`: query prefix handling smoke coverage

The goal is characterization first: each file should map to one primary concern so reorg failures are localized and easier to interpret.

Run tests from the repository root with `python -m pytest`. Install contributor tooling first with `python -m pip install -r requirements-dev.txt`; the runtime-only `requirements.txt` intentionally omits test and type-check packages.
