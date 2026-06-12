from unshuffle.core.tokenizer import tokenize


def test_tokenize_handles_latin_diacritics_and_camel_case() -> None:
    tokens = tokenize("CaféKickÄudioLoop120")

    assert "café" in tokens
    assert "kick" in tokens
    assert "äudio" in tokens
    assert "loop" in tokens
    assert "120" in tokens


def test_tokenize_preserves_sequence_when_flatten_is_false() -> None:
    tokens = tokenize("SeñorSnareOneShot", flatten=False)

    assert tokens == ["señor", "snare", "one", "shot"]
