import collections
from typing import Any, Dict, Optional, Set, Tuple, cast

from ...core.constants import (
    ALIAS_TABLE,
    BPM_REGEX_PATTERN,
    CATEGORY_SUPPRESSION_RULES,
    CONTEXT_WEIGHT,
    FILENAME_WEIGHT,
    KEY_FALLBACK_CONFIDENCE,
    KEY_REGEX_PATTERN,
    MARGIN_THRESHOLD,
    NO_SIGNAL_THRESHOLD,
    PACK_WEIGHT,
    SHORT_CIRCUIT_THRESHOLD,
    SUPPRESS_TRIGGER,
    get_runtime_config_snapshot,
)
from ...core.models import LibNode
from ...core.tokenizer import tokenize
from .audio_type import detect_audio_type as _detect_audio_type


def is_category_alias(name_lower: str) -> bool:
    return name_lower in ALIAS_TABLE


def apply_suppression(
    scores: Dict[str, float],
    suppression_rules: Optional[Dict[str, list[str]]] = None,
) -> Dict[str, float]:
    new_scores = dict(scores)
    rules = suppression_rules if suppression_rules is not None else CATEGORY_SUPPRESSION_RULES
    for suppressor, targets in rules.items():
        if scores.get(suppressor, 0.0) >= SUPPRESS_TRIGGER:
            for target in targets:
                if target in new_scores:
                    new_scores[target] = 0.0
    return new_scores


def compute_component_score(
    filename: str,
    pack_name: Optional[str] = None,
    token_adjustments: Optional[Dict[str, Dict[str, float]]] = None,
    runtime: Optional[dict] = None,
) -> Dict[str, float]:
    tokens = tokenize(filename)
    if pack_name:
        tokens.update(tokenize(pack_name))
    runtime = runtime or get_runtime_config_snapshot()
    alias_table = cast(Any, runtime["alias_table"])
    weighted_tokens = weighted_adjustment_tokens(tokens, runtime) if token_adjustments else set()

    scores = {}

    for token in tokens:
        if token in alias_table:
            result = alias_table[token]
            canonical = str(result[0] if isinstance(result, (list, tuple)) else result)
            weight = result[1] if isinstance(result, (list, tuple)) else 1.0
            scores[canonical] = scores.get(canonical, 0.0) + weight

        if token_adjustments and token in weighted_tokens and token in token_adjustments:
            for adj_cat, offset in token_adjustments[token].items():
                scores[adj_cat] = scores.get(adj_cat, 0.0) + offset

    return apply_suppression(scores)


def get_subcategory(category: str, tokens: Set[str], runtime: Optional[dict] = None) -> Optional[str]:
    runtime = runtime or get_runtime_config_snapshot()
    cat_map = runtime["sub_taxonomy_map"].get(category, {})
    for token in tokens:
        if token in cat_map:
            sub = cat_map[token]
            return None if sub == "no-sub" else sub
    
    res = runtime.get("default_sub_map", {}).get(category)
    return None if res == "no-sub" else res


SCORING_ENGINE = None
SCORING_ENGINE_SIGNATURE = None
LOOP_EXCLUSIVE_CATEGORIES = {"Full Drums"}
KEY_FALLBACK_BASS_TOKENS = frozenset({"808", "bass", "sub"})


def reset_scoring_engine():
    global SCORING_ENGINE, SCORING_ENGINE_SIGNATURE
    SCORING_ENGINE = None
    SCORING_ENGINE_SIGNATURE = None


def _scoring_runtime_signature(runtime: dict) -> tuple:
    alias_table = cast(Any, runtime["alias_table"])
    noise_words = cast(Any, runtime["noise_words"])
    aliases = tuple(
        sorted(
            (
                str(alias),
                tuple(value) if isinstance(value, list) else value,
            )
            for alias, value in alias_table.items()
        )
    )
    return aliases, tuple(sorted(str(word) for word in noise_words))


def get_scoring_engine(runtime: Optional[dict] = None):
    global SCORING_ENGINE, SCORING_ENGINE_SIGNATURE

    runtime = runtime or get_runtime_config_snapshot()
    signature = _scoring_runtime_signature(runtime)
    if SCORING_ENGINE is None or SCORING_ENGINE_SIGNATURE != signature:
        from .scoring import ScoringEngine

        noise_set = cast(Any, runtime["noise_words"])
        alias_table = cast(Any, runtime["alias_table"])

        SCORING_ENGINE = ScoringEngine(alias_table, noise_words=noise_set)
        SCORING_ENGINE_SIGNATURE = signature
    return SCORING_ENGINE


def weighted_adjustment_tokens(tokens: Set[str], runtime: Optional[dict] = None) -> Set[str]:
    runtime = runtime or get_runtime_config_snapshot()
    engine = get_scoring_engine(runtime=runtime)
    return {str(token).strip().lower() for token in tokens if str(token).strip().lower() in engine.reverse_weights}


