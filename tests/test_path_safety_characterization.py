from pathlib import Path

from unshuffle.core.path_safety import ensure_unique_path


def test_ensure_unique_path_appends_numeric_suffix(tmp_path: Path) -> None:
    original = tmp_path / "kick.wav"
    original.write_text("a", encoding="utf-8")
    second = tmp_path / "kick_1.wav"
    second.write_text("b", encoding="utf-8")

    candidate = ensure_unique_path(original)

    assert candidate == tmp_path / "kick_2.wav"
