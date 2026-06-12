from pathlib import Path
from unittest import mock
import json
import math
import struct
import wave

from unshuffle.audio import SimilarityEngine
from unshuffle.audio.metadata import get_audio_duration
from unshuffle.core import LibNode, NodeType
from unshuffle.core.features import CURRENT_FEATURE_SCHEMA, FEATURE_VECTOR_SIZE, vector_to_feature_values
from unshuffle.logic.classification import detect_audio_type


def test_similarity_engine_eviction_keeps_cache_bounded():
    engine = SimilarityEngine(extractor_path="missing-extractor", max_cache_entries=2)
    engine._cache_set(Path("a.wav"), [0.1] * FEATURE_VECTOR_SIZE)
    engine._cache_set(Path("b.wav"), [0.2] * FEATURE_VECTOR_SIZE)
    engine._cache_set(Path("c.wav"), [0.3] * FEATURE_VECTOR_SIZE)

    assert list(engine.feature_cache.keys()) == ["b.wav", "c.wav"]


def test_similarity_engine_caches_negative_extractor_results_until_file_changes(tmp_path: Path):
    extractor = tmp_path / "extractor.exe"
    extractor.write_text("fake", encoding="utf-8")
    sample = tmp_path / "bad.wav"
    sample.write_bytes(b"not audio")
    engine = SimilarityEngine(extractor_path=str(extractor), max_cache_entries=4)

    failed = mock.Mock(returncode=1, stderr="bad file", stdout="")
    with mock.patch("unshuffle.audio.acoustic.subprocess.run", return_value=failed) as run:
        assert engine.extract_features(sample) is None
        assert engine.extract_features(sample) is None
        assert run.call_count == 1

        sample.write_bytes(b"changed audio")
        assert engine.extract_features(sample) is None
        assert run.call_count == 2


def test_extraction_failure_messages_map_to_user_tags():
    assert SimilarityEngine.extraction_failure_tag_for_message("File is silent (RMS: 0)") == "Silent"
    assert SimilarityEngine.extraction_failure_tag_for_message("File is empty") == "Empty"
    assert SimilarityEngine.extraction_failure_tag_for_message("dr_wav: Failed to open file") == "Corrupted"


def test_windows_feature_extraction_hides_subprocess_window(tmp_path: Path, monkeypatch):
    extractor = tmp_path / "extractor.exe"
    extractor.write_text("fake", encoding="utf-8")
    sample = tmp_path / "sample.wav"
    sample.write_bytes(b"fake audio")
    engine = SimilarityEngine(extractor_path=str(extractor))

    completed = mock.Mock(
        returncode=0,
        stdout=json.dumps({"vector": [0.1] * FEATURE_VECTOR_SIZE, "feature_schema": list(CURRENT_FEATURE_SCHEMA)}),
        stderr="",
    )
    monkeypatch.setattr("unshuffle.audio.acoustic.os.name", "nt")

    with mock.patch("unshuffle.audio.acoustic.subprocess.run", return_value=completed) as run:
        assert engine.extract_feature_payload(sample) is not None

    kwargs = run.call_args.kwargs
    assert kwargs["creationflags"] & 0x08000000
    assert kwargs["startupinfo"].dwFlags & 1
    assert kwargs["startupinfo"].wShowWindow == 0


def test_audio_duration_logs_debug_for_mutagen_failures(tmp_path: Path):
    bad_file = tmp_path / "bad.mp3"
    bad_file.write_bytes(b"not audio")

    with mock.patch("unshuffle.audio.metadata.HAS_MUTAGEN", True), \
         mock.patch("unshuffle.audio.metadata.mutagen") as fake_mutagen, \
         mock.patch("unshuffle.audio.metadata.logging.debug") as debug_log:
        fake_mutagen.File.side_effect = RuntimeError("boom")
        assert get_audio_duration(bad_file) is None

    debug_log.assert_called()


def test_vector_from_blob_decodes_little_endian_payload():
    import struct

    blob = struct.pack("<" + ("f" * FEATURE_VECTOR_SIZE), *([0.25] * FEATURE_VECTOR_SIZE))
    vec = SimilarityEngine.vector_from_blob(blob)

    assert vec is not None
    assert len(vec) == len(CURRENT_FEATURE_SCHEMA)


def test_feature_loopiness_detects_long_decaying_tail(tmp_path: Path):
    sample = tmp_path / "GDYN_Punching_Perc_DESIGNED - 3.wav"
    _write_wav(sample, _decaying_hit(3.2))

    node = LibNode(path=sample, name=sample.stem, node_type=NodeType.FILE, extension=".wav")
    features = _feature_values(active_duration=3.2, transient_tail_score=0.9, loopiness_score=0.1)

    assert detect_audio_type(node, duration=get_audio_duration(sample), features=features) == "Oneshots"


def test_feature_loopiness_detects_repeating_long_audio(tmp_path: Path):
    sample = tmp_path / "mystery.wav"
    _write_wav(sample, _pulse_loop(3.2))

    node = LibNode(path=sample, name=sample.stem, node_type=NodeType.FILE, extension=".wav")
    features = _feature_values(active_duration=3.2, transient_tail_score=0.1, loopiness_score=0.9)

    assert detect_audio_type(node, duration=get_audio_duration(sample), features=features) == "Loops"


def test_duration_fallback_waits_until_five_seconds_without_loopiness(tmp_path: Path):
    sample = tmp_path / "texture.mp3"
    sample.write_bytes(b"not real audio")
    node = LibNode(path=sample, name=sample.stem, node_type=NodeType.FILE, extension=".mp3")

    assert detect_audio_type(node, duration=3.2) == "Oneshots"
    assert detect_audio_type(node, duration=5.0) == "Loops"


def _write_wav(path: Path, samples: list[float], rate: int = 8000) -> None:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        payload = b"".join(
            struct.pack("<h", max(-32767, min(32767, int(sample * 32767)))) for sample in samples
        )
        handle.writeframes(payload)


def _decaying_hit(duration: float, rate: int = 8000) -> list[float]:
    count = int(duration * rate)
    result = []
    for index in range(count):
        t = index / rate
        envelope = math.exp(-3.5 * t)
        result.append(math.sin(2.0 * math.pi * 180.0 * t) * envelope)
    return result


def _pulse_loop(duration: float, rate: int = 8000) -> list[float]:
    count = int(duration * rate)
    result = []
    for index in range(count):
        t = index / rate
        beat_phase = (t * 4.0) % 1.0
        envelope = 0.2 + (0.8 if beat_phase < 0.18 else 0.0)
        result.append(math.sin(2.0 * math.pi * 110.0 * t) * envelope)
    return result


def _feature_values(**overrides: float) -> dict[str, float]:
    vector = [0.1] * FEATURE_VECTOR_SIZE
    values = vector_to_feature_values(vector)
    values.update(overrides)
    return values
