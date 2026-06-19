"""Theme token registry and semantic color variants for the GUI."""

from __future__ import annotations

import pathlib
from dataclasses import dataclass
import json

SYSTEM_THEME_KEY = "system"
OCEAN_THEME_KEY = "ocean"
ASH_THEME_KEY = "ash"
SUNSET_THEME_KEY = "sunset"
PEARL_THEME_KEY = "pearl"
DEFAULT_THEME_KEY = "default"

CATEGORY_IDENTITY_MAP = {
    "Bass": "identity.1",
    "Kicks": "identity.2",
    "Snares": "identity.3",
    "Claps": "identity.3",
    "Hats Cymbals": "identity.4",
    "Melodics": "identity.4",
    "Full Drums": "identity.5",
    "Vocals": "identity.5",
    "Percussion": "identity.6",
    "FX": "identity.6",
    "Non-Audio Assets": "identity.neutral",
    "Uncategorized": "identity.neutral",
}


@dataclass(frozen=True)
class ThemeColors:
    name: str
    id: str
    is_dark: bool
    contrast_percent: int
    vibrancy_percent: int
    stack_direction: str
    bg_darker: str
    bg_dark: str
    bg_med: str
    bg_light: str
    bg_hover: str
    bg_accent: str
    bg_dropdown: str
    bg_list: str
    bg_scrollbar: str
    bg_scrollbar_handle: str
    primary: str
    primary_hover: str
    primary_light: str
    primary_bright: str
    danger: str
    danger_hover: str
    danger_light: str
    success: str
    warning: str
    text_main: str
    text_light: str
    text_header: str
    text_gray: str
    text_muted: str
    text_dim: str
    text_dimmer: str
    text_row_idx: str
    text_inactive: str
    text_inverse: str
    border: str
    border_light: str
    border_input: str
    border_accent: str
    selection: str
    translucent_bg: str
    translucent_border: str
    table_hover: str
    table_select: str
    search_highlight: str
    hands_off_bg: str
    tree_oneshot: str
    tree_loop: str
    tree_midi: str
    tree_root: str
    tree_category: str
    tree_pack: str
    surface_card: str
    surface_subtle: str
    surface_raised: str
    surface_overlay: str
    action_secondary: str
    action_cancel: str
    action_selected: str
    status_info: str
    status_success_soft: str
    status_warning_soft: str
    status_danger_soft: str
    status_info_soft: str
    identity: tuple[str, str, str, str, str, str]
    identity_soft: tuple[str, str, str, str, str, str]
    identity_neutral: str
    identity_soft_neutral: str
    grouping_table: tuple[str, str, str, str, str, str]
    grouping_tree: tuple[str, str, str, str, str, str]
    grouping_list: tuple[str, str, str, str, str, str]