def _has_key_fallback_bass_hint(tokens: Set[str]) -> bool:
    return bool(tokens & KEY_FALLBACK_BASS_TOKENS)


def classify_node(
    node: LibNode,
    pack_name: Optional[str] = None,
    global_boosts: Optional[Dict[str, float]] = None,
    token_adjustments: Optional[Dict[str, Dict[str, float]]] = None,
    duration: Optional[float] = None,
    features: Optional[Dict[str, float]] = None,
    min_confidence: Optional[float] = None,
    debug: bool = False,
    runtime: Optional[dict] = None,
) -> Tuple[str, float, dict]:
    runtime = runtime or get_runtime_config_snapshot()
    engine = get_scoring_engine(runtime=runtime)
    global_boosts = global_boosts or {}
    detected_audio_type = _detect_audio_type(node, duration, runtime=runtime, features=features)
    excluded_categories = LOOP_EXCLUSIVE_CATEGORIES if detected_audio_type == "Oneshots" else set()

    f_tokens = tokenize(node.name)
    p_tokens = tokenize(node.path.parent.name)
    pk_tokens = tokenize(pack_name) if pack_name else set()

    f_scores, f_trace = engine.score_tokens_with_trace(f_tokens, debug=debug)
    p_scores, p_trace = engine.score_tokens_with_trace(p_tokens, debug=debug)
    pk_scores, pk_trace = engine.score_tokens_with_trace(pk_tokens, debug=debug)

    trace: Dict[str, Any] = {
        "components": {
            "filename": {
                "label": "file name",
                "text": node.name,
                "tokens": sorted(f_tokens),
                "scores": dict(f_scores),
                "token_trace": f_trace,
                "weight": FILENAME_WEIGHT,
            },
            "parent": {
                "label": "parent folder",
                "text": node.path.parent.name,
                "tokens": sorted(p_tokens),
                "scores": dict(p_scores),
                "token_trace": p_trace,
                "weight": CONTEXT_WEIGHT,
            },
            "pack": {
                "label": "pack name",
                "text": pack_name or "",
                "tokens": sorted(pk_tokens),
                "scores": dict(pk_scores),
                "token_trace": pk_trace,
                "weight": PACK_WEIGHT,
            },
        },
        "global_boosts": [],
        "token_adjustments": [],
        "duration_penalties": [],
        "candidates": [],
        "audio_type_exclusions": [],
        "selected_category": None,
        "selected_score": 0.0,
        "confidence": 0.0,
    }
    if excluded_categories:
        trace["audio_type_exclusions"] = [
            {
                "category": category,
                "audio_type": detected_audio_type,
                "reason": "loop_exclusive_category",
            }
            for category in sorted(excluded_categories)
        ]
    suppression_rules = cast(Dict[str, list[str]], runtime.get("category_suppression_rules", CATEGORY_SUPPRESSION_RULES))

    if f_scores:
        suppressed_f_scores = apply_suppression(dict(f_scores), suppression_rules)
        if suppressed_f_scores != dict(f_scores):
            trace["components"]["filename"]["suppressed_scores"] = dict(suppressed_f_scores)
        eligible_f_scores = {
            category: score for category, score in suppressed_f_scores.items() if category not in excluded_categories
        }
        if not eligible_f_scores:
            f_top_cat, f_top_score = None, 0.0
        else:
            f_top_cat, f_top_score = max(eligible_f_scores.items(), key=lambda item: item[1])
        f_runner_up = max(
            (score for category, score in eligible_f_scores.items() if category != f_top_cat),
            default=0.0,
        )
        if (
            f_top_cat is not None
            and f_top_score >= SHORT_CIRCUIT_THRESHOLD
            and (f_top_score - f_runner_up) >= MARGIN_THRESHOLD
        ):
            trace["selected_category"] = f_top_cat
            trace["selected_score"] = f_top_score
            trace["confidence"] = f_top_score
            return f_top_cat, f_top_score, {"stage": "f_shortcircuit", "raw": suppressed_f_scores, "trace": trace}

    combined = collections.defaultdict(float)

    for category, score in f_scores.items():
        if category in excluded_categories:
            continue
        combined[category] += score * FILENAME_WEIGHT
    for category, score in p_scores.items():
        if category in excluded_categories:
            continue
        combined[category] += score * CONTEXT_WEIGHT
    for category, score in pk_scores.items():
        if category in excluded_categories:
            continue
        combined[category] += score * PACK_WEIGHT
    for category, boost in global_boosts.items():
        if category in excluded_categories:
            continue
        combined[category] += boost
        trace["global_boosts"].append({"category": category, "offset": boost})

    if token_adjustments:
        all_unique_tokens = f_tokens | p_tokens | pk_tokens
        for token in sorted(weighted_adjustment_tokens(all_unique_tokens, runtime)):
            if token in token_adjustments:
                for adj_cat, offset in token_adjustments[token].items():
                    if adj_cat in excluded_categories:
                        continue
                    combined[adj_cat] += offset
                    trace["token_adjustments"].append({"token": token, "category": adj_cat, "offset": offset})

    if duration is not None and duration > 1.5:
        percussive_cats = set(runtime["percussive_categories"])
        for category in sorted(percussive_cats):
            if category in combined:
                combined[category] -= 0.5
                trace["duration_penalties"].append(
                    {"category": category, "offset": -0.5, "reason": "long_duration_percussive"}
                )

    before_suppression = dict(combined)
    suppressed_combined = apply_suppression(before_suppression, suppression_rules)
    if suppressed_combined != before_suppression:
        trace["suppression"] = [
            {
                "suppressor": suppressor,
                "target": target,
                "previous_score": before_suppression.get(target, 0.0),
            }
            for suppressor, targets in suppression_rules.items()
            if before_suppression.get(suppressor, 0.0) >= SUPPRESS_TRIGGER
            for target in targets
            if target in before_suppression and suppressed_combined.get(target) != before_suppression.get(target)
        ]
        combined = collections.defaultdict(float, suppressed_combined)

    if not combined:
        if KEY_REGEX_PATTERN.search(node.name):
            conf = min(1.0, KEY_FALLBACK_CONFIDENCE / 1.5)
            trace["selected_score"] = 0.0
            trace["confidence"] = conf
            if _has_key_fallback_bass_hint(f_tokens):
                trace["selected_category"] = "Bass"
                return "Bass", conf, {"stage": "key_fallback_bass", "raw": combined, "trace": trace}
            trace["selected_category"] = "Melodics"
            return "Melodics", conf, {"stage": "key_fallback", "raw": combined, "trace": trace}
        trace["selected_category"] = "Uncategorized"
        return "Uncategorized", 0.0, {"stage": "no_signal", "raw": combined, "trace": trace}

    sorted_res = sorted(combined.items(), key=lambda item: item[1], reverse=True)
    top_cat, top_score = sorted_res[0]

    if top_score < NO_SIGNAL_THRESHOLD:
        if KEY_REGEX_PATTERN.search(node.name):
            conf = min(1.0, KEY_FALLBACK_CONFIDENCE / 1.5)
            trace["selected_score"] = top_score
            trace["confidence"] = conf
            if _has_key_fallback_bass_hint(f_tokens):
                trace["selected_category"] = "Bass"
                return "Bass", conf, {"stage": "key_fallback_bass", "raw": combined, "trace": trace}
            trace["selected_category"] = "Melodics"
            return "Melodics", conf, {"stage": "key_fallback", "raw": combined, "trace": trace}
        conf = max(0.0, min(1.0, top_score / 1.5))
        trace["selected_category"] = "Uncategorized"
        trace["selected_score"] = top_score
        trace["confidence"] = conf
        return "Uncategorized", conf, {"stage": "noise_floor", "raw": combined, "trace": trace}

    candidates = [top_cat]
    for category, score in sorted_res[1:]:
        if (top_score - score) < MARGIN_THRESHOLD:
            candidates.append(category)

    result_category = top_cat
    result_raw_score = top_score
    result_meta: dict[str, Any] = {"stage": "final", "raw": combined}

    if len(candidates) > 1:
        all_tokens = list(f_tokens) + list(p_tokens) + list(pk_tokens)
        result_category = engine.resolve_tie(all_tokens, candidates)
        result_raw_score = combined[result_category]
        result_meta = {"stage": "specificity", "candidates": candidates, "raw": combined}

    result_conf = max(0.0, min(1.0, result_raw_score / 1.5))
    trace["candidates"] = list(candidates)
    trace["selected_category"] = result_category
    trace["selected_score"] = result_raw_score
    trace["confidence"] = result_conf
    result_meta["trace"] = trace

    if debug:
        top_three = dict(sorted(combined.items(), key=lambda item: item[1], reverse=True)[:3])
        if len(candidates) > 1:
            print(f"  [DEBUG] TIE: {top_three}...")
            print(f"  [DEBUG] Spec chose: {result_category} from {candidates}")
        else:
            print(f"  [DEBUG] WIN: {result_category} ({result_conf:.4f}) | {top_three}...")

    return result_category, result_conf, result_meta


def detect_audio_type(
    node: LibNode,
    duration: Optional[float] = None,
    runtime: Optional[dict] = None,
    features: Optional[Dict[str, float]] = None,
) -> str:
    return _detect_audio_type(node, duration, runtime=runtime, features=features)
