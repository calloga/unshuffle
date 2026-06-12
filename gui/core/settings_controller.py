import json
import logging
from PySide6.QtCore import QObject, Signal, QByteArray, QSettings

from gui.styles import DEFAULT_THEME_KEY, SYSTEM_THEME_KEY, normalize_theme_key
from gui.utils.settings_helpers import create_app_qsettings


WINDOW_GEOMETRY_KEY = "window_geometry"
DOCKED_GEOMETRY_KEY = "docked_geometry"
DOCKED_MODE_KEY = "docked_mode"
CLASSIFICATION_RANGE_MIN_KEY = "classification_range_min"
CLASSIFICATION_RANGE_MAX_KEY = "classification_range_max"
DEFAULT_VIEW_TREE_KEY = "default_view_tree"
DEFAULT_VIEW_MODE_KEY = "default_view_mode"
THEME_KEY = "theme_key"
FOLLOW_SYSTEM_THEME_KEY = "follow_system_theme"
ZOOM_PERCENT_KEY = "zoom_percent"
RECENT_SEARCHES_KEY = "recent_searches_json"
SAVED_FILTERS_JSON_KEY = "saved_filters_json"
SAVED_FILTERS_BY_SESSION_PREFIX = "saved_filters_json_by_session/"
AUTO_CHECK_COHERENCE_ON_START_KEY = "auto_check_coherence_on_start"
CURRENT_PAGE_KEY = "current_page"
CURRENT_SYSTEM_SECTION_KEY = "current_system_section"
LIBRARY_VIEW_MODES_KEY = "library_view_modes_json"
LIBRARY_PAGE_STATE_KEY = "library_page_state_json"
LIBRARY_PAGE_STATE_BY_SESSION_PREFIX = "library_page_state_json_by_session/"
DEFAULT_LIBRARY_VIEW_MODES = ("table", "tree", "map")
SHOW_STARTUP_LAUNCHER_KEY = "show_startup_launcher"
STARTUP_LAUNCHER_LAST_CHOICE_KEY = "startup_launcher_last_choice_json"


def create_app_settings() -> QSettings:
    return create_app_qsettings()


def normalize_library_view_modes(value) -> list[str]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            value = [item.strip() for item in value.split(",")]
    modes = []
    for item in value or []:
        mode = str(item or "").strip().lower()
        if mode in DEFAULT_LIBRARY_VIEW_MODES and mode not in modes:
            modes.append(mode)
    ordered = [mode for mode in DEFAULT_LIBRARY_VIEW_MODES if mode in modes]
    return ordered or list(DEFAULT_LIBRARY_VIEW_MODES)


