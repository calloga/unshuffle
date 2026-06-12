"""Main-window action helpers grouped by responsibility."""

from .build import refresh_build_menu
from .history import clear_history, confirm_undo, refresh_history_menu, reset_learning
from .library import (
    _recent_scan_sources,
    _scan_recent_source,
    do_remove_folder,
    handle_refresh_all,
    refresh_library_menu,
    remove_folder_clicked,
    remove_folder_clicked_via_pill,
)
from .selection import (
    open_selection_in_explorer,
    preview_selection,
    prompt_set_selection_pack,
    refresh_selection_menu,
    selected_records,
    selection_target,
    set_selection_category,
    set_selection_type,
)
from .session import load_staging_session

__all__ = [
    "_recent_scan_sources",
    "_scan_recent_source",
    "clear_history",
    "confirm_undo",
    "do_remove_folder",
    "handle_refresh_all",
    "load_staging_session",
    "open_selection_in_explorer",
    "preview_selection",
    "prompt_set_selection_pack",
    "refresh_build_menu",
    "refresh_history_menu",
    "refresh_library_menu",
    "refresh_selection_menu",
    "remove_folder_clicked",
    "remove_folder_clicked_via_pill",
    "reset_learning",
    "selected_records",
    "selection_target",
    "set_selection_category",
    "set_selection_type",
]
