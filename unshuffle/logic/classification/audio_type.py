from typing import Dict, List, Optional

from ...core.constants import (
    BPM_REGEX_PATTERN,
    LOOP_PROBABILITY_THRESHOLD,
    LOOP_SPECIFICITY_MALUS,
    SHORT_DURATION_MALUS,
    WEAK_LOOP_PROBABILITY,
    get_runtime_config_snapshot,
)
from ...core.models import LibNode
from ...core.tokenizer import tokenize


def _token_matches_indicator(token: str, indicator_token: str) -> bool:
    return token == indicator_token or (token.endswith("s") and token[:-1] == indicator_token)


def _contains_indicator_sequence(tokens: List[str], indicator: str) -> bool:
    indicator_tokens = tokenize(indicator, flatten=False)
    if not indicator_tokens:
        return False

    window = len(indicator_tokens)
    for start in range(0, len(tokens) - window + 1):
        if all(_token_matches_indicator(tokens[start + offset], indicator_tokens[offset]) for offset in range(window)):
            return True
    return False


def _safe_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def detect_audio_type(
    node: LibNode,
    duration: Optional[float] = None,
    runtime: Optional[dict] = None,
    features: Optional[Dict[str, float]] = None,
) -> str:
    runtime = runtime or get_runtime_config_snapshot()
    oneshot_indicators = runtime["oneshot_indicators"]
    loop_indicators = runtime["loop_indicators"]
    weak_loop_indicators = runtime["weak_loop_indicators"]
    model_numbers = runtime["model_numbers"]
    oneshot_hint_tokens = runtime["oneshot_hint_tokens"]

    ext = node.extension
    name_tokens = tokenize(node.name, flatten=False)
    parent_tokens = tokenize(node.path.parent.name, flatten=False)

    if node.name.startswith("._"):
        return "Metadata"
    from ...core.constants import AUDIO_EXTS

    if ext not in AUDIO_EXTS:
        return "Non-Audio Assets"

    if any(_contains_indicator_sequence(name_tokens, indicator) for indicator in oneshot_indicators):
        return "Oneshots"

    loop_probability = 0.0

    has_filename_loop = any(_contains_indicator_sequence(name_tokens, indicator) for indicator in loop_indicators)
    has_parent_loop = any(_contains_indicator_sequence(parent_tokens, indicator) for indicator in loop_indicators)
    has_named_oneshot_hint = any(_contains_indicator_sequence(name_tokens, token) for token in oneshot_hint_tokens)

    bpm_match = BPM_REGEX_PATTERN.search(node.name.lower())
    if bpm_match and bpm_match.group(1).strip() in model_numbers:
        bpm_match = None

    parent_bpm_match = BPM_REGEX_PATTERN.search(node.path.parent.name.lower()) if not bpm_match else None
    if parent_bpm_match and parent_bpm_match.group(1).strip() in model_numbers:
        parent_bpm_match = None

    if has_filename_loop or bpm_match:
        loop_probability = 1.0
    elif has_parent_loop or parent_bpm_match:
        loop_probability = 1.0
        if has_named_oneshot_hint:
            loop_probability -= LOOP_SPECIFICITY_MALUS
    elif any(_contains_indicator_sequence(name_tokens, indicator) for indicator in weak_loop_indicators) or any(
        _contains_indicator_sequence(parent_tokens, indicator) for indicator in weak_loop_indicators
    ):
        loop_probability = WEAK_LOOP_PROBABILITY

    if duration is not None:
        if duration < 0.4:
            loop_probability = 0.0
        elif duration < 1.0 and loop_probability > 0:
            loop_probability -= SHORT_DURATION_MALUS * 2

    if loop_probability >= LOOP_PROBABILITY_THRESHOLD:
        return "Loops"

    if duration is not None and duration >= 2.5:
        features = features or {}
        transient_tail_score = _safe_float(features.get("transient_tail_score"), 0.0)
        loopiness_score = _safe_float(features.get("loopiness_score"), 0.0)
        if transient_tail_score >= 0.5:
            return "Oneshots"
        if loopiness_score >= 0.5 and not has_named_oneshot_hint:
            return "Loops"
        if duration >= 5.0 and not has_named_oneshot_hint:
            return "Loops"

    return "Oneshots"
