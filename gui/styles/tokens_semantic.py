"""Theme token registry and semantic color variants for the GUI."""

from __future__ import annotations

from dataclasses import dataclass


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


OCEAN = _theme(
    theme_id=OCEAN_THEME_KEY,
    name="Ocean",
    is_dark=False,
    contrast_percent=63,
    vibrancy_percent=72,
    stack_direction="lighter",
    app_bg="#cfe3ef",
    app_window="#ebf4f8",
    app_panel="#f4f8fa",
    app_panel_raised="#ffffff",
    app_menu="#ffffff",
    text_primary="#122033",
    text_secondary="#41556f",
    text_muted="#708196",
    text_disabled="#a8b3c2",
    text_inverse="#ffffff",
    border_subtle="rgba(58, 120, 168, 0.18)",
    border_medium="rgba(58, 120, 168, 0.28)",
    border_strong="rgba(42, 100, 150, 0.34)",
    accent_primary="#2488a8",
    accent_primary_hover="#156f8c",
    accent_primary_soft="#ddf4f7",
    accent_primary_text="#0d6987",
    status_success="#20b15a",
    status_warning="#f59e0b",
    status_danger="#ef4444",
    control_bg_hover="#eaf6fb",
    table_bg="#f6fbff",
    table_row_hover="#eef8fe",
    table_row_selected="#d8edf5",
    surface_card="#f6fbff",
    surface_subtle="#f3faff",
    surface_raised="#fbfdff",
    surface_overlay="rgba(14, 37, 58, 0.34)",
    action_secondary="#eef8fe",
    action_cancel="#f3faff",
    action_selected="#eaf6fb",
    status_info="#2488a8",
    status_success_soft="#dcf8e8",
    status_warning_soft="#fff1d7",
    status_danger_soft="#ffe5e7",
    status_info_soft="#edf7fb",
    identity=("#e45d70", "#f39d68", "#d94fa4", "#2fb7c0", "#37a0dc", "#e7b842"),
    identity_soft=("#fde8ec", "#fff0e4", "#f9e2f1", "#ddf5f6", "#e0f1fb", "#fff6d8"),
    identity_neutral="#78869a",
    identity_soft_neutral="#e8f0f6",
    grouping_table=("#f4fbff", "#eaf7fb", "#fff5eb", "#fdf0f6", "#ecf8f2", "#fff8d9"),
    grouping_tree=("#e6f5fb", "#f2fbff", "#fff3e7", "#faedf5", "#e6f6ef", "#fff4cc"),
    grouping_list=("#edf8fd", "#f4fcff", "#fff7ed", "#fdf1f7", "#edf9f3", "#fff8dc"),
)

PEARL = _theme(
    theme_id=PEARL_THEME_KEY,
    name="Pearl",
    is_dark=False,
    contrast_percent=54,
    vibrancy_percent=46,
    stack_direction="lighter",
    app_bg="#f6f0e7",
    app_window="#fffdf8",
    app_panel="#fffaf2",
    app_panel_raised="#ffffff",
    app_menu="#ffffff",
    text_primary="#111827",
    text_secondary="#3f4b5c",
    text_muted="#738093",
    text_disabled="#a5afbc",
    text_inverse="#ffffff",
    border_subtle="rgba(60, 80, 110, 0.11)",
    border_medium="rgba(60, 80, 110, 0.18)",
    border_strong="rgba(60, 80, 110, 0.28)",
    accent_primary="#508f8b",
    accent_primary_hover="#3d726f",
    accent_primary_soft="#e7f3f0",
    accent_primary_text="#2c5957",
    status_success="#16a34a",
    status_warning="#ea8a13",
    status_danger="#d9534f",
    control_bg_hover="#f6f8fb",
    table_bg="#ffffff",
    table_row_hover="#fff6ea",
    table_row_selected="#dcece8",
    surface_card="#ffffff",
    surface_subtle="#fbf4ea",
    surface_raised="#ffffff",
    surface_overlay="rgba(49, 38, 32, 0.28)",
    action_secondary="#f5ede2",
    action_cancel="#f1ece5",
    action_selected="#e8f2ee",
    status_info="#5f9e9a",
    status_success_soft="#e7f4e8",
    status_warning_soft="#fff3dc",
    status_danger_soft="#fae8e8",
    status_info_soft="#e7f3f0",
    identity=("#c97084", "#d7a45c", "#9b80bd", "#86a779", "#5f9e9a", "#d8ba66"),
    identity_soft=("#f7e5e9", "#f8edd9", "#eee8f5", "#edf3e8", "#e7f3f0", "#f8f0d7"),
    identity_neutral="#81766d",
    identity_soft_neutral="#f1ece5",
    grouping_table=("#fffdf8", "#fbf6ee", "#f9f2e7", "#f4eff3", "#eff4ec", "#f9f1de"),
    grouping_tree=("#fff9ef", "#f8f2ea", "#fbf4e7", "#f3edf5", "#edf5ee", "#f7edda"),
    grouping_list=("#fffaf2", "#fcf5eb", "#fbf1e0", "#f6eff4", "#f0f5ed", "#f8f1df"),
)

