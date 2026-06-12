from pathlib import Path
from typing import Any, cast
from unittest import mock

from unshuffle.core import LibNode, NodeType
from unshuffle.core.constants import TOKEN_ADJUSTMENT_CAP, get_runtime_config_snapshot
from unshuffle.logic.classification import classify_node, detect_audio_type, reset_scoring_engine


def test_runtime_config_snapshot_includes_metadata_centralization_fields():
    runtime = get_runtime_config_snapshot()

    assert runtime["oneshot_hint_tokens"] == ["kick", "snare", "hat", "clap", "perc", "tom", "rim", "oneshot"]
    assert runtime["percussive_categories"] == ["Kicks", "Snares", "Claps", "Hats & Cymbals", "Percussion"]


def test_detect_audio_type_uses_runtime_oneshot_hint_tokens():
    node = LibNode(
        path=Path(r"C:\audio\Long\kick_tail.wav"),
        name="kick_tail.wav",
        node_type=NodeType.FILE,
        extension=".wav",
    )

    runtime = get_runtime_config_snapshot()
    runtime["loop_indicators"] = []
    runtime["weak_loop_indicators"] = []
    runtime["oneshot_indicators"] = []
    runtime["model_numbers"] = set()
    runtime["oneshot_hint_tokens"] = ["kick"]

    with mock.patch("unshuffle.logic.classification.audio_type.get_runtime_config_snapshot", return_value=runtime):
        assert detect_audio_type(node, duration=3.0, features={"loopiness_score": 0.9}) == "Oneshots"

    runtime["oneshot_hint_tokens"] = []
    with mock.patch("unshuffle.logic.classification.audio_type.get_runtime_config_snapshot", return_value=runtime):
        assert detect_audio_type(node, duration=3.0, features={"loopiness_score": 0.9}) == "Loops"


def test_detect_audio_type_ignores_non_numeric_feature_scores():
    node = LibNode(
        path=Path(r"C:\audio\Long\texture.wav"),
        name="texture.wav",
        node_type=NodeType.FILE,
        extension=".wav",
    )

    assert detect_audio_type(
        node,
        duration=3.0,
        features=cast(Any, {"transient_tail_score": "nope", "loopiness_score": object()}),
    ) == "Oneshots"


def test_classify_node_uses_runtime_percussive_categories_for_duration_penalty():
    node = LibNode(
        path=Path(r"C:\audio\folder\kick.wav"),
        name="kick.wav",
        node_type=NodeType.FILE,
        extension=".wav",
    )

    runtime = get_runtime_config_snapshot()
    runtime["percussive_categories"] = ["Kicks"]

    with mock.patch("unshuffle.logic.classification.service.get_runtime_config_snapshot", return_value=runtime):
        category, confidence, _meta = classify_node(node, duration=2.0)

    assert category == "Kicks"
    assert confidence > 0.0


def test_classify_node_rebuilds_scoring_engine_when_runtime_aliases_change():
    node = LibNode(
        path=Path(r"C:\audio\folder\foo.wav"),
        name="foo.wav",
        node_type=NodeType.FILE,
        extension=".wav",
    )
    first = get_runtime_config_snapshot()
    first["alias_table"] = {"foo": ["Kicks", 1.0]}
    first["category_suppression_rules"] = {}
    second = get_runtime_config_snapshot()
    second["alias_table"] = {"foo": ["Snares", 1.0]}
    second["category_suppression_rules"] = {}

    reset_scoring_engine()
    try:
        first_category, _first_confidence, _first_meta = classify_node(node, runtime=first)
        second_category, _second_confidence, _second_meta = classify_node(node, runtime=second)
    finally:
        reset_scoring_engine()

    assert first_category == "Kicks"
    assert second_category == "Snares"


def test_classify_node_clamps_negative_noise_floor_confidence():
    node = LibNode(
        path=Path(r"C:\audio\folder\plain.wav"),
        name="plain.wav",
        node_type=NodeType.FILE,
        extension=".wav",
    )
    runtime = get_runtime_config_snapshot()
    runtime["alias_table"] = {}
    runtime["category_suppression_rules"] = {}

    category, confidence, meta = classify_node(
        node,
        runtime=runtime,
        token_adjustments={"plain": {"Kicks": -1.0}},
    )

    assert category == "Uncategorized"
    assert confidence == 0.0
    assert meta["trace"]["confidence"] == 0.0


def test_runtime_config_snapshot_deep_copies_alias_payloads():
    from unshuffle.core import constants

    with constants.CONFIG_STATE_LOCK:
        constants._STATE.alias_table["mutable-probe"] = ["Kicks", 1.0]
    try:
        snapshot = get_runtime_config_snapshot()
        snapshot["alias_table"]["mutable-probe"][0] = "Snares"
        with constants.CONFIG_STATE_LOCK:
            assert constants._STATE.alias_table["mutable-probe"][0] == "Kicks"
    finally:
        with constants.CONFIG_STATE_LOCK:
            constants._STATE.alias_table.pop("mutable-probe", None)


