import re
from typing import Literal, List, Set, overload


_CAMEL_CHECK = re.compile(r"[a-z\u00DF-\u017F][A-Z\u00C0-\u00DE]")


@overload
def tokenize(text: str, flatten: Literal[True] = True) -> Set[str]:
    ...


@overload
def tokenize(text: str, flatten: Literal[False]) -> List[str]:
    ...


def tokenize(text: str, flatten: bool = True) -> Set[str] | List[str]:
    """
    Splits text into meaningful word fragments, supporting CamelCase and
    alphanumeric boundaries.
    """
    tokens = []
    for part in re.split(r"[^a-zA-Z0-9\u00C0-\u017F]", text):
        if not part:
            continue

        if part.isalpha() and not _CAMEL_CHECK.search(part):
            token = part.lower()
            if len(token) > 1:
                tokens.append(token)
            continue

        if part.isdigit():
            tokens.append(part)
            continue

        sub = re.findall(
            r"[A-Z\u00C0-\u00DE]?[a-z\u00DF-\u017F]+|"
            r"[A-Z\u00C0-\u00DE]+(?=[A-Z\u00C0-\u00DE][a-z\u00DF-\u017F]|\d|$)|"
            r"[0-9]+",
            part,
        )
        if sub:
            for item in sub:
                token = item.lower()
                if len(token) == 1 and token.isalpha():
                    continue
                tokens.append(token)
        else:
            token = part.lower()
            if not (len(token) == 1 and token.isalpha()):
                tokens.append(token)

    return set(tokens) if flatten else tokens
