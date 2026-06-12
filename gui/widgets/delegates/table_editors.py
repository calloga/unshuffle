from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QLineEdit

from gui.utils.styles import ColorPalette, apply_style, make_qcolor, scaled_px


def _qss_color(value: str) -> str:
    return make_qcolor(value).name()


def table_editor_style() -> str:
    pad_h = scaled_px(8)
    bg = _qss_color(ColorPalette.TABLE_SELECT)
    primary = _qss_color(ColorPalette.PRIMARY)
    text = _qss_color(ColorPalette.TEXT_MAIN)
    inverse_text = _qss_color(ColorPalette.TEXT_INVERSE)
    dropdown_bg = _qss_color(ColorPalette.BG_DROPDOWN)
    border = _qss_color(ColorPalette.BORDER)
    return (
        f"QComboBox[tableEditor=\"true\"], QLineEdit[tableEditor=\"true\"] {{ "
        f"background-color: {bg}; color: {text}; "
        f"selection-background-color: {primary}; selection-color: {inverse_text}; "
        f"border: 1px solid {primary}; border-radius: 0px; "
        f"padding: 0 {pad_h}px; min-height: 0px; }}"
        f"QComboBox[tableEditor=\"true\"]:hover, QComboBox[tableEditor=\"true\"]:focus, "
        f"QLineEdit[tableEditor=\"true\"]:hover, QLineEdit[tableEditor=\"true\"]:focus {{ "
        f"background-color: {bg}; }}"
        f"QComboBox[tableEditor=\"true\"]::drop-down {{ width: {scaled_px(18)}px; border: none; }}"
        f"QComboBox[tableEditor=\"true\"]::down-arrow {{ width: 0px; height: 0px; }}"
        f"QComboBox[tableEditor=\"true\"] QLineEdit {{ background: transparent; "
        f"color: {text}; selection-background-color: {primary}; "
        f"selection-color: {inverse_text}; border: none; padding: 0px; min-height: 0px; }}"
        f"QComboBox[tableEditor=\"true\"] QAbstractItemView {{ background: {dropdown_bg}; "
        f"color: {text}; selection-background-color: {primary}; "
        f"selection-color: {inverse_text}; outline: none; border: 1px solid {border}; }}"
    )


def mark_table_editor(editor) -> None:
    editor.setProperty("tableEditor", True)
    editor.setAutoFillBackground(True)
    editor.setAttribute(Qt.WA_StyledBackground, True)
    apply_style(editor, table_editor_style())
    if isinstance(editor, QLineEdit):
        editor.setFrame(False)
    if isinstance(editor, QComboBox):
        editor.setFrame(False)
        line_edit = editor.lineEdit()
        if line_edit is not None:
            line_edit.setProperty("tableEditor", True)
            line_edit.setAutoFillBackground(False)
            line_edit.setAttribute(Qt.WA_StyledBackground, True)
