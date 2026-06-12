"""Theme-bound style helpers used by current GUI widgets."""

from gui.styles.tokens_semantic import ASH, ThemeColors
from PySide6.QtGui import QColor
from .style_helpers import apply_style
from .color_helpers import make_qcolor

class ColorPalette:
    # Backgrounds
    BG_DARKER = ASH.bg_darker
    BG_DARK = ASH.bg_dark
    BG_MED = ASH.bg_med
    BG_LIGHT = ASH.bg_light
    BG_HOVER = ASH.bg_hover
    BG_ACCENT = ASH.bg_accent
    BG_DROPDOWN = ASH.bg_dropdown
    BG_LIST = ASH.bg_list
    BG_SCROLLBAR = ASH.bg_scrollbar
    BG_SCROLLBAR_HANDLE = ASH.bg_scrollbar_handle
    
    # Primaries
    PRIMARY = ASH.primary
    PRIMARY_HOVER = ASH.primary_hover
    PRIMARY_LIGHT = ASH.primary_light
    PRIMARY_BRIGHT = ASH.primary_bright
    
    # Actions
    DANGER = ASH.danger
    DANGER_HOVER = ASH.danger_hover
    DANGER_LIGHT = ASH.danger_light
    SUCCESS = ASH.success
    WARNING = ASH.warning
    
    # Text
    TEXT_MAIN = ASH.text_main
    TEXT_LIGHT = ASH.text_light
    TEXT_INVERSE = ASH.text_inverse
    TEXT_HEADER = ASH.text_header
    TEXT_GRAY = ASH.text_gray
    TEXT_MUTED = ASH.text_muted
    TEXT_DIM = ASH.text_dim
    TEXT_DIMMER = ASH.text_dimmer
    TEXT_ROW_IDX = ASH.text_row_idx
    TEXT_INACTIVE = ASH.text_inactive
    
    # Borders
    BORDER = ASH.border
    BORDER_LIGHT = ASH.border_light
    BORDER_INPUT = ASH.border_input
    BORDER_ACCENT = ASH.border_accent
    
    # Others
    SELECTION = ASH.selection
    TRANSLUCENT_BG = ASH.translucent_bg
    TRANSLUCENT_BORDER = ASH.translucent_border
    TABLE_HOVER = ASH.table_hover
    TABLE_SELECT = ASH.table_select
    TABLE_MARGIN = 14
    SEARCH_HIGHLIGHT = ASH.search_highlight
    HANDS_OFF_BG = ASH.hands_off_bg
    TREE_ONESHOT = ASH.tree_oneshot
    TREE_LOOP = ASH.tree_loop
    TREE_MIDI = ASH.tree_midi
    TREE_ROOT = ASH.tree_root
    TREE_CATEGORY = ASH.tree_category
    TREE_PACK = ASH.tree_pack
    STATUS_INFO = ASH.status_info
    STATUS_SUCCESS_SOFT = ASH.status_success_soft
    STATUS_WARNING_SOFT = ASH.status_warning_soft
    STATUS_DANGER_SOFT = ASH.status_danger_soft
    STATUS_INFO_SOFT = ASH.status_info_soft
    SURFACE_CARD = ASH.surface_card
    SURFACE_SUBTLE = ASH.surface_subtle
    SURFACE_RAISED = ASH.surface_raised
    ACTION_SECONDARY = ASH.action_secondary
    ACTION_CANCEL = ASH.action_cancel
    ACTION_SELECTED = ASH.action_selected
    IDENTITY = ASH.identity
    IDENTITY_SOFT = ASH.identity_soft
    IDENTITY_NEUTRAL = ASH.identity_neutral
    IDENTITY_SOFT_NEUTRAL = ASH.identity_soft_neutral
    GROUPING_TABLE = ASH.grouping_table
    GROUPING_TREE = ASH.grouping_tree
    GROUPING_LIST = ASH.grouping_list
    TRANSPARENT = "transparent"


CURRENT_ZOOM_PERCENT = 100


