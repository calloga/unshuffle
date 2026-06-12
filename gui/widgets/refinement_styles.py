from __future__ import annotations

from gui.utils.styles import ColorPalette, button_style, scaled_px


def refinement_action_combo_style() -> str:
    return (
        f"{button_style('danger', size='cell')}"
        f"QPushButton[actionTone=\"accept\"] {{ background: {ColorPalette.SUCCESS}; }}"
        f"QPushButton[actionTone=\"reject\"] {{ background: {ColorPalette.DANGER}; }}"
    )


def refinement_outlier_cell_container_style() -> str:
    return "QWidget { background: transparent; }"


def refinement_outlier_action_combo_style() -> str:
    return (
        f"{button_style('danger', size='cell')}"
        f"QPushButton[actionTone=\"accept\"] {{ background: {ColorPalette.SUCCESS}; }}"
        f"QPushButton[actionTone=\"reject\"] {{ background: {ColorPalette.DANGER}; }}"
    )


def refinement_target_combo_style() -> str:
    return (
        f"QPushButton {{ background: transparent; border: none; border-radius: {scaled_px(3)}px; "
        f"padding: 0 {scaled_px(10)}px; min-height: {scaled_px(26)}px; color: {ColorPalette.TEXT_MAIN}; "
        "font-weight: normal; }"
    )


def anchor_action_combo_style() -> str:
    return (
        f"QComboBox {{ background: {ColorPalette.BG_LIGHT}; color: {ColorPalette.TEXT_MAIN}; "
        f"border: none; border-radius: {scaled_px(4)}px; padding: 0 {scaled_px(24)}px 0 {scaled_px(10)}px; "
        f"min-height: {scaled_px(26)}px; font-weight: 600; }}"
        f"QComboBox[actionTone=\"none\"] {{ background: {ColorPalette.BG_LIGHT}; color: {ColorPalette.TEXT_MAIN}; }}"
        f"QComboBox[actionTone=\"promotion\"] {{ background: {ColorPalette.SUCCESS}; color: {ColorPalette.TEXT_INVERSE}; }}"
        f"QComboBox[actionTone=\"ignore\"] {{ background: {ColorPalette.DANGER}; color: {ColorPalette.TEXT_INVERSE}; }}"
        f"QComboBox[actionTone=\"update\"] {{ background: {ColorPalette.BG_MED}; color: {ColorPalette.TEXT_LIGHT}; }}"
        f"QComboBox:hover {{ background: {ColorPalette.BG_HOVER}; }}"
        f"QComboBox[actionTone=\"promotion\"]:hover {{ background: {ColorPalette.SUCCESS}; }}"
        f"QComboBox[actionTone=\"ignore\"]:hover {{ background: {ColorPalette.DANGER_HOVER}; }}"
        f"QComboBox[actionTone=\"update\"]:hover {{ background: {ColorPalette.BG_HOVER}; }}"
        f"QComboBox::drop-down {{ width: {scaled_px(22)}px; border: none; }}"
        f"QComboBox QAbstractItemView {{ background: {ColorPalette.BG_DROPDOWN}; color: {ColorPalette.TEXT_MAIN}; "
        f"selection-background-color: {ColorPalette.PRIMARY}; selection-color: {ColorPalette.TEXT_INVERSE}; outline: none; }}"
    )


def refinement_target_menu_style() -> str:
    return (
        f"QMenu {{ background: {ColorPalette.BG_DROPDOWN}; color: {ColorPalette.TEXT_MAIN}; "
        f"border: none; padding: {scaled_px(6)}px; }}"
        f"QMenu::item {{ padding: {scaled_px(6)}px {scaled_px(24)}px {scaled_px(6)}px {scaled_px(10)}px; }}"
        f"QMenu::item:selected {{ background: {ColorPalette.PRIMARY}; color: {ColorPalette.TEXT_INVERSE}; }}"
    )


def refinement_tab_style() -> str:
    return (
        f"QTabWidget::pane {{ border: none; background: transparent; }}"
        f"QTabBar::tab {{ background: {ColorPalette.BG_LIGHT}; color: {ColorPalette.TEXT_LIGHT}; "
        f"padding: {scaled_px(8)}px {scaled_px(14)}px; border: none; "
        f"border-top-left-radius: {scaled_px(4)}px; border-top-right-radius: {scaled_px(4)}px; "
        f"margin-right: {scaled_px(2)}px; }}"
        f"QTabBar::tab:selected {{ background: {ColorPalette.PRIMARY}; color: {ColorPalette.TEXT_INVERSE}; }}"
        f"QTabBar::tab:hover:!selected {{ background: {ColorPalette.BG_HOVER}; color: {ColorPalette.TEXT_MAIN}; }}"
    )


def refinement_dialog_style() -> str:
    return (
        f"QDialog {{ background: {ColorPalette.BG_DARK}; color: {ColorPalette.TEXT_LIGHT}; }}"
        f"QLabel {{ color: {ColorPalette.TEXT_LIGHT}; }}"
        f"{button_style('secondary', size='normal')}"
        f"QPushButton[role=\"primary\"] {{ background: {ColorPalette.PRIMARY}; color: {ColorPalette.TEXT_INVERSE}; }}"
        f"QPushButton[role=\"primary\"]:hover {{ background: {ColorPalette.PRIMARY_HOVER}; }}"
    )