def test_load_config_skips_non_string_taxonomy_category(tmp_path, monkeypatch):
    from unshuffle.core import config as config_module

    data_dir = tmp_path / "data"
    taxonomy_dir = data_dir / "taxonomy"
    taxonomy_dir.mkdir(parents=True)
    (data_dir / "config.json").write_text("{}", encoding="utf-8")
    (taxonomy_dir / "bad.json").write_text(
        '{"category": ["bad"], "default_sub": ["bad"], "taxonomy": {"Sub": ["alias"]}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(config_module, "ROOT_DIR", tmp_path)

    loaded = config_module.load_config()

    assert "alias" not in loaded["ALIAS_TABLE"]
    assert all(isinstance(key, str) for key in loaded["SUB_TAXONOMY_MAP"])


def test_classify_node_keeps_full_drums_loop_exclusive():
    node = LibNode(
        path=Path(r"C:\audio\kit\beat.wav"),
        name="beat.wav",
        node_type=NodeType.FILE,
        extension=".wav",
    )

    oneshot_category, _oneshot_confidence, oneshot_meta = classify_node(node, duration=0.2)
    loop_category, loop_confidence, _loop_meta = classify_node(node, duration=5.0)

    assert oneshot_category != "Full Drums"
    assert oneshot_meta["trace"]["audio_type_exclusions"] == [
        {"category": "Full Drums", "audio_type": "Oneshots", "reason": "loop_exclusive_category"}
    ]
    assert loop_category == "Full Drums"
    assert loop_confidence > 0.0


def test_classify_node_does_not_short_circuit_tied_filename_scores():
    node = LibNode(
        path=Path(r"C:\audio\Snares\HitBoy Snare.wav"),
        name="HitBoy Snare.wav",
        node_type=NodeType.FILE,
        extension=".wav",
    )
    runtime = get_runtime_config_snapshot()
    runtime["alias_table"] = {
        "hit": ["FX", 1.0],
        "snare": ["Snares", 1.0],
        "snares": ["Snares", 1.0],
    }
    runtime["category_suppression_rules"] = {}

    reset_scoring_engine()
    try:
        category, _confidence, meta = classify_node(node, runtime=runtime)
    finally:
        reset_scoring_engine()

    assert meta["stage"] != "f_shortcircuit"
    assert category == "Snares"


def test_key_fallback_bass_hint_uses_tokens_not_substrings():
    runtime = get_runtime_config_snapshot()
    runtime["alias_table"] = {}
    runtime["category_suppression_rules"] = {}
    cases = {
        "sub_Cm.wav": ("Bass", "key_fallback_bass"),
        "subtle_Cm.wav": ("Melodics", "key_fallback"),
        "submarine_Cm.wav": ("Melodics", "key_fallback"),
        "ambassador_Cm.wav": ("Melodics", "key_fallback"),
    }

    reset_scoring_engine()
    try:
        for name, expected in cases.items():
            node = LibNode(
                path=Path(r"C:\audio\Mystery") / name,
                name=name,
                node_type=NodeType.FILE,
                extension=".wav",
            )
            category, _confidence, meta = classify_node(node, runtime=runtime)
            assert (category, meta["stage"]) == expected
    finally:
        reset_scoring_engine()


def test_unweighted_learned_correction_is_ignored():
    runtime = get_runtime_config_snapshot()
    runtime["alias_table"] = {}
    runtime["category_suppression_rules"] = {}
    node = LibNode(
        path=Path(r"C:\audio\Pharaoh Premium Drum Samples (BETA)\Cymatics - Agony - 115 BPM C Min.wav"),
        name="Cymatics - Agony - 115 BPM C Min.wav",
        node_type=NodeType.FILE,
        extension=".wav",
    )

    reset_scoring_engine()
    try:
        category, _confidence, meta = classify_node(
            node,
            runtime=runtime,
            token_adjustments={"cymatics": {"Percussion": TOKEN_ADJUSTMENT_CAP}},
        )
    finally:
        reset_scoring_engine()

    assert category == "Melodics"
    assert meta["stage"] == "key_fallback"
    assert dict(meta["raw"]) == {}
    assert meta["trace"]["token_adjustments"] == []


def test_runtime_execution_learning_is_disabled_for_non_user_decisions():
    from unshuffle.runtime.execution_learning import classification_adjustments_for

    record = type("Record", (), {})()
    record.category = "Snares"
    record.evidence = {"heuristic_category": "Percussion"}
    record.source_path = Path("D:/Samples/Pack/shaker.wav")

    assert classification_adjustments_for(record) == set()
