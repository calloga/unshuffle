from PySide6.QtCore import QObject, Signal
from ..utils.constants import StagingColumn, SEARCH_PREFIX_MAP
from unshuffle.bridge.search_bridge import SearchBridge

class SearchEngine(QObject):
    """
    Handles FTS5 search orchestration and query mapping for the staging table.
    """
    resultsFound = Signal(object)


    def __init__(self, engine=None):
        super().__init__()
        self.engine = None
        self.bridge = None
        if engine is not None:
            self.set_engine(engine)

    def set_engine(self, engine):
        self.engine = engine
        if engine is None:
            self.bridge = None
        elif isinstance(engine, SearchBridge):
            self.bridge = engine
        else:
            workflow = getattr(engine, "workflow", None)
            self.bridge = SearchBridge(workflow or engine)

    def set_bridge(self, bridge):
        self.bridge = bridge
        self.engine = bridge.workflow if bridge else None

    def execute_search(self, query_text):
        return self.run_query(self.bridge, query_text)

    @classmethod
    def has_database_terms(cls, query_text: str) -> bool:
        for group in cls.parse_query_groups((query_text or "").strip()):
            for term in group:
                if term.strip() and not cls.is_confidence_query(term):
                    return True
        return False

    @classmethod
    def run_query(cls, bridge, query_text):
        """
        Processes a raw search string into a structured FTS5 query.
        Returns a set of matched IDs or a ranked list for similarity searches.
        """
        query_text = query_text.strip()
        if not query_text:
            return None

        if bridge is not None and not isinstance(bridge, SearchBridge):
            workflow = getattr(bridge, "workflow", None)
            bridge = SearchBridge(workflow or bridge)

        if not bridge or not bridge.has_session():
            return set()

        groups = cls.parse_query_groups(query_text)
        if not groups:
            return None

        union_ids = set()
        ranked_result = None
        has_fts_terms = False
        for group in groups:
            canonical_terms = [
                cls.canonicalize_term(term)
                for term in group
                if term.strip() and not cls.is_confidence_query(term)
            ]
            canonical_query = ",".join(canonical_terms)
            if not canonical_query:
                continue
            has_fts_terms = True
            matched_ids = bridge.search_staging(canonical_query)
            if isinstance(matched_ids, list) and cls.is_similarity_query(canonical_query):
                ranked_result = matched_ids if ranked_result is None else [i for i in ranked_result if i in set(matched_ids)]
            else:
                union_ids.update(set(matched_ids))

        if not has_fts_terms:
            return None

        if ranked_result is not None and not union_ids:
            return ranked_result
        if ranked_result is not None:
            return ranked_result + [i for i in union_ids if i not in set(ranked_result)]
        return union_ids

    @classmethod
    def parse_query_groups(cls, query_text: str) -> list[list[str]]:
        """Parse a search string into OR groups containing AND terms.

        Commas and the word ``and`` are AND separators. The word ``or`` and
        ``|`` are OR separators. Quoted text is kept intact.
        """
        tokens = cls._split_query_tokens(query_text)
        groups = [[]]
        current = []
        for token in tokens:
            marker = token.lower()
            if marker in {"or", "|"}:
                if current:
                    groups[-1].append(" ".join(current).strip())
                    current = []
                if groups[-1]:
                    groups.append([])
                continue
            if marker in {"and", ",", "&"}:
                if current:
                    groups[-1].append(" ".join(current).strip())
                    current = []
                continue
            current.append(token)
        if current:
            groups[-1].append(" ".join(current).strip())
        return [group for group in groups if group]

    @classmethod
    def active_prefixes(cls, query_text: str) -> set[str]:
        prefixes = set()
        for group in cls.parse_query_groups(query_text):
            for term in group:
                field = cls._split_field_term(term)
                if not field:
                    continue
                prefix, _value = field
                mapped = SEARCH_PREFIX_MAP.get(prefix, prefix)
                prefixes.add(mapped)
        return prefixes

    @classmethod
    def canonicalize_term(cls, term: str) -> str:
        term = term.strip()
        field = cls._split_field_term(term)
        if not field:
            return term
        prefix, value = field
        mapped = SEARCH_PREFIX_MAP.get(prefix)
        if mapped == "audio_type":
            value = cls._canonical_audio_type_value(value)
        return f"{mapped}:{value.strip()}" if mapped else term

    @staticmethod
    def _canonical_audio_type_value(value: str) -> str:
        stripped = str(value or "").strip()
        quote = ""
        if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in {"'", '"'}:
            quote = stripped[0]
            inner = stripped[1:-1].strip()
        else:
            inner = stripped

        normalized = inner.casefold().replace("_", " ").replace("-", " ")
        normalized = " ".join(normalized.split())
        if normalized in {"utility", "non audio", "non audio asset", "non audio assets", "nonaudio", "nonaudio assets"}:
            inner = "Non-Audio Assets"

        return f"{quote}{inner}{quote}" if quote else inner

    @staticmethod
    def _split_field_term(term: str):
        in_quote = False
        for idx, ch in enumerate(term):
            if ch == '"':
                in_quote = not in_quote
            elif not in_quote and ch in {":", "="}:
                prefix = term[:idx].strip().lower()
                if prefix:
                    return prefix, term[idx + 1 :]
        return None

    @staticmethod
    def _split_query_tokens(query_text: str) -> list[str]:
        tokens = []
        current = []
        in_quote = False
        for ch in query_text:
            if ch == '"':
                in_quote = not in_quote
                current.append(ch)
                continue
            if not in_quote and ch in {",", "|", "&"}:
                if current:
                    tokens.append("".join(current).strip())
                    current = []
                tokens.append(ch)
                continue
            if not in_quote and ch.isspace():
                if current:
                    tokens.append("".join(current).strip())
                    current = []
                continue
            current.append(ch)
        if current:
            tokens.append("".join(current).strip())
        return [t for t in tokens if t]

    @staticmethod
    def is_similarity_query(query_text):
        return "similar:" in query_text.lower()

    @staticmethod
    def is_confidence_query(query_text):
        field = SearchEngine._split_field_term(str(query_text or "").strip())
        if not field:
            return False
        prefix, _value = field
        return prefix.lower() in {"conf", "confidence"}
