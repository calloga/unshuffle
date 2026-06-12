import collections
from pathlib import Path
from typing import Iterable

from ...core.constants import ANCHOR_MAP, GLOBAL_BOOST_CAP
from ...logic.classification import tokenize


class GlobalFrequencyAnalyzer:
    """
    Scans a library to build a category-prevalence map from fed paths.
    """

    def __init__(self):
        self.category_counts = collections.Counter()
        self.total_signals = 0
        self.boosts = {}
        self.anchors = set(ANCHOR_MAP.values())
        self.anchor_to_categories = collections.defaultdict(list)
        for category, anchor in ANCHOR_MAP.items():
            self.anchor_to_categories[anchor].append(category)

    def feed_path(self, path: Path, tokens: Iterable[str] | None = None):
        name_tokens = list(tokens) if tokens is not None else tokenize(path.name)
        for token in name_tokens:
            if token in self.anchors:
                for category in self.anchor_to_categories.get(token, ()):
                    self.category_counts[category] += 1
                    self.total_signals += 1

    def finalize(self):
        if self.total_signals > 0:
            for category in ANCHOR_MAP:
                prevalence = self.category_counts[category] / self.total_signals
                self.boosts[category] = round(min(GLOBAL_BOOST_CAP, (prevalence**0.5) * GLOBAL_BOOST_CAP), 4)
        else:
            self.boosts = {category: 0.0 for category in ANCHOR_MAP}

    def get_boost(self, category: str) -> float:
        return self.boosts.get(category, 0.0)
