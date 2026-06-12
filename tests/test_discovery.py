import csv
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest
from typing import Any, Dict
from unshuffle.bridge.discovery_bridge import DiscoveryBridge
from unshuffle.logic.discovery.discovery_engine import (
    get_category_tokens,
    get_all_weighted_tokens,
    scan_discovery_data,
    scan_library,
    generate_combinations
)
from unshuffle.logic.discovery.alias_discovery import (
    get_discovery_output_dir,
    load_alias_table,
    run_discovery,
    run_import,
    save_alias_table,
    show_token_weights,
)

@pytest.fixture
def mock_db():
    db = MagicMock()
    db.get_aliases.return_value = {
        "kick": ["Kicks", 1.0],
        "snare": ["Snares", 1.0],
        "clap": ["Snares", 0.8]
    }
    return db

def test_get_category_tokens():
    alias_table = {
        "kick drum": ["Kicks", 1.0],
        "deep kick": ["Kicks", 1.0],
        "snare": ["Snares", 1.0]
    }
    tokens = get_category_tokens(alias_table, "Kicks")
    assert "kick" in tokens
    assert "drum" in tokens
    assert "deep" in tokens
    assert "snare" not in tokens

def test_get_all_weighted_tokens():
    alias_table = {
        "kick": ["Kicks", 1.0],
        "snare": ["Snares", 1.0]
    }
    tokens = get_all_weighted_tokens(alias_table)
    assert tokens == {"kick", "snare"}

def test_scan_library(tmp_path):
    (tmp_path / "kick_snare_01.wav").touch()
    (tmp_path / "deep_kick.wav").touch()
    (tmp_path / "unrelated.wav").touch()
    
    weighted_tokens = {"snare", "deep"}
    word = "kick"
    
    results = scan_library(word, tmp_path, weighted_tokens)

    res_dict = dict(results)
    assert res_dict["kick snare"] == 1
    assert res_dict["deep kick"] == 1
    assert "unrelated" not in str(results)

def test_scan_discovery_data_matches_live_scan_logic():
    weighted_tokens = {"snare", "deep"}
    entries: list[dict[str, Any]] = [
        {"name": "kick_snare_01.wav", "tokens": ["kick", "snare", "01", "wav"]},
        {"name": "deep_kick.wav", "tokens": ["deep", "kick", "wav"]},
        {"name": "unrelated.wav", "tokens": ["unrelated", "wav"]},
    ]

    results = scan_discovery_data("kick", entries, weighted_tokens)
    res_dict = dict(results)
    assert res_dict["kick snare"] == 1
    assert res_dict["deep kick"] == 1

def test_generate_combinations():
    word = "sub"
    category_tokens = {"kick", "bass"}
    
    combos = generate_combinations(word, category_tokens)
    assert "bass sub" in combos
    assert "kick sub" in combos
    assert "bass kick sub" in combos
    assert len(combos) == 3

def test_load_alias_table(mock_db):
    table = load_alias_table(mock_db)
    assert "kick" in table
    mock_db.get_aliases.assert_called_once()

def test_save_alias_table(mock_db):
    alias_table = {"new": ["Test", 1.0]}
    written = save_alias_table(mock_db, alias_table)
    assert written == 1
    mock_db.seed_aliases_bulk.assert_called_once()
    args = mock_db.seed_aliases_bulk.call_args[0][0]
    assert args[0] == ("new", "Test", 1.0, "discovery")

def test_run_import(tmp_path, mock_db):
    csv_file = tmp_path / "test_import.csv"
    with open(csv_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["Alias", "Frequency", "Valid"])
        writer.writeheader()
        writer.writerow({"Alias": "punchy kick", "Frequency": "10", "Valid": "x"})
        writer.writerow({"Alias": "bad kick", "Frequency": "1", "Valid": ""})
    
    with patch("unshuffle.persistence.get_db", return_value=mock_db):
        summary = run_import(str(csv_file), "Kicks", tmp_path)

    mock_db.seed_aliases_bulk.assert_called_once()
    args = mock_db.seed_aliases_bulk.call_args[0][0]
    aliases = [a[0] for a in args]
    assert "punchy kick" in aliases
    assert "bad kick" not in aliases
    assert summary["added_count"] == 1
    assert summary["added_aliases"] == ["punchy kick"]