def sync_color_palette(colors: ThemeColors) -> None:
    ColorPalette.BG_DARKER = colors.bg_darker
    ColorPalette.BG_DARK = colors.bg_dark
    ColorPalette.BG_MED = colors.bg_med
    ColorPalette.BG_LIGHT = colors.bg_light
    ColorPalette.BG_HOVER = colors.bg_hover
    ColorPalette.BG_ACCENT = colors.bg_accent
    ColorPalette.BG_DROPDOWN = colors.bg_dropdown
    ColorPalette.BG_LIST = colors.bg_list
    ColorPalette.BG_SCROLLBAR = colors.bg_scrollbar
    ColorPalette.BG_SCROLLBAR_HANDLE = colors.bg_scrollbar_handle
    ColorPalette.PRIMARY = colors.primary
    ColorPalette.PRIMARY_HOVER = colors.primary_hover
    ColorPalette.PRIMARY_LIGHT = colors.primary_light
    ColorPalette.PRIMARY_BRIGHT = colors.primary_bright
    ColorPalette.DANGER = colors.danger
    ColorPalette.DANGER_HOVER = colors.danger_hover
    ColorPalette.DANGER_LIGHT = colors.danger_light
    ColorPalette.SUCCESS = colors.success
    ColorPalette.WARNING = colors.warning
    ColorPalette.TEXT_MAIN = colors.text_main
    ColorPalette.TEXT_LIGHT = colors.text_light
    ColorPalette.TEXT_INVERSE = colors.text_inverse
    ColorPalette.TEXT_HEADER = colors.text_header
    ColorPalette.TEXT_GRAY = colors.text_gray
    ColorPalette.TEXT_MUTED = colors.text_muted
    ColorPalette.TEXT_DIM = colors.text_dim
    ColorPalette.TEXT_DIMMER = colors.text_dimmer
    ColorPalette.TEXT_ROW_IDX = colors.text_row_idx
    ColorPalette.TEXT_INACTIVE = colors.text_inactive
    ColorPalette.BORDER = colors.border
    ColorPalette.BORDER_LIGHT = colors.border_light
    ColorPalette.BORDER_INPUT = colors.border_input
    ColorPalette.BORDER_ACCENT = colors.border_accent
    ColorPalette.SELECTION = colors.selection
    ColorPalette.TRANSLUCENT_BG = colors.translucent_bg
    ColorPalette.TRANSLUCENT_BORDER = colors.translucent_border
    ColorPalette.TABLE_HOVER = colors.table_hover
    ColorPalette.TABLE_SELECT = colors.table_select
    ColorPalette.SEARCH_HIGHLIGHT = colors.search_highlight
    ColorPalette.HANDS_OFF_BG = colors.hands_off_bg
    ColorPalette.TREE_ONESHOT = colors.tree_oneshot
    ColorPalette.TREE_LOOP = colors.tree_loop
    ColorPalette.TREE_MIDI = colors.tree_midi
    ColorPalette.TREE_ROOT = colors.tree_root
    ColorPalette.TREE_CATEGORY = colors.tree_category
    ColorPalette.TREE_PACK = colors.tree_pack
    ColorPalette.STATUS_INFO = colors.status_info
    ColorPalette.STATUS_SUCCESS_SOFT = colors.status_success_soft
    ColorPalette.STATUS_WARNING_SOFT = colors.status_warning_soft
    ColorPalette.STATUS_DANGER_SOFT = colors.status_danger_soft
    ColorPalette.STATUS_INFO_SOFT = colors.status_info_soft
    ColorPalette.SURFACE_CARD = colors.surface_card
    ColorPalette.SURFACE_SUBTLE = colors.surface_subtle
    ColorPalette.SURFACE_RAISED = colors.surface_raised
    ColorPalette.ACTION_SECONDARY = colors.action_secondary
    ColorPalette.ACTION_CANCEL = colors.action_cancel
    ColorPalette.ACTION_SELECTED = colors.action_selected
    ColorPalette.IDENTITY = colors.identity
    ColorPalette.IDENTITY_SOFT = colors.identity_soft
    ColorPalette.IDENTITY_NEUTRAL = colors.identity_neutral
    ColorPalette.IDENTITY_SOFT_NEUTRAL = colors.identity_soft_neutral
    ColorPalette.GROUPING_TABLE = colors.grouping_table
    ColorPalette.GROUPING_TREE = colors.grouping_tree
    ColorPalette.GROUPING_LIST = colors.grouping_list
    
    from gui.models.library_tree import clear_tree_color_caches
    clear_tree_color_caches()


def identity_lane_color(index: int, *, soft: bool = True) -> str:
    palette = ColorPalette.IDENTITY_SOFT if soft else ColorPalette.IDENTITY
    if not palette:
        return ColorPalette.IDENTITY_SOFT_NEUTRAL if soft else ColorPalette.IDENTITY_NEUTRAL
    return palette[index % len(palette)]


def grouping_lane_color(index: int, view: str = "table") -> str:
    palette = {
        "tree": ColorPalette.GROUPING_TREE,
        "list": ColorPalette.GROUPING_LIST,
    }.get(view, ColorPalette.GROUPING_TABLE)
    if not palette:
        return ColorPalette.TABLE_HOVER
    return palette[index % len(palette)]


def set_zoom_percent(zoom_percent: int) -> None:
    global CURRENT_ZOOM_PERCENT
    CURRENT_ZOOM_PERCENT = max(75, zoom_percent)


def zoom_scale() -> float:
    return CURRENT_ZOOM_PERCENT / 100.0


def scaled_px(px: float) -> int:
    return max(1, round(px * zoom_scale()))


def type_toggle_box_style() -> str:
    return (
        f"QFrame#TypeToggleBox {{ background: {ColorPalette.BG_DARK}; border: none; "
        f"border-radius: {scaled_px(12)}px; }}"
    )


def type_toggle_button_style(font_size: int, *, bold: bool = False) -> str:
    weight = "font-weight: bold;" if bold else ""
    width = scaled_px(44)
    height = scaled_px(36)
    radius = scaled_px(7)
    pad_h = scaled_px(10)
    return (
        f"QPushButton {{ {weight} font-size: {scaled_px(font_size)}px; color: {ColorPalette.TEXT_LIGHT}; "
        f"background: {ColorPalette.BG_DARK}; padding: 0 {pad_h}px; min-width: {width}px; "
        f"max-width: {width}px; min-height: {height}px; max-height: {height}px; border: none; "
        f"border-radius: {radius}px; }}"
        f"QPushButton:hover {{ background: {ColorPalette.BG_HOVER}; }}"
        f"QPushButton:checked {{ background: {ColorPalette.PRIMARY}; color: {ColorPalette.TEXT_INVERSE}; }}"
    )


def section_label_style() -> str:
    return (
        f"color: {ColorPalette.TEXT_DIMMER}; font-weight: bold; "
        f"font-size: {scaled_px(11)}px; letter-spacing: 1px;"
    )