def _theme(
    *,
    theme_id: str,
    name: str,
    is_dark: bool,
    contrast_percent: int,
    vibrancy_percent: int,
    stack_direction: str,
    app_bg: str,
    app_window: str,
    app_panel: str,
    app_panel_raised: str,
    app_menu: str,
    text_primary: str,
    text_secondary: str,
    text_muted: str,
    text_disabled: str,
    text_inverse: str,
    border_subtle: str,
    border_medium: str,
    border_strong: str,
    accent_primary: str,
    accent_primary_hover: str,
    accent_primary_soft: str,
    accent_primary_text: str,
    status_success: str,
    status_warning: str,
    status_danger: str,
    control_bg_hover: str,
    table_bg: str,
    table_row_hover: str,
    table_row_selected: str,
    surface_card: str,
    surface_subtle: str,
    surface_raised: str,
    surface_overlay: str,
    action_secondary: str,
    action_cancel: str,
    action_selected: str,
    status_info: str,
    status_success_soft: str,
    status_warning_soft: str,
    status_danger_soft: str,
    status_info_soft: str,
    identity: tuple[str, str, str, str, str, str],
    identity_soft: tuple[str, str, str, str, str, str],
    identity_neutral: str,
    identity_soft_neutral: str,
    grouping_table: tuple[str, str, str, str, str, str],
    grouping_tree: tuple[str, str, str, str, str, str],
    grouping_list: tuple[str, str, str, str, str, str],
) -> ThemeColors:
    return ThemeColors(
        name=name,
        id=theme_id,
        is_dark=is_dark,
        contrast_percent=contrast_percent,
        vibrancy_percent=vibrancy_percent,
        stack_direction=stack_direction,
        bg_darker=app_bg,
        bg_dark=app_window,
        bg_med=app_panel,
        bg_light=app_panel_raised,
        bg_hover=control_bg_hover,
        bg_accent=accent_primary_soft,
        bg_dropdown=app_menu,
        bg_list=table_bg,
        bg_scrollbar=app_panel,
        bg_scrollbar_handle=border_strong,
        primary=accent_primary,
        primary_hover=accent_primary_hover,
        primary_light=accent_primary_text,
        primary_bright=accent_primary_text,
        danger=status_danger,
        danger_hover=status_danger,
        danger_light=status_danger,
        success=status_success,
        warning=status_warning,
        text_main=text_primary,
        text_light=text_primary,
        text_header=text_secondary,
        text_gray=text_secondary,
        text_muted=text_secondary,
        text_dim=text_muted,
        text_dimmer=text_disabled,
        text_row_idx=text_muted,
        text_inactive=text_disabled,
        text_inverse=text_inverse,
        border=border_subtle,
        border_light=border_medium,
        border_input=border_medium,
        border_accent=border_strong,
        selection=table_row_selected,
        translucent_bg=accent_primary_soft,
        translucent_border=border_medium,
        table_hover=table_row_hover,
        table_select=table_row_selected,
        search_highlight=accent_primary_soft,
        hands_off_bg=accent_primary_soft,
        tree_oneshot=status_danger,
        tree_loop=status_success,
        tree_midi=status_warning,
        tree_root=accent_primary,
        tree_category=accent_primary_text,
        tree_pack=accent_primary,
        surface_card=surface_card,
        surface_subtle=surface_subtle,
        surface_raised=surface_raised,
        surface_overlay=surface_overlay,
        action_secondary=action_secondary,
        action_cancel=action_cancel,
        action_selected=action_selected,
        status_info=status_info,
        status_success_soft=status_success_soft,
        status_warning_soft=status_warning_soft,
        status_danger_soft=status_danger_soft,
        status_info_soft=status_info_soft,
        identity=identity,
        identity_soft=identity_soft,
        identity_neutral=identity_neutral,
        identity_soft_neutral=identity_soft_neutral,
        grouping_table=grouping_table,
        grouping_tree=grouping_tree,
        grouping_list=grouping_list,
    )

def _load_json(theme_key: str):
    file_path = str(pathlib.Path(__file__).resolve().parent.joinpath('themes', f'{theme_key}.json'))

    with open(file_path) as f:
        return json.load(f)

OCEAN = _theme(
    theme_id=OCEAN_THEME_KEY,
    **(_load_json(OCEAN_THEME_KEY))
)

PEARL = _theme(
    theme_id=PEARL_THEME_KEY,
    **(_load_json(PEARL_THEME_KEY))
)

ASH = _theme(
    theme_id=ASH_THEME_KEY,
    **(_load_json(ASH_THEME_KEY))
    )

SUNSET = _theme(
    theme_id=SUNSET_THEME_KEY,
    **(_load_json(SUNSET_THEME_KEY))
    )
DEFAULT = _theme(
    theme_id=DEFAULT_THEME_KEY,
    **(_load_json(DEFAULT_THEME_KEY))
)


THEMES = {
    DEFAULT_THEME_KEY: DEFAULT,
    OCEAN_THEME_KEY: OCEAN,
    ASH_THEME_KEY: ASH,
    SUNSET_THEME_KEY: SUNSET,
    PEARL_THEME_KEY: PEARL,
}
