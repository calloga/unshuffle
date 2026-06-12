"""QSS builders from semantic tokens."""

from __future__ import annotations

from .tokens_semantic import ThemeColors


def build_main_style(colors: ThemeColors) -> str:
    return f"""
    QMainWindow {{ background-color: {colors.bg_darker}; }}
    QWidget {{ color: {colors.text_main}; }}
    QDialog, QMessageBox {{ background-color: {colors.bg_dark}; color: {colors.text_light}; }}
    QDialog QLabel, QMessageBox QLabel {{ color: {colors.text_light}; }}
    QFrame#AppWindow, QWidget#AppWindow {{ background-color: {colors.bg_dark}; border: none; border-radius: 12px; }}
    QGroupBox {{ background: {colors.surface_card}; border: none; margin-top: 12px; padding-top: 10px; color: {colors.text_light}; border-radius: 6px; }}
    QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 6px; color: {colors.primary_bright}; }}
    QMenuBar {{ background-color: {colors.bg_list}; color: {colors.text_light}; border: none; }}
    QMenuBar::item {{ background: transparent; padding: 7px 12px; border-radius: 4px; }}
    QMenuBar::item:selected {{ background: {colors.bg_hover}; }}
    QMenu {{ background-color: {colors.bg_dropdown}; color: {colors.text_light}; border: none; padding: 6px; border-radius: 6px; }}
    QMenu::item {{ background: transparent; color: {colors.text_light}; padding: 7px 38px 7px 12px; border-radius: 4px; }}
    QMenu::item:selected {{ background: {colors.primary}; color: {colors.text_inverse}; }}
    QMenu::separator {{ height: 1px; background: {colors.bg_light}; margin: 4px 8px; }}
    QToolTip {{ background-color: {colors.bg_dropdown}; color: {colors.text_main}; border: 1px solid {colors.border_accent}; padding: 5px 7px; border-radius: 4px; }}
    QTabWidget {{ background: transparent; }}
    QTabWidget::pane {{ border: none; top: -1px; background: {colors.surface_subtle}; border-radius: 4px; }}
    QTabWidget::tab-bar {{ left: 0; }}
    QTabBar {{ background: {colors.surface_subtle}; }}
    QTabBar::tab {{ background: {colors.bg_med}; color: {colors.text_dim}; padding: 10px 18px; border: none; border-top-left-radius: 3px; border-top-right-radius: 3px; margin-right: 3px; }}
    QTabBar::tab:selected {{ background: {colors.surface_raised}; color: {colors.text_light}; }}
    QTabBar::tab:hover:!selected {{ background: {colors.bg_hover}; color: {colors.text_main}; }}
    QWidget#LibraryToolbar {{ background: {colors.bg_list}; border: none; border-radius: 8px; }}
    QWidget#LibraryViewPanel {{ background: {colors.bg_list}; border: none; border-radius: 8px; }}
    QLineEdit {{ background: {colors.bg_light}; color: {colors.text_main}; border: none; padding: 0 8px; border-radius: 4px; min-height: 36px; selection-background-color: {colors.primary}; selection-color: {colors.text_inverse}; }}
    QLineEdit:hover, QLineEdit:focus {{ background: {colors.bg_hover}; }}
    QListView#LibrarySearchCompleter, QListView#TreeFilterCompleter {{ background-color: {colors.bg_dropdown}; color: {colors.text_main}; border: 1px solid {colors.border}; border-radius: 6px; padding: 4px; }}
    QListView#LibrarySearchCompleter::item, QListView#TreeFilterCompleter::item {{ min-height: 24px; padding: 6px 12px; border-radius: 4px; color: {colors.text_main}; }}
    QListView#LibrarySearchCompleter::item:hover, QListView#TreeFilterCompleter::item:hover {{ background-color: {colors.bg_hover}; }}
    QListView#LibrarySearchCompleter::item:selected, QListView#TreeFilterCompleter::item:selected {{ background-color: {colors.primary}; color: {colors.text_inverse}; }}
    QComboBox {{ background: {colors.bg_light}; color: {colors.text_main}; border: 1px solid transparent; padding-left: 8px; min-height: 30px; border-radius: 4px; }}
    QComboBox:hover {{ background: {colors.bg_hover}; border-color: transparent; }}
    QComboBox::drop-down {{ border: none; width: 24px; }}
    QComboBox QAbstractItemView {{ background: {colors.bg_dropdown}; color: {colors.text_main}; selection-background-color: {colors.primary}; selection-color: {colors.text_inverse}; outline: none; }}
    QPushButton {{ background: {colors.action_secondary}; color: {colors.text_main}; border: 1px solid transparent; padding: 0 14px; border-radius: 4px; font-weight: 700; min-height: 32px; }}
    QPushButton#primary, QPushButton[role="primary"] {{ background: {colors.primary}; color: {colors.text_inverse}; border: 1px solid transparent; }}
    QPushButton:hover {{ background: {colors.bg_hover}; border-color: transparent; }}
    QPushButton#primary:hover, QPushButton[role="primary"]:hover {{ background: {colors.primary_hover}; border-color: transparent; }}
    QPushButton:disabled {{ background: {colors.border_light}; color: {colors.text_dim}; }}
    QPushButton#danger {{ background: {colors.danger}; border-color: transparent; }}
    QPushButton#danger:hover {{ background: {colors.danger_hover}; }}
    QMessageBox QPushButton, QDialogButtonBox QPushButton {{ background: {colors.action_secondary}; color: {colors.text_main}; border: none; border-radius: 4px; min-width: 76px; min-height: 32px; padding: 0 16px; font-weight: 700; }}
    QMessageBox QPushButton:hover, QDialogButtonBox QPushButton:hover {{ background: {colors.bg_hover}; }}
    QMessageBox QPushButton:default, QDialogButtonBox QPushButton:default {{ background: {colors.primary}; color: {colors.text_inverse}; }}
    QMessageBox QPushButton:default:hover, QDialogButtonBox QPushButton:default:hover {{ background: {colors.primary_hover}; }}
    QCheckBox {{ color: {colors.text_main}; spacing: 8px; }}
    QCheckBox::indicator {{ width: 15px; height: 15px; border: 1px solid transparent; border-radius: 2px; background: {colors.status_info_soft}; }}
    QCheckBox::indicator:hover {{ background: {colors.bg_hover}; border-color: transparent; }}
    QCheckBox::indicator:checked {{ background: {colors.primary}; border-color: transparent; }}
    QTableView {{ background-color: {colors.bg_list}; gridline-color: transparent; color: {colors.text_light}; selection-background-color: {colors.table_select}; selection-color: {colors.text_main}; border: none; border-radius: 8px; alternate-background-color: {colors.bg_list}; }}
    QTableView::item:selected:active, QTableView::item:selected:!active {{ background: {colors.table_select}; color: {colors.text_main}; }}
    QTableView::item:hover {{ background: {colors.table_hover}; }}
    QTreeView, QTreeWidget {{ background-color: {colors.bg_list}; color: {colors.text_light}; border: none; border-radius: 8px; outline: none; alternate-background-color: {colors.bg_list}; }}
    QTreeView::item, QTreeWidget::item {{ padding: 3px 5px; border: none; outline: none; }}
    QTreeView::item:hover {{ background: {colors.table_hover}; }}
    QTreeView::item:selected {{ background: {colors.selection}; color: {colors.text_main}; }}
    QTreeView::item:focus, QTreeView::item:selected:focus {{ border: none; outline: none; }}
    QTreeWidget::item:hover {{ background: {colors.table_hover}; }}
    QTreeWidget::item:selected {{ background: {colors.selection}; color: {colors.text_main}; }}
    QHeaderView {{ background-color: {colors.bg_hover}; border: none; }}
    QHeaderView::section {{ background-color: {colors.bg_hover}; color: {colors.text_header}; padding: 8px 12px; border: none; font-weight: bold; }}
    QHeaderView::up-arrow, QHeaderView::down-arrow {{ width: 0px; height: 0px; }}
    QTableCornerButton::section {{ background-color: {colors.bg_hover}; border: none; }}
    QSplitter::handle:horizontal {{ background: {colors.bg_dark}; width: 6px; }}
    QSplitter::handle:vertical {{ background: {colors.bg_dark}; height: 6px; }}
    QTextEdit {{ background: {colors.bg_med}; color: {colors.text_muted}; font-family: 'Consolas', monospace; border: none; border-radius: 4px; selection-background-color: {colors.selection}; selection-color: {colors.text_main}; }}
    QProgressBar {{ border: none; border-radius: 2px; text-align: center; height: 6px; background: {colors.bg_med}; }}
    QProgressBar::chunk {{ background-color: {colors.primary}; }}
    QScrollArea {{ background: transparent; border: none; }}
    QScrollBar:vertical {{ background: transparent; width: 8px; margin: 0; }}
    QScrollBar::handle:vertical {{ background: {colors.bg_scrollbar_handle}; min-height: 28px; border-top-left-radius: 0; border-bottom-left-radius: 0; border-top-right-radius: 4px; border-bottom-right-radius: 4px; }}
    QScrollBar::handle:vertical:hover {{ background: {colors.border_accent}; }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar:horizontal {{ background: transparent; height: 8px; margin: 0; }}
    QScrollBar::handle:horizontal {{ background: {colors.bg_scrollbar_handle}; min-width: 28px; border-top-left-radius: 0; border-top-right-radius: 0; border-bottom-left-radius: 4px; border-bottom-right-radius: 4px; }}
    QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: transparent; }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
"""