def section_toggle_style() -> str:
    return (
        f"QPushButton {{ text-align: left; color: {ColorPalette.TEXT_DIMMER}; font-weight: bold; "
        f"font-size: {scaled_px(11)}px; letter-spacing: 1px; border: none; background: {ColorPalette.TRANSPARENT}; "
        f"padding: {scaled_px(10)}px 0; }}"
        f"QPushButton[iconBelow=\"true\"] {{ text-align: center; padding: {scaled_px(4)}px 0 {scaled_px(6)}px 0; "
        f"font-size: {scaled_px(10)}px; min-height: {scaled_px(30)}px; }}"
        f"QPushButton:hover {{ color: {ColorPalette.TEXT_DIM}; }}"
    )


def section_scroll_style() -> str:
    return (
        f"QScrollArea {{ background: {ColorPalette.TRANSPARENT}; border: none; }}"
        f"{scrollbar_style()}"
    )


def sidebar_scroll_style(*, left: bool = False) -> str:
    return (
        f"QScrollArea {{ background: transparent; border: none; border-radius: {scaled_px(8)}px; }}"
        f"QScrollArea::viewport {{ background: transparent; border: none; border-radius: {scaled_px(8)}px; }}"
        f"QScrollArea > QWidget > QWidget {{ background: transparent; border-radius: {scaled_px(8)}px; }}"
        f"{scrollbar_style(left=left)}"
    )


def scrollbar_style(*, left: bool = False) -> str:
    left_rules = (
        "QScrollBar:vertical { subcontrol-position: left; }"
        if left
        else ""
    )
    vertical_radius = (
        f"border-top-left-radius: {scaled_px(4)}px; border-bottom-left-radius: {scaled_px(4)}px; "
        "border-top-right-radius: 0; border-bottom-right-radius: 0;"
        if left
        else (
            "border-top-left-radius: 0; border-bottom-left-radius: 0; "
            f"border-top-right-radius: {scaled_px(4)}px; border-bottom-right-radius: {scaled_px(4)}px;"
        )
    )
    return (
        f"QScrollBar:vertical {{ background: transparent; width: {scaled_px(8)}px; margin: 0; }}"
        f"QScrollBar::handle:vertical {{ background: {ColorPalette.BG_SCROLLBAR_HANDLE}; min-height: {scaled_px(28)}px; "
        f"{vertical_radius} }}"
        f"QScrollBar::handle:vertical:hover {{ background: {ColorPalette.BORDER_ACCENT}; }}"
        "QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }"
        "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }"
        f"QScrollBar:horizontal {{ background: transparent; height: {scaled_px(8)}px; margin: 0; }}"
        f"QScrollBar::handle:horizontal {{ background: {ColorPalette.BG_SCROLLBAR_HANDLE}; min-width: {scaled_px(28)}px; "
        f"border-top-left-radius: 0; border-top-right-radius: 0; "
        f"border-bottom-left-radius: {scaled_px(4)}px; border-bottom-right-radius: {scaled_px(4)}px; }}"
        f"QScrollBar::handle:horizontal:hover {{ background: {ColorPalette.BORDER_ACCENT}; }}"
        "QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: transparent; }"
        "QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }"
        f"{left_rules}"
    )


def sidebar_item_label_style() -> str:
    return (
        f"color: {ColorPalette.TEXT_LIGHT}; font-size: {scaled_px(13)}px; "
        "border: none; background: transparent;"
    )


def sidebar_remove_button_style() -> str:
    size = scaled_px(20)
    return (
        f"QPushButton {{ color: {ColorPalette.TEXT_DIMMER}; font-size: {scaled_px(16)}px; background: transparent; "
        f"padding: 0px; margin: 0px; border: none; min-width: {size}px; max-width: {size}px; "
        f"min-height: {size}px; max-height: {size}px; text-align: center; }} "
        f"QPushButton:hover {{ color: {ColorPalette.DANGER_LIGHT}; }}"
    )


def sidebar_active_item_style() -> str:
    return (
        f"QFrame {{ background: {ColorPalette.SELECTION}; padding: 2px 4px; }} "
        f"QLabel {{ color: {ColorPalette.TEXT_MAIN}; }}"
    )


def sidebar_base_style() -> str:
    return (
        f"QFrame#LibrarySidebar {{ background: {ColorPalette.BG_LIST}; border: none; "
        f"border-radius: {scaled_px(8)}px; }}"
    )


def sidebar_header_style() -> str:
    return (
        f"background: {ColorPalette.BG_HOVER}; border: none; "
        f"border-radius: 0px;"
    )


def sidebar_title_style() -> str:
    return (
        f"color: {ColorPalette.TEXT_HEADER}; font-weight: bold; "
        f"font-size: {scaled_px(12)}px; letter-spacing: 0px; "
        f"padding: 0 {scaled_px(12)}px;"
    )


def sidebar_add_button_style() -> str:
    return (
        f"QPushButton {{ background: {ColorPalette.BG_LIST}; color: {ColorPalette.TEXT_MUTED}; border: none; "
        f"border-radius: 0px; font-size: {scaled_px(18)}px; font-weight: bold; }}"
        f"QPushButton:hover {{ background: {ColorPalette.BG_HOVER}; color: {ColorPalette.TEXT_LIGHT}; }}"
    )