ASH = _theme(
    theme_id=ASH_THEME_KEY,
    name="Ash",
    is_dark=True,
    contrast_percent=58,
    vibrancy_percent=55,
    stack_direction="lighter",
    app_bg="#1d2022",
    app_window="#25282b",
    app_panel="#2b2f33",
    app_panel_raised="#34393d",
    app_menu="#2d3033",
    text_primary="#eef5ff",
    text_secondary="#c2cedc",
    text_muted="#8997a8",
    text_disabled="#5f6c7a",
    text_inverse="#111827",
    border_subtle="rgba(237, 220, 198, 0.10)",
    border_medium="rgba(237, 220, 198, 0.17)",
    border_strong="rgba(237, 220, 198, 0.27)",
    accent_primary="#d99045",
    accent_primary_hover="#edab62",
    accent_primary_soft="rgba(217, 144, 69, 0.16)",
    accent_primary_text="#f1bf80",
    status_success="#4ade80",
    status_warning="#f59e0b",
    status_danger="#f87171",
    control_bg_hover="#383d40",
    table_bg="#25282b",
    table_row_hover="#31363a",
    table_row_selected="#544334",
    surface_card="#2f3438",
    surface_subtle="#272b2e",
    surface_raised="#363c40",
    surface_overlay="rgba(5, 7, 8, 0.52)",
    action_secondary="#353a3e",
    action_cancel="#303438",
    action_selected="rgba(217, 144, 69, 0.30)",
    status_info="#5aa7a1",
    status_success_soft="rgba(74, 222, 128, 0.14)",
    status_warning_soft="rgba(245, 158, 11, 0.15)",
    status_danger_soft="rgba(248, 113, 113, 0.14)",
    status_info_soft="rgba(90, 167, 161, 0.15)",
    identity=("#df6d57", "#d99045", "#d2ae45", "#8e6f92", "#5aa7a1", "#7897b2"),
    identity_soft=("rgba(223,109,87,0.18)", "rgba(217,144,69,0.18)", "rgba(210,174,69,0.18)", "rgba(142,111,146,0.18)", "rgba(90,167,161,0.17)", "rgba(120,151,178,0.17)"),
    identity_neutral="#a0a6a9",
    identity_soft_neutral="rgba(210, 216, 218, 0.11)",
    grouping_table=("rgba(223,109,87,0.07)", "rgba(217,144,69,0.07)", "rgba(210,174,69,0.07)", "rgba(142,111,146,0.08)", "rgba(90,167,161,0.07)", "rgba(120,151,178,0.07)"),
    grouping_tree=("rgba(223,109,87,0.10)", "rgba(217,144,69,0.10)", "rgba(210,174,69,0.10)", "rgba(142,111,146,0.11)", "rgba(90,167,161,0.10)", "rgba(120,151,178,0.10)"),
    grouping_list=("rgba(223,109,87,0.08)", "rgba(217,144,69,0.08)", "rgba(210,174,69,0.08)", "rgba(142,111,146,0.09)", "rgba(90,167,161,0.08)", "rgba(120,151,178,0.08)"),
)

