# Test Suite Map

The suite is organized by subsystem. Characterization tests protect established behavior at refactoring boundaries; narrower unit and integration tests cover newer components directly.

## Scan, Classification, and Audio

- `test_algorithm.py`, `test_discovery.py`, and `test_scan_structure.py`: discovery, planning, classification, and source-tree behavior.
- `test_hashing.py`, `test_pack_detection.py`, `test_tagging_pass.py`, and `test_layered_evidence.py`: staged classification passes and cache-safe reuse.
- `test_audio_characterization.py`, `test_audio_runtime_behavior.py`, `test_extractor_similarity.py`, and `test_frequency_characterization.py`: audio heuristics, native extraction, feature vectors, and similarity behavior.
- `test_scan_progress.py`, `test_concurrency.py`, and `test_resource_monitor.py`: phase progress, bounded worker use, cancellation, and scan telemetry.

## Persistence and Session State

- `persistence/`: schema, migration, CRUD, cache, maintenance, and store-level behavior.
- `test_database_handle_reuse.py`: ownership and reuse of the active global database and target-local mirror.
- `test_db_backed_staging_model.py` and `test_models.py`: large-session storage and model contracts.
- `test_coherence_persistence.py` and `test_coherence_hnsw.py`: sound-map/coherence persistence and index behavior.
- `test_config_characterization.py` and `test_runtime_config_policy.py`: persisted configuration compatibility and runtime policy.

## Runtime and Filesystem Safety

- `test_engine.py` and `test_runtime_characterization.py`: scan/build lifecycle, locking, session restoration, and undo.
- `test_execution_transfer.py` and `test_execution_duplicates.py`: copy/move execution and duplicate handling.
- `test_destructive_path_safety.py` and `test_path_safety_characterization.py`: containment, symlinks, tamper resistance, cleanup, and prefix-sibling safety.
- `test_path_support_characterization.py` and `test_platform_labels.py`: cross-platform paths and user-facing platform labels.

## GUI and Workflows

- `test_gui_model_characterization.py`: table/tree models, editing, drag-fill, and staging-session selection labels.
- `test_gui_search_characterization.py`, `test_query_characterization.py`, `test_search_prefixes.py`, and `test_tokenizer_characterization.py`: query parsing, filter composition, and search wiring.
- `test_gui_workflow_characterization.py`, `test_footer_state.py`, and `test_history_page.py`: main-window workflows, view/session state, footer controls, and history.
- `test_gui_theme_characterization.py` and `test_dock_appearance.py`: theme assets, native tooltips, adaptive/docked appearance, and application version display.
- `test_gui_shortcuts_characterization.py`: keyboard and editing shortcuts.
- `test_coherence_controller.py`, `test_coherence_engine.py`, and `test_coherence_service.py`: sound-map data flow, filtering, caches, and analysis orchestration.

## Import, Export, and Long Operations

- `test_import_workers.py`, `test_session_import_dialog.py`, and `test_operation_monitor.py`: background CSV/staging imports, session choice UI, progress, and cancellation.
- `test_gui_workflow_characterization.py` and persistence export tests also cover metadata preservation and global/local database synchronization.
- `test_tree_organization.py`: custom structures, filter-backed nodes, validation, and semantic edits.

## Architecture, Diagnostics, and Releases

- `test_imports.py`, `test_layer_import_guard.py`, `test_bridge_characterization.py`, and `test_cli_bridge_characterization.py`: public imports and layer boundaries.
- `test_diagnostics_characterization.py` and `test_file_diagnostics.py`: crash/native-component reports and file-level explanations.
- `test_release_data_validation.py`, `test_release_tooling.py`, `test_updates.py`, and `test_performance_baselines.py`: release data, platform packaging, update feeds, and performance smoke gates.

## Running Tests

Install contributor tooling, then run from the repository root:

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

For a focused run, pass a test module or node ID, for example:

```bash
python -m pytest -q tests/test_import_workers.py
python -m pytest -q tests/test_gui_workflow_characterization.py::MainWindowDebounceTests
```

The runtime-only `requirements.txt` intentionally omits test and type-check packages.