def menu_style() -> str:
    return (
        f"QMenu {{ background-color: {ColorPalette.BG_DROPDOWN}; color: {ColorPalette.TEXT_LIGHT}; "
        f"border: none; padding: {scaled_px(6)}px; border-radius: {scaled_px(6)}px; }}"
        f"QMenu::item {{ background: transparent; color: {ColorPalette.TEXT_LIGHT}; "
        f"padding: {scaled_px(7)}px {scaled_px(38)}px {scaled_px(7)}px {scaled_px(12)}px; "
        f"border-radius: {scaled_px(4)}px; }}"
        f"QMenu::item:selected {{ background: {ColorPalette.PRIMARY}; color: {ColorPalette.TEXT_INVERSE}; }}"
        f"QMenu::separator {{ height: 1px; background: {ColorPalette.BG_LIGHT}; "
        f"margin: {scaled_px(4)}px {scaled_px(8)}px; }}"
    )


def button_style(
    role: str = "secondary",
    *,
    size: str = "normal",
    text_align: str = "center",
    min_width: int | None = None,
) -> str:
    heights = {
        "compact": 28,
        "normal": 32,
        "toolbar": 36,
        "cell": 26,
    }
    pad_h = {
        "compact": 10,
        "normal": 14,
        "toolbar": 14,
        "cell": 10,
    }
    height = scaled_px(heights.get(size, heights["normal"]))
    padding = scaled_px(pad_h.get(size, pad_h["normal"]))
    radius = scaled_px(4)
    if role == "primary":
        bg, hover, fg = ColorPalette.PRIMARY, ColorPalette.PRIMARY_HOVER, ColorPalette.TEXT_INVERSE
        disabled_bg = ColorPalette.BG_HOVER
    elif role == "danger":
        bg, hover, fg = ColorPalette.DANGER, ColorPalette.DANGER_HOVER, ColorPalette.TEXT_INVERSE
        disabled_bg = ColorPalette.BG_HOVER
    elif role == "warning":
        bg, hover, fg = ColorPalette.WARNING, ColorPalette.PRIMARY_HOVER, ColorPalette.TEXT_INVERSE
        disabled_bg = ColorPalette.BG_HOVER
    elif role == "ghost":
        bg, hover, fg = "transparent", ColorPalette.TABLE_HOVER, ColorPalette.TEXT_LIGHT
        disabled_bg = "transparent"
    else:
        bg, hover, fg = ColorPalette.ACTION_SECONDARY, ColorPalette.BG_HOVER, ColorPalette.TEXT_MAIN
        disabled_bg = ColorPalette.BG_HOVER
    min_width_rule = f"min-width: {scaled_px(min_width)}px;" if min_width is not None else ""
    return (
        f"QPushButton {{ background: {bg}; color: {fg}; border: none; border-radius: {radius}px; "
        f"padding: 0 {padding}px; min-height: {height}px; {min_width_rule} "
        f"text-align: {text_align}; font-weight: 700; }}"
        f"QPushButton:hover {{ background: {hover}; }}"
        f"QPushButton:disabled {{ background: {disabled_bg}; color: {ColorPalette.TEXT_DIM}; }}"
        f"QPushButton:checked {{ background: {ColorPalette.PRIMARY}; color: {ColorPalette.TEXT_INVERSE}; }}"
        f"QPushButton:checked:hover {{ background: {ColorPalette.PRIMARY_HOVER}; }}"
    )


def input_control_style(selector: str = "QLineEdit") -> str:
    return (
        f"{selector} {{ background: {ColorPalette.BG_LIGHT}; color: {ColorPalette.TEXT_MAIN}; "
        f"selection-background-color: {ColorPalette.PRIMARY}; selection-color: {ColorPalette.TEXT_INVERSE}; "
        f"border: none; border-radius: {scaled_px(4)}px; padding: 0 {scaled_px(8)}px; "
        f"min-height: {scaled_px(36)}px; }}"
        f"{selector}:hover, {selector}:focus {{ background: {ColorPalette.BG_HOVER}; }}"
    )


def header_section_style(*, density: str = "data") -> str:
    pad_v = scaled_px(8 if density != "compact" else 6)
    pad_h = scaled_px(12)
    return (
        f"QHeaderView {{ background-color: {ColorPalette.BG_HOVER}; border: none; }}"
        f"QHeaderView::section {{ background-color: {ColorPalette.BG_HOVER}; color: {ColorPalette.TEXT_HEADER}; "
        f"padding: {pad_v}px {pad_h}px; border: none; font-weight: bold; }}"
        "QHeaderView::up-arrow, QHeaderView::down-arrow { width: 0px; height: 0px; }"
        f"QTableCornerButton::section {{ background-color: {ColorPalette.BG_HOVER}; border: none; }}"
    )