SUNSET = _theme(
    theme_id=SUNSET_THEME_KEY,
    name="Sunset",
    is_dark=True,
    contrast_percent=82,
    vibrancy_percent=68,
    stack_direction="lighter",
    app_bg="#06070d",
    app_window="#0b0e17",
    app_panel="#111522",
    app_panel_raised="#181d2d",
    app_menu="#121521",
    text_primary="#f2f7ff",
    text_secondary="#bccbda",
    text_muted="#7e8fa3",
    text_disabled="#4f5f70",
    text_inverse="#ffffff",
    border_subtle="rgba(255, 193, 122, 0.11)",
    border_medium="rgba(255, 193, 122, 0.18)",
    border_strong="rgba(255, 193, 122, 0.30)",
    accent_primary="#f09a3e",
    accent_primary_hover="#ffb35c",
    accent_primary_soft="rgba(240, 154, 62, 0.18)",
    accent_primary_text="#ffc076",
    status_success="#47d16c",
    status_warning="#f5a524",
    status_danger="#ff5c5c",
    control_bg_hover="#1a2031",
    table_bg="#0b0e17",
    table_row_hover="#151a29",
    table_row_selected="#3a2b29",
    surface_card="#151a29",
    surface_subtle="#0f1320",
    surface_raised="#1b2234",
    surface_overlay="rgba(0, 0, 0, 0.62)",
    action_secondary="#192033",
    action_cancel="#141a29",
    action_selected="rgba(240, 154, 62, 0.34)",
    status_info="#9a8cff",
    status_success_soft="rgba(71, 209, 108, 0.15)",
    status_warning_soft="rgba(245, 165, 36, 0.16)",
    status_danger_soft="rgba(255, 92, 92, 0.15)",
    status_info_soft="rgba(154, 140, 255, 0.16)",
    identity=("#f26f53", "#f09a3e", "#f7c64a", "#e14a76", "#b05cff", "#806dff"),
    identity_soft=("rgba(242,111,83,0.20)", "rgba(240,154,62,0.20)", "rgba(247,198,74,0.18)", "rgba(225,74,118,0.19)", "rgba(176,92,255,0.18)", "rgba(128,109,255,0.18)"),
    identity_neutral="#9098ad",
    identity_soft_neutral="rgba(210, 216, 235, 0.10)",
    grouping_table=("rgba(242,111,83,0.08)", "rgba(240,154,62,0.08)", "rgba(247,198,74,0.07)", "rgba(225,74,118,0.08)", "rgba(176,92,255,0.08)", "rgba(128,109,255,0.08)"),
    grouping_tree=("rgba(242,111,83,0.11)", "rgba(240,154,62,0.11)", "rgba(247,198,74,0.10)", "rgba(225,74,118,0.11)", "rgba(176,92,255,0.10)", "rgba(128,109,255,0.10)"),
    grouping_list=("rgba(242,111,83,0.09)", "rgba(240,154,62,0.09)", "rgba(247,198,74,0.08)", "rgba(225,74,118,0.09)", "rgba(176,92,255,0.08)", "rgba(128,109,255,0.08)"),
)
DEFAULT = _theme(
    theme_id=DEFAULT_THEME_KEY,
    name="Default",
    is_dark=True,
    contrast_percent=80,
    vibrancy_percent=70,
    stack_direction="lighter",
    app_bg="#080a0f",
    app_window="#0e111a",
    app_panel="#131824",
    app_panel_raised="#1b2132",
    app_menu="#141824",
    text_primary="#f8fbfd",
    text_secondary="#94a3b8",
    text_muted="#64748b",
    text_disabled="#475569",
    text_inverse="#06121f",
    border_subtle="rgba(36, 240, 252, 0.10)",
    border_medium="rgba(36, 240, 252, 0.17)",
    border_strong="rgba(36, 240, 252, 0.28)",
    accent_primary="#246cfc",
    accent_primary_hover="#4080ff",
    accent_primary_soft="rgba(36, 108, 252, 0.16)",
    accent_primary_text="#24f0fc",
    status_success="#3cd17c",
    status_warning="#f5a524",
    status_danger="#ff5c5c",
    control_bg_hover="#1a2233",
    table_bg="#0e111a",
    table_row_hover="#161b29",
    table_row_selected="rgba(36, 108, 252, 0.28)",
    surface_card="#131824",
    surface_subtle="#0b0e17",
    surface_raised="#1b2132",
    surface_overlay="rgba(4, 6, 10, 0.65)",
    action_secondary="#182030",
    action_cancel="#131824",
    action_selected="rgba(36, 108, 252, 0.32)",
    status_info="#24f0fc",
    status_success_soft="rgba(60, 209, 124, 0.15)",
    status_warning_soft="rgba(245, 165, 36, 0.16)",
    status_danger_soft="rgba(255, 92, 92, 0.15)",
    status_info_soft="rgba(36, 240, 252, 0.16)",
    identity=("#9030fc", "#246cfc", "#24f0fc", "#a855f7", "#3b82f6", "#06b6d4"),
    identity_soft=("rgba(144,48,252,0.20)", "rgba(36,108,252,0.20)", "rgba(36,240,252,0.18)", "rgba(168,85,247,0.19)", "rgba(59,130,246,0.18)", "rgba(6,182,212,0.18)"),
    identity_neutral="#708090",
    identity_soft_neutral="rgba(112, 128, 144, 0.12)",
    grouping_table=("rgba(144,48,252,0.08)", "rgba(36,108,252,0.08)", "rgba(36,240,252,0.07)", "rgba(168,85,247,0.08)", "rgba(59,130,246,0.08)", "rgba(6,182,212,0.08)"),
    grouping_tree=("rgba(144,48,252,0.11)", "rgba(36,108,252,0.11)", "rgba(36,240,252,0.10)", "rgba(168,85,247,0.11)", "rgba(59,130,246,0.10)", "rgba(6,182,212,0.10)"),
    grouping_list=("rgba(144,48,252,0.09)", "rgba(36,108,252,0.09)", "rgba(36,240,252,0.08)", "rgba(168,85,247,0.09)", "rgba(59,130,246,0.08)", "rgba(6,182,212,0.08)"),
)


THEMES = {
    DEFAULT_THEME_KEY: DEFAULT,
    OCEAN_THEME_KEY: OCEAN,
    ASH_THEME_KEY: ASH,
    SUNSET_THEME_KEY: SUNSET,
    PEARL_THEME_KEY: PEARL,
}
