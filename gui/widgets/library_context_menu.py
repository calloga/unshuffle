from __future__ import annotations

from typing import cast

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QMenu

from unshuffle.core.constants import CATEGORIES

from ..utils.constants import COLUMN_CONFIG, StagingColumn
from ..utils.styles import apply_style, menu_style


def show_library_context_menu(tab, pos) -> None:
    if not tab.proxy_model:
        return

    selection = tab.view_table.selectionModel().selectedIndexes()
    if not selection:
        return
    rows = sorted(list(set(tab.proxy_model.mapToSource(idx).row() for idx in selection)))

    menu = QMenu(tab)
    apply_style(menu, menu_style())

    copy_action = QAction("Copy", tab)
    copy_action.triggered.connect(lambda checked=False: tab.copy_selection_to_clipboard())
    menu.addAction(copy_action)

    copy_path_action = QAction("Copy as Path", tab)
    copy_path_action.triggered.connect(lambda checked=False, rows=rows: tab.copy_rows_as_paths(rows))
    menu.addAction(copy_path_action)

    play_action = QAction("Play Preview (Space)", tab)
    play_action.triggered.connect(lambda checked=False: tab.playRequested.emit(selection[0]))
    menu.addAction(play_action)

    similar_action = QAction("Similarity Explorer", tab)
    similar_action.triggered.connect(lambda checked=False: tab.similarityRequested.emit(selection[0]))
    menu.addAction(similar_action)

    records = tab._records_for_source_rows(rows)
    opposite_type = tab._opposite_audio_type_for_records(records)
    if opposite_type:
        change_type_action = QAction(f"Change to {opposite_type}", tab)
        change_type_action.triggered.connect(
            lambda checked=False, val=opposite_type, r=records: tab.bulkTypeRequested.emit(val, r)
        )
        menu.addAction(change_type_action)

    explore_action = QAction("Show in Explorer", tab)
    explore_action.triggered.connect(lambda checked=False: tab.openExplorerRequested.emit(selection[0]))
    menu.addAction(explore_action)

    menu.addSeparator()
    delete_action = QAction("Delete from Disk", tab)
    delete_action.triggered.connect(lambda checked=False, r=records: tab.deleteRecordsRequested.emit(r))
    menu.addAction(delete_action)

    idx = selection[0]
    val = str(idx.data(Qt.DisplayRole))
    col = idx.column()
    if col in COLUMN_CONFIG:
        val_search_action = QAction(f"Filter by '{val}'", tab)
        val_search_action.triggered.connect(
            lambda checked=False, p=COLUMN_CONFIG[cast(StagingColumn, col)]["prefix"], v=val: tab.quickFilterRequested.emit(
                f'{p}:"{v}"',
                tab._compose_mode(QApplication.keyboardModifiers()),
            )
        )
        menu.addAction(val_search_action)

    menu.addSeparator()

    if len(rows) > 1:
        category_menu = menu.addMenu("Set Category for Selection")
        from unshuffle.core.constants import SUB_TAXONOMY_MAP

        for category in CATEGORIES:
            if category in SUB_TAXONOMY_MAP and SUB_TAXONOMY_MAP[category]:
                subcategory_menu = category_menu.addMenu(category)
                subcategories = sorted(list(set(sub for sub in SUB_TAXONOMY_MAP[category].values() if sub != "no-sub")))

                for subcategory in subcategories:
                    action = QAction(subcategory, tab)
                    action.triggered.connect(
                        lambda checked=False, c=category, s=subcategory, r=records: tab.bulkSubcategoryRequested.emit(c, s, r)
                    )
                    subcategory_menu.addAction(action)

                generic_action = QAction("Generic / Root", tab)
                generic_action.triggered.connect(
                    lambda checked=False, c=category, r=records: tab.bulkCategoryRequested.emit(c, r)
                )
                subcategory_menu.addAction(generic_action)
            else:
                action = QAction(category, tab)
                action.triggered.connect(lambda checked=False, c=category, r=records: tab.bulkCategoryRequested.emit(c, r))
                category_menu.addAction(action)

        type_menu = menu.addMenu("Set Type for Selection")
        for audio_type in ["Loops", "Oneshots"]:
            action = QAction(audio_type, tab)
            action.triggered.connect(lambda checked=False, val=audio_type, r=records: tab.bulkTypeRequested.emit(val, r))
            type_menu.addAction(action)

    menu.exec(tab.view_table.viewport().mapToGlobal(pos))