def data_table_style(selector: str = "QTableWidget") -> str:
    grid = "rgba(255, 255, 255, 0.025)" if make_qcolor(ColorPalette.BG_LIST).lightness() < 120 else "rgba(42, 100, 150, 0.035)"
    return (
        f"{selector} {{ background: {ColorPalette.BG_LIST}; color: {ColorPalette.TEXT_LIGHT}; "
        f"border: none; border-radius: {scaled_px(8)}px; alternate-background-color: {ColorPalette.BG_LIST}; "
        f"gridline-color: {grid}; outline: none; }}"
        f"{selector}::item {{ padding: {scaled_px(7)}px {scaled_px(12)}px; border: none; outline: none; }}"
        f"{selector}::item:hover {{ background: {ColorPalette.TABLE_HOVER}; }}"
        f"{selector}::item:selected:active, {selector}::item:selected:!active {{ "
        f"background: {ColorPalette.TABLE_SELECT}; color: {ColorPalette.TEXT_MAIN}; }}"
        f"{selector}::indicator {{ width: {scaled_px(14)}px; height: {scaled_px(14)}px; "
        f"background: {ColorPalette.BG_LIGHT}; border: 1px solid {ColorPalette.BORDER_ACCENT}; "
        f"border-radius: {scaled_px(3)}px; }}"
        f"{selector}::indicator:hover {{ background: {ColorPalette.BG_HOVER}; border-color: {ColorPalette.PRIMARY}; }}"
        f"{selector}::indicator:checked {{ background: {ColorPalette.PRIMARY}; border-color: {ColorPalette.PRIMARY}; }}"
        f"{header_section_style()}"
    )


def vertical_header_style() -> str:
    return f"""
    QHeaderView::section {{
        color: {ColorPalette.TEXT_ROW_IDX};
        padding: 1px 3px;
        border: none;
        font-family: monospace;
        font-size: {scaled_px(9)}px;
        font-weight: normal;
    }}
"""


def carousel_frame_style() -> str:
    return f"QFrame {{ background: {ColorPalette.BG_LIST}; border-radius: 4px; border: none; }}"


def transparent_panel_style() -> str:
    return f"QFrame {{ background: {ColorPalette.TRANSPARENT}; border-radius: {scaled_px(6)}px; padding: 1px; }}"


def preview_bar_style() -> str:
    return f"QFrame {{ background: {ColorPalette.BG_LIST}; border: none; }}"


def table_separator_color() -> str:
    line = make_qcolor(ColorPalette.BORDER_LIGHT)
    line.setAlpha(10 if make_qcolor(ColorPalette.BG_LIST).lightness() < 120 else 14)
    return line.name(QColor.HexArgb)


def footer_base_style() -> str:
    return f"QFrame#ModernFooter {{ background: {ColorPalette.BG_LIST}; border-top: 1px solid {table_separator_color()}; }}"


def footer_draft_label_style() -> str:
    return f"color: {ColorPalette.PRIMARY_BRIGHT}; font-weight: bold; font-size: {scaled_px(11)}px;"


def footer_cta_button_style(tone: str = "primary") -> str:
    role = tone if tone in {"primary", "secondary", "danger"} else "primary"
    return button_style(role, size="compact")


def vibe_anchor_bar_style() -> str:
    return f"QFrame {{ background: {ColorPalette.BG_DARK}; border-top: 1px solid {ColorPalette.BORDER}; }}"


def vibe_anchor_label_style() -> str:
    return (
        f"QLabel {{ color: {ColorPalette.TEXT_DIMMER}; font-size: {scaled_px(8)}px; "
        "font-weight: bold; letter-spacing: 0.5px; border: none; background: transparent; }"
    )


def dock_view_style() -> str:
    return (
        f"QWidget#DockView {{ background: {ColorPalette.BG_DARK}; color: {ColorPalette.TEXT_LIGHT}; "
        f"font-size: {scaled_px(14)}px; }}"
        f"QScrollArea#DockScrollArea {{ background: {ColorPalette.BG_DARK}; border: none; }}"
        f"QScrollArea#DockScrollArea::viewport {{ background: {ColorPalette.BG_DARK}; border: none; }}"
        f"QWidget#DockScrollContent {{ background: {ColorPalette.BG_DARK}; color: {ColorPalette.TEXT_LIGHT}; }}"
        f"QPushButton {{ padding: 4px 8px; min-height: {scaled_px(28)}px; }}"
        f"{scrollbar_style()}"
    )


def dock_save_search_button_style() -> str:
    return button_style("primary", size="toolbar")


def dock_options_button_style() -> str:
    return (
        f"QPushButton {{ text-align: center; color: {ColorPalette.TEXT_DIM}; font-weight: bold; "
        f"font-size: {scaled_px(10)}px; letter-spacing: 1px; border-top: 1px solid {ColorPalette.BORDER}; "
        f"background: {ColorPalette.TRANSPARENT}; padding: {scaled_px(8)}px 0; }}"
        f"QPushButton:hover {{ color: {ColorPalette.TEXT_MUTED}; background: {ColorPalette.BG_ACCENT}; }}"
    )


def combo_style() -> str:
    return (
        f"QComboBox {{ background: {ColorPalette.BG_LIGHT}; color: {ColorPalette.TEXT_MAIN}; "
        f"border: 1px solid transparent; padding-left: {scaled_px(5)}px; }}"
        f"QComboBox:hover {{ background: {ColorPalette.BG_HOVER}; border-color: transparent; }}"
        f"QComboBox QAbstractItemView {{ background: {ColorPalette.BG_DROPDOWN}; color: {ColorPalette.TEXT_MAIN}; "
        f"selection-background-color: {ColorPalette.PRIMARY}; outline: none; }}"
    )


def sidebar_content_style() -> str:
    return f"QWidget {{ background: transparent; border: none; }}"


def build_dialog_base_style() -> str:
    return (
        f"QDialog {{ background: {ColorPalette.BG_DARK}; color: {ColorPalette.TEXT_LIGHT}; }} "
        f"QLabel {{ color: {ColorPalette.TEXT_MUTED}; }}"
        f"{button_style('secondary', size='normal')}"
        f"QPushButton#primary {{ background: {ColorPalette.PRIMARY}; color: {ColorPalette.TEXT_INVERSE}; }}"
        f"QPushButton#primary:hover {{ background: {ColorPalette.PRIMARY_HOVER}; }}"
    )


