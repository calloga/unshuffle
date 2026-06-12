from PySide6.QtGui import QColor

from ...utils.styles import (
    ColorPalette,
    button_style as shared_button_style,
    header_section_style,
    scaled_px,
    scrollbar_style,
)
from ...utils.color_helpers import make_qcolor


WARNING_TEXT = (
    "Customize your organized library's structure to your taste, or create an entirely new organization."
)


def button_style(tone: str = "secondary") -> str:
    if tone == "add_child":
        return (
            f"QPushButton {{ background: {ColorPalette.STATUS_INFO_SOFT}; color: {ColorPalette.TEXT_LIGHT}; "
            f"border: none; border-radius: {scaled_px(4)}px; font-weight: bold; "
            f"min-height: {scaled_px(32)}px; padding: 0 {scaled_px(14)}px; }}"
            f"QPushButton:hover {{ background: {ColorPalette.TABLE_HOVER}; }}"
            f"QPushButton:disabled {{ background: {ColorPalette.STATUS_INFO_SOFT}; color: {ColorPalette.TEXT_DIM}; }}"
        )
    role = "primary" if tone in {"primary", "folder"} else tone
    return shared_button_style(role, size="normal")


def editor_style() -> str:
    help_bg = make_qcolor(ColorPalette.PRIMARY)
    help_bg.setAlpha(34)
    is_dark = make_qcolor(ColorPalette.BG_LIST).lightness() < 120
    is_default_theme = (
        str(ColorPalette.PRIMARY).lower() == "#246cfc"
        and str(ColorPalette.BG_DARKER).lower() == "#080a0f"
    )
    field_bg = ColorPalette.BG_LIST if (not is_dark or is_default_theme) else ColorPalette.BG_HOVER
    field_hover = ColorPalette.BG_HOVER if (not is_dark or is_default_theme) else ColorPalette.BG_LIST
    return (
        f"QWidget#TreeOrganizationEditorRoot {{ background: transparent; color: {ColorPalette.TEXT_LIGHT}; }}"
        f"QFrame#TreeEditorContent {{ background: {ColorPalette.BG_LIST}; border: none; border-radius: {scaled_px(8)}px; }}"
        f"QScrollArea#TreeProfileScroll, QWidget#TreeProfileContent {{ background: {ColorPalette.BG_LIST}; border: none; }}"
        f"QFrame#TreeProfileRow {{ background: {ColorPalette.BG_LIST}; border: none; border-radius: 0px; }}"
        f"QFrame#TreeProfileRow:hover {{ background: {ColorPalette.TABLE_HOVER}; }}"
        f"QFrame#TreeProfileRow[selected=\"true\"] {{ background: {ColorPalette.TABLE_HOVER}; }}"
        f"QLabel#TreeProfileActive {{ color: {ColorPalette.PRIMARY}; font-weight: bold; }}"
        f"QLabel#TreeProfileName {{ color: {ColorPalette.TEXT_LIGHT}; font-weight: bold; }}"
        f"QLabel#TreeProfileMeta {{ color: {ColorPalette.TEXT_MUTED}; }}"
        f"QFrame#TreeNewDialog {{ background: {ColorPalette.BG_LIST}; border: none; border-radius: {scaled_px(8)}px; }}"
        f"QLabel#TreeNameError {{ color: {ColorPalette.DANGER}; font-weight: normal; }}"
        f"QLabel#TreeNameError[available=\"true\"] {{ color: {ColorPalette.SUCCESS}; }}"
        f"QListView#TreeFilterCompleter {{ background: {ColorPalette.BG_DROPDOWN}; color: {ColorPalette.TEXT_MAIN}; "
        f"selection-background-color: {ColorPalette.PRIMARY}; selection-color: {ColorPalette.TEXT_INVERSE}; "
        f"border: none; border-radius: {scaled_px(6)}px; padding: {scaled_px(4)}px; }}"
        f"QListView#TreeFilterCompleter::item {{ min-height: {scaled_px(24)}px; padding: {scaled_px(4)}px {scaled_px(8)}px; }}"
        f"QLabel#TreeFilterHint {{ background: transparent; color: {ColorPalette.TEXT_MUTED}; "
        f"border: none; border-radius: {scaled_px(5)}px; padding: {scaled_px(2)}px {scaled_px(2)}px; }}"
        f"QLabel#TreeEditorHelpBanner {{ background: {help_bg.name(QColor.HexArgb)}; color: {ColorPalette.TEXT_LIGHT}; "
        f"border: none; border-radius: {scaled_px(5)}px; padding: {scaled_px(6)}px {scaled_px(8)}px; }}"
        f"QLabel#TreeLogicPill {{ background: {ColorPalette.BG_LIGHT}; color: {ColorPalette.TEXT_MAIN}; "
        f"border: none; border-radius: {scaled_px(4)}px; padding: {scaled_px(5)}px {scaled_px(7)}px; }}"
        f"QDialog {{ background: {ColorPalette.BG_DARK}; color: {ColorPalette.TEXT_LIGHT}; }}"
        f"QLabel {{ color: {ColorPalette.TEXT_LIGHT}; }}"
        f"QLabel#TreeFieldLabel {{ color: {ColorPalette.TEXT_HEADER}; font-size: {scaled_px(11)}px; font-weight: bold; }}"
        f"QLineEdit, QComboBox {{ background: {field_bg}; color: {ColorPalette.TEXT_MAIN}; "
        f"selection-background-color: {ColorPalette.PRIMARY}; selection-color: {ColorPalette.TEXT_INVERSE}; "
        f"border: none; border-radius: {scaled_px(4)}px; padding: {scaled_px(6)}px {scaled_px(8)}px; "
        f"min-height: {scaled_px(30)}px; }}"
        f"QLineEdit:hover, QLineEdit:focus {{ background: {field_hover}; }}"
        f"QLineEdit:disabled, QComboBox:disabled {{ background: {field_bg}; color: {ColorPalette.TEXT_LIGHT}; }}"
        f"QComboBox QAbstractItemView {{ background: {ColorPalette.BG_DROPDOWN}; color: {ColorPalette.TEXT_MAIN}; "
        f"selection-background-color: {ColorPalette.PRIMARY}; selection-color: {ColorPalette.TEXT_INVERSE}; outline: none; }}"
        f"QCheckBox {{ color: {ColorPalette.TEXT_LIGHT}; spacing: {scaled_px(8)}px; }}"
        f"QCheckBox:disabled {{ color: {ColorPalette.TEXT_MUTED}; }}"
        f"QFrame#TreeEditorDetail {{ background: {ColorPalette.BG_LIGHT}; color: {ColorPalette.TEXT_LIGHT}; "
        f"border: none; border-radius: 0px; outline: none; }}"
        f"QFrame#TreeEditorFolderHeader {{ background: {ColorPalette.SELECTION}; border: none; border-radius: {scaled_px(7)}px; }}"
        f"QLabel#TreeEditorFolderTitle {{ color: {ColorPalette.TEXT_MAIN}; font-weight: bold; }}"
        f"QLabel#TreeEditorFolderMeta {{ color: {ColorPalette.TEXT_MAIN}; font-size: {scaled_px(11)}px; }}"
        f"QFrame#TreeEditorActionRow {{ background: transparent; border: none; }}"
        f"QPushButton#TreeAddChildButton {{ background: {ColorPalette.STATUS_INFO_SOFT}; color: {ColorPalette.TEXT_LIGHT}; "
        f"border: none; border-radius: {scaled_px(4)}px; font-weight: bold; }}"
        f"QPushButton#TreeAddChildButton:hover {{ background: {ColorPalette.TABLE_HOVER}; }}"
        f"QLabel#TreeEditorHeader {{ background: {ColorPalette.BG_HOVER}; color: {ColorPalette.TEXT_HEADER}; "
        f"border-radius: 0px; padding: 0 {scaled_px(12)}px; font-weight: bold; "
        f"min-height: {scaled_px(38)}px; max-height: {scaled_px(38)}px; }}"
        f"QLabel#TreeEditorSection {{ color: {ColorPalette.TEXT_HEADER}; font-weight: bold; }}"
        f"QTreeView {{ background: {ColorPalette.BG_LIST}; color: {ColorPalette.TEXT_LIGHT}; "
        f"border: none; border-radius: {scaled_px(8)}px; outline: none; }}"
        f"QTreeView::viewport {{ background: {ColorPalette.BG_LIST}; border-radius: {scaled_px(8)}px; }}"
        f"QTreeView::item {{ min-height: {scaled_px(28)}px; padding: {scaled_px(2)}px {scaled_px(6)}px; border: none; }}"
        f"QTreeView::item:hover {{ background: {ColorPalette.TABLE_HOVER}; }}"
        f"QTreeView::item:selected {{ background: {ColorPalette.SELECTION}; color: {ColorPalette.TEXT_MAIN}; }}"
        f"{header_section_style()}"
        f"{scrollbar_style()}"
    )
