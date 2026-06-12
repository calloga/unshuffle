from ...core.constants import LOOP_INDICATORS, NOISE_WORDS, ONESHOT_INDICATORS
from ...core.models import LibNode
from ...core.tokenizer import tokenize
from ...logic.classification import is_category_alias


def _is_generic_token(token: str) -> bool:
    token = (token or "").strip().lower()
    if not token:
        return True
    if token.isdigit():
        return True
    if is_category_alias(token):
        return True
    if token in NOISE_WORDS:
        return True
    if token in ONESHOT_INDICATORS:
        return True
    if token in LOOP_INDICATORS:
        return True
    if token.endswith("s") and is_category_alias(token[:-1]):
        return True
    return False


def is_generic_folder(node: LibNode) -> bool:
    tokens = tokenize(node.name)
    if not tokens:
        return True
    return all(_is_generic_token(token) for token in tokens)