def build_dialog_input_style() -> str:
    return input_control_style("QLineEdit")


def build_page_style() -> str:
    is_light = make_qcolor(ColorPalette.BG_LIST).lightness() > 120
    source_soft = ColorPalette.GROUPING_LIST[2] if len(ColorPalette.GROUPING_LIST) > 2 else ColorPalette.BG_HOVER
    target_soft = ColorPalette.GROUPING_LIST[4] if len(ColorPalette.GROUPING_LIST) > 4 else ColorPalette.BG_HOVER
    row_alt = source_soft if is_light else ColorPalette.BG_DARK
    checkbox_bg = ColorPalette.STATUS_INFO_SOFT
    secondary_button_bg = ColorPalette.ACTION_SECONDARY
    return (
        f"QWidget#BuildPage {{ background: transparent; color: {ColorPalette.TEXT_LIGHT}; }}"
        f"QLabel {{ color: {ColorPalette.TEXT_LIGHT}; }}"
        f"QWidget#CompareOptionsCard {{ background: {ColorPalette.BG_LIST}; border: none; border-radius: {scaled_px(8)}px; }}"
        f"QWidget#CompareReviewCard {{ background: {ColorPalette.BG_LIST}; border: none; "
        f"border-radius: {scaled_px(8)}px; }}"
        f"QWidget#CompareTreePanel {{ background: {ColorPalette.BG_LIST}; "
        f"border: none; border-radius: {scaled_px(8)}px; }}"
        f"QLabel#CompareCardTitle {{ background: transparent; "
        f"color: {ColorPalette.TEXT_LIGHT}; font-weight: bold; font-size: {scaled_px(14)}px; "
        f"padding: {scaled_px(4)}px 0; }}"
        f"QLabel#ComparePanelHeader {{ background: transparent; "
        f"color: {ColorPalette.TEXT_LIGHT}; font-weight: bold; font-size: {scaled_px(14)}px; border-radius: 0; "
        f"padding: {scaled_px(4)}px 0; }}"
        f"QLabel#ComparePanelFooter {{ background: transparent; color: {ColorPalette.TEXT_HEADER}; "
        f"font-size: {scaled_px(11)}px; padding: {scaled_px(4)}px 0; }}"
        f"QLabel#CompareFieldLabel {{ color: {ColorPalette.TEXT_HEADER}; font-weight: bold; }}"
        f"QLabel#CompareTargetError {{ color: {ColorPalette.DANGER}; font-weight: normal; "
        f"padding: 0 0 {scaled_px(2)}px 0; }}"
        f"QLabel#CompareReviewPrompt {{ color: {ColorPalette.TEXT_LIGHT}; font-weight: normal; }}"
        f"QLabel#CompareSummary {{ background: {ColorPalette.BG_HOVER}; color: {ColorPalette.TEXT_LIGHT}; "
        f"border: none; border-radius: {scaled_px(6)}px; padding: {scaled_px(8)}px {scaled_px(10)}px; }}"
        f"{input_control_style('QLineEdit')}"
        f"QCheckBox {{ color: {ColorPalette.TEXT_LIGHT}; spacing: {scaled_px(8)}px; }}"
        f"QCheckBox::indicator {{ width: {scaled_px(15)}px; height: {scaled_px(15)}px; "
        f"background: {checkbox_bg}; border: none; border-radius: {scaled_px(2)}px; }}"
        f"QCheckBox::indicator:hover {{ background: {ColorPalette.BG_HOVER}; }}"
        f"QCheckBox::indicator:checked {{ background: {ColorPalette.PRIMARY}; }}"
        f"QCheckBox::indicator:checked:hover {{ background: {ColorPalette.PRIMARY_HOVER}; }}"
        f"{button_style('secondary', size='toolbar', min_width=84)}"
        f"QPushButton#secondary {{ background: {secondary_button_bg}; color: {ColorPalette.TEXT_MAIN}; }}"
        f"QPushButton#secondary:hover {{ background: {ColorPalette.BG_HOVER}; }}"
        f"QPushButton#CompareBrowseButton {{ background: {ColorPalette.ACTION_SECONDARY}; color: {ColorPalette.TEXT_MAIN}; "
        f"border: none; border-radius: {scaled_px(5)}px; font-weight: bold; }}"
        f"QPushButton#CompareBrowseButton:hover {{ background: {ColorPalette.BG_HOVER}; border: none; }}"
        f"QTreeWidget {{ background: {ColorPalette.BG_LIST}; border: none; color: {ColorPalette.TEXT_LIGHT}; "
        f"outline: none; alternate-background-color: {row_alt}; }}"
        f"QTreeWidget[compareTone=\"source\"] {{ alternate-background-color: {source_soft}; }}"
        f"QTreeWidget[compareTone=\"target\"] {{ alternate-background-color: {target_soft}; }}"
        f"QTreeWidget::viewport {{ background: {ColorPalette.BG_LIST}; }}"
        f"QTreeWidget::item {{ min-height: {scaled_px(24)}px; border: none; }}"
        f"QTreeWidget::item:hover {{ background: {ColorPalette.TABLE_HOVER}; }}"
        f"{header_section_style()}"
        f"QSplitter::handle {{ background: transparent; width: {scaled_px(8)}px; border: none; }}"
        f"{scrollbar_style()}"
    )


