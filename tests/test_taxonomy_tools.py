import csv
import json
from pathlib import Path
from unittest import mock

from unshuffle.logic.discovery import (
    analyze_uncategorized_csv,
    analyze_uncategorized_rows,
    build_taxonomy_sync_payload,
    find_cross_taxonomy_conflicts,
    sync_taxonomy_to_db,
)


def test_build_taxonomy_sync_payload_summarizes_config():
    payload = build_taxonomy_sync_payload(
        {
            "ALIAS_TABLE": {"kick": ["Kicks", 1.0], "snare": "Snares"},
            "NOISE_WORDS": ["dry", "wet"],
            "LOOP_INDICATORS": ["loop"],
            "ONESHOT_INDICATORS": ["oneshot"],
            "WEAK_LOOP_INDICATORS": ["phrase"],
            "CATEGORY_SUPPRESSION_RULES": {"Kicks": ["Percussion"]},
            "SUB_TAXONOMY_MAP": {"Kicks": {"kick": "no-sub"}},
        }
    )

    assert payload["alias_count"] == 2
    assert payload["noise_words"] == ["dry", "wet"]
    assert payload["sub_taxonomy_map"] == {"Kicks": {"kick": "no-sub"}}


def test_sync_taxonomy_to_db_delegates_to_persistence_and_refresh():
    db = mock.Mock()
    db.write_transaction.return_value = mock.MagicMock()
    config = {
        "ALIAS_TABLE": {"kick": ["Kicks", 1.0]},
        "NOISE_WORDS": ["dry"],
        "LOOP_INDICATORS": [],
        "ONESHOT_INDICATORS": [],
        "WEAK_LOOP_INDICATORS": [],
        "CATEGORY_SUPPRESSION_RULES": {},
        "SUB_TAXONOMY_MAP": {"Kicks": {"kick": "no-sub"}},
    }

    with mock.patch("unshuffle.logic.discovery.taxonomy_sync.sync_alias_library") as sync_aliases, \
         mock.patch("unshuffle.logic.discovery.taxonomy_sync.sync_full_config") as sync_config, \
         mock.patch("unshuffle.logic.discovery.taxonomy_sync.refresh_alias_structures") as refresh, \
         mock.patch("unshuffle.logic.discovery.taxonomy_sync.reset_scoring_engine") as reset_scoring:
        summary = sync_taxonomy_to_db(db, config)

    db.write_transaction.assert_called_once_with()
    sync_aliases.assert_called_once_with(db, config["ALIAS_TABLE"], in_transaction=True)
    sync_config.assert_called_once_with(db, config, in_transaction=True)
    refresh.assert_called_once_with(db)
    reset_scoring.assert_called_once_with()
    assert summary["alias_count"] == 1
    assert summary["sub_taxonomy_category_count"] == 1


def test_find_cross_taxonomy_conflicts_reports_alias_reused_across_categories(tmp_path: Path):
    (tmp_path / "kicks.json").write_text(
        '{"category":"Kicks","taxonomy":{"no-sub":["boom","kick"]}}',
        encoding="utf-8",
    )
    (tmp_path / "fx.json").write_text(
        '{"category":"FX","taxonomy":{"Impacts":["boom","rise"]}}',
        encoding="utf-8",
    )

    conflicts = find_cross_taxonomy_conflicts(tmp_path)

    assert len(conflicts) == 1
    assert conflicts[0]["alias"] == "boom"
    assert conflicts[0]["categories"] == ["FX", "Kicks"]


def test_analyze_uncategorized_rows_summarizes_folder_counts_and_examples():
    summary = analyze_uncategorized_rows(
        [
            {"category": "Uncategorized", "source_directory": "A", "sample_name": "one.wav", "confidence_level": "0.4"},
            {"category": "Kicks", "source_directory": "B", "sample_name": "two.wav", "confidence_level": "1.0"},
            {"category": "Uncategorized", "source_directory": "A", "sample_name": "three.wav", "confidence_level": "0.2"},
        ]
    )

    assert summary["total_uncategorized"] == 2
    assert summary["top_folders"][0] == ("A", 2)
    assert summary["examples"][0]["sample_name"] == "one.wav"


def test_analyze_uncategorized_csv_reads_report_shape(tmp_path: Path):
    csv_path = tmp_path / "report.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as file_handle:
        writer = csv.DictWriter(
            file_handle,
            fieldnames=["category", "source_directory", "source_filename", "confidence_level"],
        )
        writer.writeheader()
        writer.writerow(
            {
                "category": "Uncategorized",
                "source_directory": "Folder A",
                "source_filename": "mystery.wav",
                "confidence_level": "0.11",
            }
        )

    summary = analyze_uncategorized_csv(csv_path)

    assert summary["total_uncategorized"] == 1
    assert summary["examples"][0]["sample_name"] == "mystery.wav"


def test_process_file_writes_sorted_taxonomy_atomically(tmp_path: Path):
    from unshuffle.core.sorting import process_file

    tax_path = tmp_path / "kicks.json"
    tax_path.write_text(
        json.dumps({"category": "Kicks", "taxonomy": {"main": ["kick", "big kick"]}}),
        encoding="utf-8",
    )

    changed = process_file(tax_path)

    assert changed is True
    assert not (tmp_path / "kicks.json.tmp").exists()
    data = json.loads(tax_path.read_text(encoding="utf-8"))
    assert data["taxonomy"]["main"] == ["big kick", "kick"]


def test_sort_all_logs_exceptions_instead_of_swallowing(tmp_path: Path):
    from unshuffle.core import sorting

    bad_path = tmp_path / "broken.json"
    bad_path.write_text("{}", encoding="utf-8")

    with mock.patch("unshuffle.core.sorting.process_file", side_effect=RuntimeError("boom")):
        with mock.patch("unshuffle.core.sorting.logging.exception") as log_mock:
            sorting.sort_all(tmp_path)

    log_mock.assert_called_once()
