from pathlib import Path

from unshuffle.logic.analysis.frequency import GlobalFrequencyAnalyzer


def test_global_frequency_analyzer_accepts_pre_tokenized_input() -> None:
    analyzer = GlobalFrequencyAnalyzer()

    analyzer.feed_path(Path("ignored.wav"), tokens=["kick", "cymbal"])
    analyzer.finalize()

    assert analyzer.category_counts["Kicks"] == 1
    assert analyzer.category_counts["Hats & Cymbals"] == 1


def test_global_frequency_analyzer_supports_duplicate_anchor_values() -> None:
    analyzer = GlobalFrequencyAnalyzer()
    analyzer.anchor_to_categories["kick"] = ["Kicks", "Percussion"]

    analyzer.feed_path(Path("kick.wav"), tokens=["kick"])

    assert analyzer.category_counts["Kicks"] == 1
    assert analyzer.category_counts["Percussion"] == 1