def build_compare_summary_style() -> str:
    return f"font-size: {scaled_px(12)}px;"


def preserved_ok_button_style() -> str:
    return button_style("primary", size="normal", min_width=120)


def workspace_badge_style(level: str = "info") -> str:
    bg = ColorPalette.BG_MED
    fg = ColorPalette.TEXT_LIGHT
    border = ColorPalette.BORDER_LIGHT
    if level == "success":
        bg = ColorPalette.SUCCESS
        fg = ColorPalette.BG_DARKER
        border = ColorPalette.SUCCESS
    elif level == "warn":
        bg = ColorPalette.WARNING
        fg = ColorPalette.BG_DARKER
        border = ColorPalette.WARNING
    elif level == "danger":
        bg = ColorPalette.DANGER
        fg = ColorPalette.TEXT_MAIN
        border = ColorPalette.DANGER
    return (
        f"QLabel {{ background: {bg}; color: {fg}; border: none; "
        f"border-radius: {scaled_px(8)}px; padding: {scaled_px(4)}px {scaled_px(8)}px; font-weight: bold; }}"
    )


def workspace_banner_style(level: str = "info") -> str:
    accent = ColorPalette.PRIMARY
    if level == "warn":
        accent = ColorPalette.WARNING
    elif level == "danger":
        accent = ColorPalette.DANGER
    elif level == "success":
        accent = ColorPalette.SUCCESS
    bg = make_qcolor(accent)
    bg.setAlpha(34)
    return (
        f"QLabel {{ background: {bg.name(QColor.HexArgb)}; color: {ColorPalette.TEXT_LIGHT}; "
        f"border: none; border-radius: {scaled_px(5)}px; "
        f"padding: {scaled_px(6)}px {scaled_px(8)}px; }}"
    )


def workspace_card_style() -> str:
    grid = "rgba(255, 255, 255, 0.045)" if make_qcolor(ColorPalette.BG_LIST).lightness() < 120 else "rgba(42, 100, 150, 0.055)"
    return (
        f"QFrame#WorkspaceCard {{ background: {ColorPalette.BG_LIST}; border: none; "
        f"border-radius: {scaled_px(8)}px; }}"
        f"QLabel {{ color: {ColorPalette.TEXT_MUTED}; }}"
        f"QLabel[role=\"metric-value\"] {{ color: {ColorPalette.TEXT_MAIN}; font-weight: bold; }}"
        f"QListWidget, QTableWidget {{ background: {ColorPalette.BG_LIST}; color: {ColorPalette.TEXT_LIGHT}; "
        f"border: none; border-radius: {scaled_px(8)}px; alternate-background-color: {ColorPalette.BG_DARK}; "
        f"gridline-color: {grid}; }}"
        f"QListWidget::item, QTableWidget::item {{ padding: {scaled_px(7)}px {scaled_px(12)}px; border: none; }}"
        f"QListWidget::item:hover, QTableWidget::item:hover {{ background: {ColorPalette.TABLE_HOVER}; }}"
        f"QHeaderView::section {{ background-color: {ColorPalette.BG_HOVER}; color: {ColorPalette.TEXT_HEADER}; "
        f"padding: {scaled_px(8)}px {scaled_px(12)}px; border: none; font-weight: bold; }}"
    )


def workspace_table_widget_style() -> str:
    return data_table_style("QTableWidget")


def workspace_primary_button_style() -> str:
    return button_style("primary", size="normal")


def workspace_sidebar_button_style() -> str:
    return (
        f"QPushButton {{ background: transparent; color: {ColorPalette.TEXT_LIGHT}; border: none; "
        f"border-radius: {scaled_px(4)}px; padding: 0 {scaled_px(10)}px; min-height: {scaled_px(36)}px; "
        f"text-align: left; font-size: {scaled_px(13)}px; font-weight: normal; }}"
        f"QPushButton:hover {{ background: {ColorPalette.TABLE_HOVER}; }}"
        f"QPushButton[active=\"true\"] {{ background: {ColorPalette.SELECTION}; color: {ColorPalette.TEXT_MAIN}; }}"
        f"QPushButton:disabled {{ background: transparent; color: {ColorPalette.TEXT_DIM}; }}"
    )


def workspace_stat_box_style() -> str:
    alias_bg = ColorPalette.IDENTITY_SOFT[4] if len(ColorPalette.IDENTITY_SOFT) > 4 else ColorPalette.BG_HOVER
    additions_bg = ColorPalette.STATUS_SUCCESS_SOFT
    conflicts_bg = ColorPalette.STATUS_WARNING_SOFT
    return (
        f"QFrame#WorkspaceStatBox {{ background: {ColorPalette.BG_HOVER}; border: none; border-radius: {scaled_px(6)}px; }}"
        f"QFrame#WorkspaceStatBox[statTone=\"aliases\"] {{ background: {alias_bg}; }}"
        f"QFrame#WorkspaceStatBox[statTone=\"additions\"] {{ background: {additions_bg}; }}"
        f"QFrame#WorkspaceStatBox[statTone=\"conflicts\"] {{ background: {conflicts_bg}; }}"
        f"QLabel#WorkspaceStatValue {{ color: {ColorPalette.TEXT_LIGHT}; font-size: {scaled_px(18)}px; "
        f"font-weight: bold; }}"
        f"QLabel#WorkspaceStatLabel {{ color: {ColorPalette.TEXT_HEADER}; font-size: {scaled_px(11)}px; }}"
    )


