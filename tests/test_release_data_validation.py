from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from unshuffle.validation.data_config import validate_release_data


def test_checked_in_release_data_validates() -> None:
    issues = validate_release_data(Path(__file__).resolve().parents[1])
    assert issues == []


def test_release_data_validator_reports_taxonomy_shape_errors(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    taxonomy_dir = data_dir / "taxonomy"
    metadata_dir = data_dir / "metadata"
    taxonomy_dir.mkdir(parents=True)
    metadata_dir.mkdir()
    (data_dir / "config.json").write_text(json.dumps({"LOG_LEVEL": "INFO"}), encoding="utf-8")
    (taxonomy_dir / "broken.json").write_text(
        json.dumps({"category": "Broken", "taxonomy": {"no-sub": ["kick", "kick", 123]}}),
        encoding="utf-8",
    )
    (metadata_dir / "genre_relationships.json").write_text(
        json.dumps({"music": {"families": {"dance": ["house"]}}}),
        encoding="utf-8",
    )

    messages = [issue.message for issue in validate_release_data(tmp_path)]

    assert any("duplicate value: kick" in message for message in messages)
    assert any("no-sub[2] must be a non-empty string" in message for message in messages)


def test_release_data_validator_reports_system_anchor_errors(tmp_path: Path) -> None:
    from unshuffle.core.features import CURRENT_FEATURE_SCHEMA, CURRENT_EXTRACTOR_VERSION, CURRENT_FEATURE_SPACE_VERSION

    data_dir = tmp_path / "data"
    taxonomy_dir = data_dir / "taxonomy"
    metadata_dir = data_dir / "metadata"
    anchors_dir = data_dir / "anchors"
    taxonomy_dir.mkdir(parents=True)
    metadata_dir.mkdir()
    anchors_dir.mkdir()
    (data_dir / "config.json").write_text(json.dumps({"LOG_LEVEL": "INFO"}), encoding="utf-8")
    (taxonomy_dir / "bass.json").write_text(
        json.dumps({"category": "Bass", "taxonomy": {"no-sub": ["bass"]}}),
        encoding="utf-8",
    )
    (metadata_dir / "genre_relationships.json").write_text(
        json.dumps({"music": {"families": {"dance": ["house"]}}}),
        encoding="utf-8",
    )
    vector = [0.1] * len(CURRENT_FEATURE_SCHEMA)
    bad_anchor = {
        "profile_type": "cluster_anchor",
        "anchor_id": "anchor-bad",
        "cluster_id": "cluster",
        "audio_type": "Loops",
        "category": "Bass",
        "subcategory": "",
        "features": {
            "feature_space_version": CURRENT_FEATURE_SPACE_VERSION,
            "extractor_version": CURRENT_EXTRACTOR_VERSION,
            "normalization_version": "unshuffle-norm-v1",
            "vector_schema": list(CURRENT_FEATURE_SCHEMA[:-1]),
            "medoid_vector": vector,
            "cluster_centroid": vector,
            "cluster_std": [0.01],
            "coherence_radius": 0.2,
        },
        "evidence": {"n_reference_items": 5},
        "privacy": {
            "contains_audio": False,
            "contains_filenames": False,
            "contains_paths": False,
            "contains_vendor_names": False,
            "contains_artist_names": False,
            "contains_folder_structure": False,
        },
    }
    (anchors_dir / "system_anchors.json").write_text(json.dumps([bad_anchor]), encoding="utf-8")

    messages = [issue.message for issue in validate_release_data(tmp_path)]

    assert any("vector_schema must match current feature schema" in message for message in messages)
    assert any("cluster_std must be a valid current-schema vector" in message for message in messages)


def test_release_data_validation_module_command_passes() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "unshuffle.validation.data_config"],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "validation passed" in result.stdout.lower()
