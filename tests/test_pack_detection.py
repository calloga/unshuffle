from pathlib import Path

import pytest

from unshuffle.core.constants import CHILD_DUP_BONUS
from unshuffle.logic.analysis.service import AnalysisContext, build_node_graph


def _analyze(root: Path, monkeypatch):
    monkeypatch.setattr("unshuffle.core.hashing.get_file_hash", lambda _path: "stub")
    context = AnalysisContext(root)
    build_node_graph(root, context)
    return context


def test_duplicate_container_promotes_child_for_shared_tokens(tmp_path, monkeypatch):
    root = tmp_path / "source"
    sample = root / "Edition-3-24bit-48kHz" / "24bit 48kHz" / "Foreign Girls" / "95 loop slide.wav"
    sample.parent.mkdir(parents=True)
    sample.write_text("stub")

    context = _analyze(root, monkeypatch)

    parent = context.nodes[root / "Edition-3-24bit-48kHz"]
    child = context.nodes[root / "Edition-3-24bit-48kHz" / "24bit 48kHz"]

    assert parent.is_duplicate_container
    assert child.is_child_of_duplicate
    assert child.weight_evidence["CHILD_DUP"] == pytest.approx(CHILD_DUP_BONUS * (4 / 6), abs=0.001)


def test_duplicate_container_promotes_child_for_repeated_folder_tokens(tmp_path, monkeypatch):
    root = tmp_path / "source"
    sample = root / "Aden Pack" / "Aden Pack" / "Kicks" / "kick.wav"
    sample.parent.mkdir(parents=True)
    sample.write_text("stub")

    context = _analyze(root, monkeypatch)

    parent = context.nodes[root / "Aden Pack"]
    child = context.nodes[root / "Aden Pack" / "Aden Pack"]

    assert parent.is_duplicate_container
    assert child.is_child_of_duplicate
    assert child.weight_evidence["CHILD_DUP"] == CHILD_DUP_BONUS


def test_duplicate_container_ignores_weak_single_token_overlap(tmp_path, monkeypatch):
    root = tmp_path / "source"
    sample = root / "Ghosthack_-_Free_Impacts_2022" / "Impacts" / "Ghosthack-Impact_Detonate.wav"
    sample.parent.mkdir(parents=True)
    sample.write_text("stub")

    context = _analyze(root, monkeypatch)

    parent = context.nodes[root / "Ghosthack_-_Free_Impacts_2022"]
    child = context.nodes[root / "Ghosthack_-_Free_Impacts_2022" / "Impacts"]

    assert not parent.is_duplicate_container
    assert not child.is_child_of_duplicate
    assert "CHILD_DUP" not in child.weight_evidence