class SettingsController(QObject):
    """
    Handles persistence of user preferences, history, and window state.
    """
    settingsLoaded = Signal(dict)
    recentSearchesUpdated = Signal(list)
    savedFiltersUpdated = Signal(list)

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.app = parent 

    def build_app_settings_state(self) -> dict:
        explicit_theme = normalize_theme_key(self.settings.value(THEME_KEY, DEFAULT_THEME_KEY))
        follow_system = self.settings.value(FOLLOW_SYSTEM_THEME_KEY, False, type=bool)
        theme_key = SYSTEM_THEME_KEY if follow_system else explicit_theme
        is_docked = self.settings.value(DOCKED_MODE_KEY, False, type=bool)
        geom_key = DOCKED_GEOMETRY_KEY if is_docked else WINDOW_GEOMETRY_KEY
        return {
            "geometry": self.settings.value(geom_key),
            "docked_mode": is_docked,
            "classification_range_min": 0.0,
            "default_view_tree": self.settings.value(DEFAULT_VIEW_TREE_KEY, False, type=bool),
            "default_view_mode": self.get_default_view_mode(),
            "theme_key": theme_key,
            "follow_system_theme": follow_system,
            "zoom_percent": int(self.settings.value(ZOOM_PERCENT_KEY, 100, type=int)),
            "current_page": "library",
            "current_system_section": self.settings.value(CURRENT_SYSTEM_SECTION_KEY, "tree_organization"),
            "library_view_modes": self.get_library_view_modes(),
            "library_page_state": self.get_library_page_state(),
        }

    def load_app_settings(self) -> dict:
        """Loads persistent app state and emits it for the UI shell to apply."""
        state = self.build_app_settings_state()
        self.settingsLoaded.emit(state)
        return state
            
    def save_app_settings(self):
        """Persists window geometry and state."""
        is_docked = False
        if self.app and hasattr(self.app, "stack") and hasattr(self.app, "dock_view"):
            is_docked = (self.app.stack.currentWidget() == self.app.dock_view)
        
        if is_docked:
            self.settings.setValue(DOCKED_GEOMETRY_KEY, self.app.saveGeometry())
        else:
            self.settings.setValue(WINDOW_GEOMETRY_KEY, self.app.saveGeometry())

    def load_all(self):
        """Loads and returns all persistent settings for the UI to apply."""
        return {
            "geometry": self.get_window_geometry(),
            "default_view_tree": self.settings.value(DEFAULT_VIEW_TREE_KEY, False, type=bool),
            "default_view_mode": self.get_default_view_mode(),
            "library_view_modes": self.get_library_view_modes(),
            "library_page_state": self.get_library_page_state(),
            "recent_searches": self.get_recent_searches(),
            "saved_filters": self.get_saved_filters(),
        }

    def get_window_geometry(self) -> QByteArray:
        return self.settings.value(WINDOW_GEOMETRY_KEY)

    def set_window_geometry(self, geometry: QByteArray):
        self.settings.setValue(WINDOW_GEOMETRY_KEY, geometry)

    def save_view_default(self, view_mode):
        if isinstance(view_mode, bool):
            normalized = "tree" if view_mode else "table"
        else:
            normalized = str(view_mode or "").strip().lower()
            if normalized not in DEFAULT_LIBRARY_VIEW_MODES:
                normalized = "table"
        self.settings.setValue(DEFAULT_VIEW_MODE_KEY, normalized)
        self.settings.setValue(DEFAULT_VIEW_TREE_KEY, normalized == "tree")

    def get_default_view_mode(self) -> str:
        raw = str(self.settings.value(DEFAULT_VIEW_MODE_KEY, "") or "").strip().lower()
        if raw in DEFAULT_LIBRARY_VIEW_MODES:
            return raw
        return "tree" if self.settings.value(DEFAULT_VIEW_TREE_KEY, False, type=bool) else "table"

    def get_library_view_modes(self) -> list[str]:
        raw = self.settings.value(LIBRARY_VIEW_MODES_KEY, "")
        if not raw:
            return ["table", "tree", "map"]
        return normalize_library_view_modes(raw)

    def set_library_view_modes(self, modes) -> None:
        normalized = normalize_library_view_modes(modes)
        self.settings.setValue(LIBRARY_VIEW_MODES_KEY, json.dumps(normalized))

    def get_library_page_state(self) -> dict:
        raw_json = ""
        scoped_key = self._current_library_page_state_key()
        if scoped_key:
            raw_json = self.settings.value(scoped_key, "")
        if not raw_json:
            raw_json = self.settings.value(LIBRARY_PAGE_STATE_KEY, "")
        if not raw_json:
            return {}
        try:
            data = json.loads(str(raw_json))
        except (TypeError, json.JSONDecodeError):
            return {}
        return self._normalize_library_page_state(data)

    def save_library_page_state(self, state: dict) -> None:
        normalized = self._normalize_library_page_state(state)
        raw = json.dumps(normalized)
        scoped_key = self._current_library_page_state_key()
        if scoped_key:
            self.settings.setValue(scoped_key, raw)
        else:
            self.settings.setValue(LIBRARY_PAGE_STATE_KEY, raw)

    def _normalize_library_page_state(self, state: dict) -> dict:
        if not isinstance(state, dict):
            return {}
        normalized: dict[str, object] = {
            "query": str(state.get("query") or "").strip(),
        }
        raw_types = state.get("audio_types", None)
        if raw_types is None:
            normalized["audio_types"] = None
        else:
            type_values = {
                str(value or "").strip()
                for value in (raw_types if isinstance(raw_types, (list, tuple, set)) else [raw_types])
            }
            selected = [value for value in ("Oneshots", "Loops") if value in type_values]
            normalized["audio_types"] = selected or None
        view_mode = str(state.get("view_mode") or "").strip().lower()
        if view_mode in DEFAULT_LIBRARY_VIEW_MODES:
            normalized["view_mode"] = view_mode
        return normalized

    def get_show_startup_launcher(self) -> bool:
        return self.settings.value(SHOW_STARTUP_LAUNCHER_KEY, True, type=bool)

    def set_show_startup_launcher(self, enabled: bool) -> None:
        self.settings.setValue(SHOW_STARTUP_LAUNCHER_KEY, enabled)

    def get_startup_launcher_last_choice(self) -> dict:
        raw = self.settings.value(STARTUP_LAUNCHER_LAST_CHOICE_KEY, "")
        if not raw:
            return {}
        try:
            data = json.loads(str(raw))
            return data if isinstance(data, dict) else {}
        except (TypeError, json.JSONDecodeError):
            return {}

    def set_startup_launcher_last_choice(self, choice: dict) -> None:
        self.settings.setValue(STARTUP_LAUNCHER_LAST_CHOICE_KEY, json.dumps(dict(choice or {})))

    def save_docked_mode(self, checked: bool) -> None:
        self.settings.setValue(DOCKED_MODE_KEY, checked)

    def get_theme_key(self) -> str:
        explicit_theme = normalize_theme_key(self.settings.value(THEME_KEY, DEFAULT_THEME_KEY))
        follow_system = self.settings.value(FOLLOW_SYSTEM_THEME_KEY, False, type=bool)
        return SYSTEM_THEME_KEY if follow_system else explicit_theme

    def set_theme_key(self, theme_key: str) -> None:
        normalized = normalize_theme_key(theme_key)
        if normalized == SYSTEM_THEME_KEY:
            self.settings.setValue(FOLLOW_SYSTEM_THEME_KEY, True)
            return
        self.settings.setValue(THEME_KEY, normalized)
        self.settings.setValue(FOLLOW_SYSTEM_THEME_KEY, False)

    def get_zoom_percent(self) -> int:
        return int(self.settings.value(ZOOM_PERCENT_KEY, 100, type=int))

    def set_zoom_percent(self, zoom_percent: int) -> None:
        self.settings.setValue(ZOOM_PERCENT_KEY, zoom_percent)

    def get_auto_check_coherence_on_start(self) -> bool:
        return self.settings.value(AUTO_CHECK_COHERENCE_ON_START_KEY, True, type=bool)

    def set_auto_check_coherence_on_start(self, enabled: bool) -> None:
        self.settings.setValue(AUTO_CHECK_COHERENCE_ON_START_KEY, enabled)

    def set_current_page(self, page: str, system_section: str | None = None) -> None:
        self.settings.setValue(CURRENT_PAGE_KEY, (page or "library"))
        if system_section:
            self.settings.setValue(CURRENT_SYSTEM_SECTION_KEY,system_section)

    def get_recent_searches(self) -> list:
        raw = self.settings.value(RECENT_SEARCHES_KEY, "")
        if not raw:
            return []
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                return data
        except (TypeError, json.JSONDecodeError):
            pass
        return []

    def add_recent_search(self, query: str, limit: int = 12):
        query = (query or "").strip()
        if not query:
            return
        
        current = self.get_recent_searches()
        updated = [item for item in current if item.lower() != query.lower()]
        updated.insert(0, query)
        updated = updated[:limit]
        
        self.settings.setValue(RECENT_SEARCHES_KEY, json.dumps(updated))
        self.recentSearchesUpdated.emit(updated)

    def get_saved_filters(self) -> list:
        raw_json = ""
        scoped_key = self._current_saved_filters_key()
        if scoped_key:
            raw_json = self.settings.value(scoped_key, "")
        data = []
        if raw_json:
            try:
                data = json.loads(raw_json)
            except (TypeError, json.JSONDecodeError):
                pass

        normalized = []
        seen_queries = set()
        for filt in data:
            if isinstance(filt, dict):
                name = str(filt.get("name", filt.get("query", ""))).strip()
                query = str(filt.get("query", "")).strip()
            else:
                name = str(filt).strip()
                query = name
            
            if query and query not in seen_queries:
                normalized.append({"name": name or query, "query": query})
                seen_queries.add(query)

        return sorted(normalized, key=lambda f: str(f.get("name", "")).lower())

    def _current_session_id(self) -> str:
        engine = getattr(self.app, "engine", None)
        session_id = getattr(engine, "session_id", None) if engine is not None else None
        return str(session_id or "").strip()

    def _current_saved_filters_key(self) -> str | None:
        session_id = self._current_session_id()
        if not session_id:
            return None
        return f"{SAVED_FILTERS_BY_SESSION_PREFIX}{session_id}"

    def _current_library_page_state_key(self) -> str | None:
        session_id = self._current_session_id()
        if not session_id:
            return None
        return f"{LIBRARY_PAGE_STATE_BY_SESSION_PREFIX}{session_id}"

    def get_classification_range(self):
        c_max = self.settings.value(CLASSIFICATION_RANGE_MAX_KEY, 1.0, type=float)
        c_min = 0.0
        return c_min, c_max, c_min

    def save_saved_filters(self, filters: list):
        scoped_key = self._current_saved_filters_key()
        if scoped_key:
            self.settings.setValue(scoped_key, json.dumps(filters))
        else:
            self.settings.setValue(SAVED_FILTERS_JSON_KEY, json.dumps(filters))
        self.savedFiltersUpdated.emit(filters)

    def add_filter(self, name: str, query: str):
        name = name.strip()
        query = query.strip()
        if not name or not query:
            return False
        
        current = self.get_saved_filters()
        if any(f.get("query") == query for f in current):
            return False
            
        current.append({"name": name, "query": query})
        current.sort(key=lambda f: str(f.get("name", "")).lower())
        self.save_saved_filters(current)
        return True

    def remove_filter(self, query: str):
        current = self.get_saved_filters()
        updated = [f for f in current if f.get("query") != query]
        if len(updated) == len(current):
            return False
            
        self.save_saved_filters(updated)
        return True
