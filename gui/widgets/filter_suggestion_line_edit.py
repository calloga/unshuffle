from __future__ import annotations

from typing import Any, Callable, cast

from PySide6.QtCore import QStringListModel, Qt, QTimer
from PySide6.QtWidgets import QCompleter, QLineEdit

from ..core.search_engine import SearchEngine
from ..utils.styles import apply_style


StyleProvider = Callable[[], str]

SUGGESTION_PREFIX_ALIASES = {
    "cat": "category",
    "category": "category",
    "sub": "subcategory",
    "subcategory": "subcategory",
    "pack": "packname",
    "packname": "packname",
    "name": "name",
    "file": "name",
    "filename": "name",
    "tag": "tag",
    "tags": "tag",
    "type": "type",
}


class FilterSuggestionLineEdit(QLineEdit):
    """Autocomplete line edit for Unshuffle filter/query syntax."""

    def __init__(
        self,
        parent=None,
        *,
        popup_object_name: str = "FilterSuggestionCompleter",
        popup_style_provider: StyleProvider | None = None,
    ):
        super().__init__(parent)
        self._suggestions: list[str] = []
        self._suggestion_pairs: list[tuple[str, str]] = []
        self._suggestions_by_prefix: dict[str, list[tuple[str, str, str]]] = {}
        self._saved_filter_suggestions: list[str] = []
        self._completion_insertions: dict[str, str] = {}
        self._popup_style_provider = popup_style_provider
        self._completion_timer = QTimer(self)
        self._completion_timer.setSingleShot(True)
        self._completion_timer.setInterval(50)
        self._completion_timer.timeout.connect(self._refresh_completion)
        self._model = QStringListModel(self)
        self._completer = QCompleter(self._model, self)
        popup = self._completer.popup()
        if popup is not None:
            popup.setObjectName(popup_object_name)
            self._apply_popup_style()
        self._completer.setCaseSensitivity(Qt.CaseInsensitive)
        self._completer.setFilterMode(Qt.MatchContains)
        self._completer.setCompletionMode(QCompleter.PopupCompletion)
        activated = getattr(self._completer, "textActivated", None)
        if activated is None:
            activated = cast(Any, self._completer.activated)[str]
        activated.connect(self._accept_completion)
        self.setCompleter(self._completer)
        self.textEdited.connect(lambda _text: self._completion_timer.start())

    def set_suggestions(self, suggestions: list[str], saved_filters: list[str] | None = None) -> None:
        self._suggestions = list(dict.fromkeys(item for item in suggestions if item.strip()))
        self._suggestion_pairs = [(item, item.lower()) for item in self._suggestions]
        self._suggestions_by_prefix = {}
        for item in self._suggestions:
            field = SearchEngine._split_field_term(item)
            if not field:
                continue
            prefix, value = field
            suggestion_prefix = SUGGESTION_PREFIX_ALIASES.get(prefix.lower(), prefix.lower())
            display_value = value.strip().strip('"')
            self._suggestions_by_prefix.setdefault(suggestion_prefix, []).append(
                (item, display_value.lower(), item.lower())
            )
        self._saved_filter_suggestions = list(
            dict.fromkeys(item for item in saved_filters or [] if item.strip())
        )

    def refresh_theme(self) -> None:
        self._apply_popup_style()

    def focusInEvent(self, event):
        super().focusInEvent(event)
        if not self.text().strip() and self._saved_filter_suggestions:
            self._show_candidates(self._saved_filter_suggestions, "")

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Tab:
            current = self._completer.currentCompletion() or self._top_completion()
            if current:
                self._accept_completion(current)
                event.accept()
                return
        super().keyPressEvent(event)

    def _apply_popup_style(self) -> None:
        popup = self._completer.popup()
        if popup is not None and self._popup_style_provider is not None:
            apply_style(popup, self._popup_style_provider())

    def _refresh_completion(self, _text: str = "") -> None:
        fragment = self._current_fragment()
        if len(fragment.replace('"', "").strip()) < 2:
            self._completer.popup().hide()
            return
        matches = self._matching_suggestions(fragment)
        self._show_candidates(matches, fragment)

    def _show_candidates(self, candidates: list[str], prefix: str) -> None:
        self._completion_insertions = {}
        display_candidates = self._display_candidates(candidates[:80])
        self._model.setStringList(display_candidates)
        if display_candidates:
            self._completer.setCompletionPrefix("")
            self._completer.complete()
        else:
            self._completer.popup().hide()

    def _display_candidates(self, candidates: list[str]) -> list[str]:
        text = self.text()
        start, end = self._fragment_bounds(text, self.cursorPosition())
        has_context = bool(text[:start].strip() or text[end:].strip())
        display: list[str] = []
        seen: set[str] = set()
        for candidate in candidates:
            label = f"{text[:start]}{candidate}{text[end:]}" if has_context else candidate
            if label in seen:
                continue
            seen.add(label)
            display.append(label)
            self._completion_insertions[label] = candidate
        return display

    def _top_completion(self) -> str:
        fragment = self._current_fragment()
        matches = self._matching_suggestions(fragment)
        return matches[0] if matches else ""

    def _matching_suggestions(self, fragment: str) -> list[str]:
        field_match = self._field_fragment(fragment)
        if field_match is not None:
            prefix, value = field_match
            scoped = self._suggestions_by_prefix.get(prefix, [])
            if not value:
                return [item for item, _value_lower, _item_lower in scoped]
            starts = []
            contains = []
            for item, value_lower, item_lower in scoped:
                if value_lower.startswith(value) or item_lower.startswith(fragment.strip().lower()):
                    starts.append(item)
                elif value in value_lower or value in item_lower:
                    contains.append(item)
            return [*starts, *contains]

        needle = fragment.strip().lower().strip('"')
        if not needle:
            return []
        starts = []
        contains = []
        for item, hay in self._suggestion_pairs:
            if hay.startswith(needle):
                starts.append(item)
            elif needle in hay:
                contains.append(item)
        return [*starts, *contains]

    @staticmethod
    def _field_fragment(fragment: str) -> tuple[str, str] | None:
        field = SearchEngine._split_field_term(fragment.strip())
        if not field:
            return None
        prefix, value = field
        normalized_prefix = SUGGESTION_PREFIX_ALIASES.get(prefix.lower(), prefix.lower())
        if not normalized_prefix:
            return None
        return normalized_prefix, value.strip().strip('"').lower()

    def _current_fragment(self) -> str:
        start, end = self._fragment_bounds(self.text(), self.cursorPosition())
        return self.text()[start:end].strip()

    def _accept_completion(self, completion: str) -> None:
        completion = self._completion_insertions.get(completion, completion)
        text = self.text()
        start, end = self._fragment_bounds(text, self.cursorPosition())
        new_text = text[:start] + completion + text[end:]
        self.setText(new_text)
        self.setCursorPosition(start + len(completion))
        self._completer.popup().hide()

    @classmethod
    def _fragment_bounds(cls, text: str, cursor: int) -> tuple[int, int]:
        SearchEngine._split_query_tokens(text)
        cursor = max(0, min(cursor, len(text)))
        start = cls._fragment_start(text, cursor)
        end = cls._fragment_end(text, cursor)
        while start < end and text[start].isspace():
            start += 1
        while end > start and text[end - 1].isspace():
            end -= 1
        return start, end

    @classmethod
    def _fragment_start(cls, text: str, cursor: int) -> int:
        in_quote = False
        quote_char = ""
        start = 0
        word = ""
        word_start = 0
        i = 0
        while i < cursor:
            ch = text[i]
            if ch == '"':
                if in_quote and ch == quote_char:
                    in_quote = False
                    quote_char = ""
                elif not in_quote:
                    in_quote = True
                    quote_char = ch
                word = ""
                i += 1
                continue
            if in_quote:
                i += 1
                continue
            if ch in {",", "|", "&"}:
                start = i + 1
                word = ""
                i += 1
                continue
            if ch.isspace():
                if word.lower() in {"and", "or"}:
                    start = i + 1
                word = ""
                i += 1
                continue
            if not word:
                word_start = i
            word += ch
            if word.lower() in {"and", "or"}:
                next_i = i + 1
                if next_i == cursor or next_i >= len(text) or text[next_i].isspace():
                    start = next_i
                    word = ""
            i += 1
        if word.lower() in {"and", "or"} and word_start >= start:
            start = cursor
        return start

    @classmethod
    def _fragment_end(cls, text: str, cursor: int) -> int:
        in_quote = False
        quote_char = ""
        word = ""
        i = cursor
        while i < len(text):
            ch = text[i]
            if ch == '"':
                if in_quote and ch == quote_char:
                    in_quote = False
                    quote_char = ""
                elif not in_quote:
                    in_quote = True
                    quote_char = ch
                word = ""
                i += 1
                continue
            if in_quote:
                i += 1
                continue
            if ch in {",", "|", "&"}:
                return i
            if ch.isspace():
                if word.lower() in {"and", "or"}:
                    return max(cursor, i - len(word))
                word = ""
                i += 1
                continue
            word += ch
            i += 1
        if word.lower() in {"and", "or"}:
            return len(text) - len(word)
        return len(text)