def test_show_token_weights_returns_structured_hits():
    results = show_token_weights(["kick", "mysterytoken"])

    assert results[0]["word"] == "kick"
    assert isinstance(results[0]["hits"], list)
    assert results[1]["found"] is False


def test_run_discovery_requires_explicit_inputs(tmp_path):
    with pytest.raises(ValueError):
        run_discovery(tmp_path, discover_words=None, category="Kicks")

    with pytest.raises(ValueError):
        run_discovery(tmp_path, discover_words="kick", category=None)


def test_run_discovery_returns_rows_and_writes_csv(tmp_path, mock_db):
    with patch("unshuffle.persistence.get_db", return_value=mock_db):
        results = run_discovery(
            tmp_path,
            discover_words="punch",
            category="Kicks",
            auto_detect=False,
            output_dir=tmp_path,
        )

    assert results[0]["status"] == "created"
    assert results[0]["rows"][0]["Alias"] == "punch"
    assert Path(results[0]["csv_path"]).exists()


def test_run_discovery_prefers_cached_discovery_data_when_available(tmp_path, mock_db):
    discovery_data = {
        "entries": [
            {"name": "punch_kick.wav", "tokens": ["punch", "kick", "wav"]},
        ]
    }
    with patch("unshuffle.persistence.get_db", return_value=mock_db), \
         patch("unshuffle.logic.discovery.alias_discovery.load_discovery_data", return_value=discovery_data):
        results = run_discovery(
            tmp_path,
            discover_words="punch",
            category="Kicks",
            auto_detect=True,
            source_dir=str(tmp_path),
            output_dir=tmp_path,
        )

    assert results[0]["source"] == "discovery_data"
    assert results[0]["rows"][1]["Alias"] == "kick punch"


def test_get_discovery_output_dir_uses_global_system_dir_when_not_provided(tmp_path):
    base = tmp_path / "global"
    with patch("unshuffle.logic.discovery.alias_discovery.get_global_system_dir", return_value=base):
        path = get_discovery_output_dir()

    assert path == base / "discovery"
    assert path.exists()


def test_discovery_bridge_lookup_alias_returns_structured_result(tmp_path, mock_db):
    with patch("unshuffle.persistence.get_db", return_value=mock_db):
        result = DiscoveryBridge.lookup_alias(tmp_path, "punch", "Kicks")

    assert result["status"] == "created"
    assert result["word"] == "punch"
    assert result["csv_path"] is None


def test_discovery_bridge_exposes_cached_scan_and_review_helpers(tmp_path):
    entries = [{"name": "punch_kick.wav", "tokens": ["punch", "kick", "wav"]}]
    weighted_tokens = {"kick"}
    scan_results = DiscoveryBridge.scan_discovery_data("punch", entries, weighted_tokens)
    assert dict(scan_results)["kick punch"] == 1

    taxonomy_dir = tmp_path / "taxonomy"
    taxonomy_dir.mkdir()
    (taxonomy_dir / "kicks.json").write_text(
        '{"category":"Kicks","taxonomy":{"main":["boom"]}}',
        encoding="utf-8",
    )
    (taxonomy_dir / "fx.json").write_text(
        '{"category":"FX","taxonomy":{"main":["boom"]}}',
        encoding="utf-8",
    )
    conflicts = DiscoveryBridge.find_possible_conflicts(taxonomy_dir)
    assert conflicts[0]["alias"] == "boom"

    csv_path = tmp_path / "uncategorized.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as file_handle:
        writer = csv.DictWriter(
            file_handle,
            fieldnames=["sample_name", "source_directory", "category", "confidence_level"],
        )
        writer.writeheader()
        writer.writerow(
            {
                "sample_name": "mystery.wav",
                "source_directory": "Source/Unknown",
                "category": "Uncategorized",
                "confidence_level": "0.12",
            }
        )
    summary = DiscoveryBridge.analyze_uncategorized(csv_path)
    assert summary["total_uncategorized"] == 1
