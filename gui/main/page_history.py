from __future__ import annotations

PageKey = tuple[str, str | None]


def record_page_history(
    history: list[PageKey],
    index: int,
    key: PageKey,
) -> tuple[list[PageKey], int, bool]:
    if index >= 0 and history[index] == key:
        return history, index, False
    if index < len(history) - 1:
        history = history[: index + 1]
    history.append(key)
    return history, len(history) - 1, True


def previous_page_index(index: int) -> int | None:
    if index <= 0:
        return None
    return index - 1


def next_page_index(history: list[PageKey], index: int) -> int | None:
    if index < 0 or index >= len(history) - 1:
        return None
    return index + 1


def carousel_page_value(key: PageKey | None) -> str:
    page = key[0] if key else "library"
    return "library" if page == "dock" else page