def workspace_tab_widget_style() -> str:
    return (
        f"QTabWidget::pane {{ border: none; top: -1px; background: {ColorPalette.BG_DARK}; }}"
        f"QTabBar::tab {{ background: {ColorPalette.BG_MED}; color: {ColorPalette.TEXT_DIM}; "
        f"padding: {scaled_px(8)}px {scaled_px(18)}px; border: none; "
        "border-bottom: none; border-top-left-radius: 4px; border-top-right-radius: 4px; margin-right: 2px; }"
        f"QTabBar::tab:selected {{ background: {ColorPalette.BG_DARK}; color: {ColorPalette.TEXT_MAIN}; "
        f"border-bottom: none; }}"
    )


def workspace_combo_style() -> str:
    return (
        f"QComboBox {{ background: {ColorPalette.BG_LIGHT}; color: {ColorPalette.TEXT_MAIN}; "
        f"border: 1px solid transparent; padding-left: {scaled_px(6)}px; "
        f"min-height: {scaled_px(28)}px; }}"
        f"QComboBox:hover {{ background: {ColorPalette.BG_HOVER}; border-color: transparent; }}"
        f"QComboBox::drop-down {{ width: {scaled_px(24)}px; border-left: none; }}"
        f"QComboBox QAbstractItemView {{ background: {ColorPalette.BG_DROPDOWN}; color: {ColorPalette.TEXT_MAIN}; "
        f"selection-background-color: {ColorPalette.PRIMARY}; outline: none; }}"
    )


def workspace_field_label_style() -> str:
    return f"color: {ColorPalette.TEXT_LIGHT};"


def workspace_input_style() -> str:
    return input_control_style("QLineEdit")


def tree_drop_hint_style() -> str:
    border_color = make_qcolor(ColorPalette.TEXT_MAIN)
    border_color.setAlpha(80)
    bg = make_qcolor(ColorPalette.SELECTION)
    bg.setAlpha(220)
    return (
        f"QLabel {{ background: {bg.name(QColor.HexArgb)}; color: {ColorPalette.TEXT_MAIN}; "
        f"border: 1px solid {border_color.name(QColor.HexArgb)}; border-radius: {scaled_px(4)}px; "
        f"padding: {scaled_px(4)}px {scaled_px(8)}px; }}"
    )


def tree_view_style() -> str:
    grid = "rgba(255, 255, 255, 0.045)" if make_qcolor(ColorPalette.BG_LIST).lightness() < 120 else "rgba(42, 100, 150, 0.055)"
    return (
        f"QTreeView {{ background: {ColorPalette.BG_LIST}; color: {ColorPalette.TEXT_LIGHT}; border: none; outline: none; border-radius: {scaled_px(8)}px; }}"
        f"QTreeView::viewport {{ background: {ColorPalette.BG_LIST}; border-radius: {scaled_px(8)}px; }}"
        f"QHeaderView::section {{ background-color: {ColorPalette.BG_HOVER}; border: none; }}"
        f"QTreeView::item:selected {{ background: {ColorPalette.SELECTION}; color: {ColorPalette.TEXT_MAIN}; }}"
        f"QTreeView::item:focus, QTreeView::item:selected:focus {{ border: none; outline: none; }}"
        f"QTreeView::item:hover {{ background: {ColorPalette.TABLE_HOVER}; }}"
    )


def tree_header_style() -> str:
    from gui.utils.constants import SIDEBAR_HEADER_HEIGHT
    h = scaled_px(SIDEBAR_HEADER_HEIGHT)
    return (
        f"{header_section_style(density='compact')}"
        f"QHeaderView {{ min-height: {h}px; max-height: {h}px; }}"
        f"QHeaderView::section {{ min-height: {h}px; max-height: {h}px; }}"
    )


def frame_plain_style() -> str:
    return "QFrame { background: transparent; border: none; }"


def staging_table_view_style() -> str:
    return (
        f"{data_table_style('QTableView')}"
        "QTableView::item:focus { outline: none; }"
    )


def tag_editor_style() -> str:
    return input_control_style("QLineEdit")

def carousel_title_style(is_active: bool) -> str:
    color = ColorPalette.PRIMARY if is_active else ColorPalette.TEXT_DIMMER
    hover = ColorPalette.PRIMARY_BRIGHT if is_active else ColorPalette.TEXT_DIM
    return (
        f"QPushButton {{ color: {color}; font-weight: bold; font-size: 11px; letter-spacing: 1px; "
        "text-align: left; border: none; background: transparent; padding: 0; }"
        f"QPushButton:hover {{ color: {hover}; }}"
    )


def carousel_value_style(is_active: bool) -> str:
    if is_active:
        return (
            f"QPushButton {{ background: {ColorPalette.TABLE_SELECT}; color: {ColorPalette.TEXT_MAIN}; border: none; border-radius: 4px; padding: 0 20px; text-align: left; font-size: 12px; font-weight: bold; }}"
            f"QPushButton:hover {{ background: {ColorPalette.SELECTION}; }}"
        )
    return (
        f"QPushButton {{ background: transparent; color: {ColorPalette.TEXT_GRAY}; border: none; border-radius: 4px; padding: 0 20px; text-align: left; font-size: 12px; font-weight: bold; }}"
        f"QPushButton:hover {{ background: {ColorPalette.TABLE_HOVER}; }}"
    )
