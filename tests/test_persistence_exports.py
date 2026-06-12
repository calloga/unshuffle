import csv
import json
from pathlib import Path

from unshuffle.bridge.persistence_bridge import PersistenceBridge
from gui.core.data_manager import DataManager
from unshuffle.core import PlanRecord
from unshuffle.persistence import UnshuffleDB
from unshuffle.persistence.exports import (
    build_metadata_backup,
    build_taxonomy_snapshot,
    export_metadata_backup,
    export_staging_plan_csv,
    export_taxonomy_snapshot,
)


def test_build_taxonomy_snapshot_matches_v1_export_shape(tmp_path: Path):
    (tmp_path / "kicks.json").write_text(
        '{"category":"Kicks","taxonomy":{"no-sub":["kick","boom"],"Punchy":["thump"]}}',
        encoding="utf-8",
    )

    snapshot = build_taxonomy_snapshot(tmp_path)

    assert snapshot["Kicks"]["main_aliases"] == ["boom", "kick", "thump"]
    assert snapshot["Kicks"]["sub_taxonomy"]["Punchy"] == ["thump"]


def test_export_taxonomy_snapshot_writes_json(tmp_path: Path):
    taxonomy_dir = tmp_path / "taxonomy"
    taxonomy_dir.mkdir()
    (taxonomy_dir / "fx.json").write_text(
        '{"category":"FX","taxonomy":{"Impacts":["boom"]}}',
        encoding="utf-8",
    )
    output_path = tmp_path / "snapshot.json"

    written = export_taxonomy_snapshot(output_path, taxonomy_dir)

    assert written == output_path
    data = json.loads(output_path.read_text(encoding="utf-8"))
    assert data["FX"]["main_aliases"] == ["boom"]


def test_export_staging_plan_csv_matches_gui_shape(tmp_path: Path):
    output = tmp_path / "plan.csv"
    records = [
        PlanRecord(
            source_path=Path("Source/Kicks/kick.wav"),
            pack="Pack",
            category="Kicks",
            subcategory="Punchy",
            audio_type="Oneshots",
            tags=["bright", "short"],
            hash="",
            confidence="1.0",
        )
    ]

    export_staging_plan_csv(output, records)

    with open(output, "r", encoding="utf-8", newline="") as file_handle:
        rows = list(csv.DictReader(file_handle))
    assert rows == [
        {
            "source_directory": "Source\\Kicks",
            "source_filename": "kick.wav",
            "pack": "Pack",
            "category": "Kicks",
            "subcategory": "Punchy",
            "audio_type": "Oneshots",
            "tags": "bright short",
        }
    ]


def test_data_manager_export_to_csv_delegates_to_persistence_export(tmp_path: Path):
    output = tmp_path / "delegated.csv"
    manager = DataManager()
    records = [
        PlanRecord(
            source_path=Path("Source/Loops/loop.wav"),
            pack="Pack",
            category="Melodics",
            subcategory="Pads",
            audio_type="Loops",
            tags=["warm"],
            hash="",
            confidence="1.0",
        )
    ]

    assert manager.export_to_csv(output, records) is True

    with open(output, "r", encoding="utf-8", newline="") as file_handle:
        rows = list(csv.DictReader(file_handle))
    assert rows[0]["source_filename"] == "loop.wav"


def test_persistence_bridge_exports_taxonomy_snapshot(tmp_path: Path):
    taxonomy_dir = tmp_path / "taxonomy"
    taxonomy_dir.mkdir()
    (taxonomy_dir / "fx.json").write_text(
        '{"category":"FX","taxonomy":{"Impacts":["boom"]}}',
        encoding="utf-8",
    )
    output_path = tmp_path / "snapshot.json"

    bridge = PersistenceBridge()
    written = bridge.export_taxonomy_snapshot(output_path, taxonomy_dir)

    assert written == output_path
    data = json.loads(output_path.read_text(encoding="utf-8"))
    assert data["FX"]["main_aliases"] == ["boom"]


def test_export_metadata_backup_writes_db_state(tmp_path: Path):
    db = UnshuffleDB(tmp_path / "backup.db")
    try:
        db.seed_aliases_bulk([("thump", "Kicks", 0.8, "user")])
        db.seed_config_list("loop_indicator", ["loop"], clear=True)
        db.seed_suppression_rules({"Claps": ["Snare Rolls"]})
        db.seed_sub_taxonomy({"Kicks": {"thump": "Punchy"}})
        output_path = tmp_path / "metadata_backup.json"

        written = export_metadata_backup(output_path, db)

        assert written == output_path
        data = json.loads(output_path.read_text(encoding="utf-8"))
        assert data["aliases"]["thump"] == ["Kicks", 0.8, "user"]
        assert data["config_lists"]["loop_indicator"] == ["loop"]
        assert data["sub_taxonomy"]["Kicks"]["thump"] == "Punchy"
    finally:
        db.close()


def test_persistence_bridge_exports_metadata_backup(tmp_path: Path):
    db = UnshuffleDB(tmp_path / "bridge_backup.db")
    try:
        db.seed_aliases_bulk([("thump", "Kicks", 0.8, "user")])
        output_path = tmp_path / "bridge_metadata_backup.json"

        workflow = type("_Workflow", (), {"db": db, "session_id": "session-1"})()
        bridge = PersistenceBridge(workflow)
        written = bridge.export_metadata_backup(output_path)

        assert written == output_path
        data = json.loads(output_path.read_text(encoding="utf-8"))
        assert data["aliases"]["thump"] == ["Kicks", 0.8, "user"]
    finally:
        db.close()
