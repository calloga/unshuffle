import collections
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple


class ScoringEngine:
    """
    Statistical scoring engine with alias-driven weights and specificity tie-breaks.
    """

    def __init__(self, alias_table: Dict[str, List], noise_words: Optional[Set[str]] = None):
        self.category_map = collections.defaultdict(lambda: collections.Counter())
        self.total_tokens_per_category = collections.Counter()
        self.global_token_count = 0
        self.specificity = {}
        self.weights = collections.defaultdict(lambda: collections.defaultdict(float))
        self.noise_words = noise_words or set()

        token_to_categories = collections.defaultdict(set)

        from ...core.tokenizer import tokenize

        for alias_str, category_data in alias_table.items():
            category = str(category_data[0] if isinstance(category_data, (list, tuple)) else category_data)
            tokens = tokenize(alias_str, flatten=False)

            for token in tokens:
                if token in self.noise_words:
                    continue
                self.category_map[category][token] += 1
                self.total_tokens_per_category[category] += 1
                self.global_token_count += 1
                token_to_categories[token].add(category)

        for token, categories in token_to_categories.items():
            self.specificity[token] = 1.0 / len(categories)

        token_global_counts = collections.Counter()
        for category_counts in self.category_map.values():
            for token, count in category_counts.items():
                token_global_counts[token] += count

        for category, counts in self.category_map.items():
            for token, count in counts.items():
                self.weights[category][token] = count / token_global_counts[token]

        self.reverse_weights = collections.defaultdict(dict)
        for category, token_weights in self.weights.items():
            for token, weight in token_weights.items():
                self.reverse_weights[token][category] = weight

    def _score_tokens_impl(
        self,
        tokens: Iterable[str],
        debug: bool = False,
        include_trace: bool = False,
    ) -> Tuple[Dict[str, float], List[Dict[str, Any]]]:
        scores = collections.defaultdict(float)
        token_trace: List[Dict[str, Any]] = []

        for token in sorted(set(tokens)):
            if token in self.noise_words:
                if include_trace:
                    token_trace.append({"token": token, "status": "noise", "matches": []})
                continue
            if token in self.reverse_weights:
                matches = []
                for category, weight in self.reverse_weights[token].items():
                    current = scores[category]
                    if current >= 1.0:
                        continue

                    updated = 1.0 - ((1.0 - current) * (1.0 - weight))
                    scores[category] = updated
                    if include_trace:
                        matches.append(
                            {
                                "category": category,
                                "weight": weight,
                                "specificity": self.specificity.get(token, 0.0),
                                "before": current,
                                "after": updated,
                                "contribution": updated - current,
                            }
                        )

                    if debug:
                        print(f"    [DEBUG] Token '{token}' -> {category}: {weight:.4f} (Accumulated: {scores[category]:.4f})")
                if include_trace:
                    token_trace.append({"token": token, "status": "matched", "matches": matches})
            elif include_trace:
                token_trace.append(
                    {
                        "token": token,
                        "status": "not_found",
                        "matches": [],
                        "specificity": self.specificity.get(token, 0.0),
                    }
                )
        return dict(scores), token_trace

    def score_tokens(self, tokens: Iterable[str], debug: bool = False) -> Dict[str, float]:
        scores, _trace = self._score_tokens_impl(tokens, debug=debug, include_trace=False)
        return scores

    def score_tokens_with_trace(self, tokens: Iterable[str], debug: bool = False) -> Tuple[Dict[str, float], List[Dict[str, Any]]]:
        return self._score_tokens_impl(tokens, debug=debug, include_trace=True)

    def get_specificity_score(self, tokens: Iterable[str], category: str) -> float:
        total_spec = 0.0
        if category not in self.weights:
            return 0.0

        for token in tokens:
            if token in self.weights[category]:
                total_spec += self.specificity.get(token, 0.0)
        return total_spec

    def resolve_tie(self, tokens: List[str], categories: List[str]) -> str:
        if not categories:
            return "Uncategorized"

        spec_scores = {category: self.get_specificity_score(tokens, category) for category in categories}
        return max(spec_scores.items(), key=lambda item: item[1])[0]
