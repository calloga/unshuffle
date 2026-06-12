import os
import unittest
from pathlib import Path
from unittest import mock

from PySide6.QtCore import QItemSelectionModel, Qt, QUrl
from PySide6.QtGui import QKeyEvent

from gui.models.proxy import MultiFilterProxyModel
from gui.models.staging_table import StagingTableModel
from gui.utils.constants import StagingColumn
from gui.views.staging_table import StagingTableView
from unshuffle.core import PlanRecord


class TableShortcutTests(unittest.TestCase):
    def test_ctrl_f_requests_global_search_focus(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance() or QApplication([])
        view = StagingTableView()
        focus_mock = mock.Mock()
        view.focusSearchRequested.connect(focus_mock)

        event = QKeyEvent(QKeyEvent.KeyPress, Qt.Key_F, Qt.ControlModifier)
        view.keyPressEvent(event)

        focus_mock.assert_called_once_with()

    def test_space_requests_play_from_table_view(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance() or QApplication([])
        view = StagingTableView()
        play_mock = mock.Mock()
        view.playRequested.connect(play_mock)

        event = QKeyEvent(QKeyEvent.KeyPress, Qt.Key_Space, Qt.NoModifier)
        view.keyPressEvent(event)

        play_mock.assert_called_once_with()

    def test_drag_fill_applies_edits_to_source_model_rows(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication, QMainWindow
        from PySide6.QtGui import QUndoStack

        app = QApplication.instance() or QApplication([])

        record_a = mock.Mock(spec=PlanRecord)
        record_a.pack = "Pack A"
        record_a.category = "Kicks"
        record_a.subcategory = None
        record_a.tags = []
        record_a.audio_type = "Oneshots"
        record_a.source_path = Path("Source/a.wav")
        record_a.confidence = "0.90"
        record_a.evidence = {}
        record_a.is_manual = False
        record_a.is_preserved = False

        record_b = mock.Mock(spec=PlanRecord)
        record_b.pack = "Pack B"
        record_b.category = "Snares"
        record_b.subcategory = None
        record_b.tags = []
        record_b.audio_type = "Oneshots"
        record_b.source_path = Path("Source/b.wav")
        record_b.confidence = "0.90"
        record_b.evidence = {}
        record_b.is_manual = False
        record_b.is_preserved = False

        undo_stack = QUndoStack()
        source_model = StagingTableModel([record_a, record_b], undo_stack=undo_stack, sync_callback=None)
        proxy_model = MultiFilterProxyModel()
        proxy_model.setSourceModel(source_model)

        window = QMainWindow()
        window.undo_stack = undo_stack
        view = StagingTableView()
        view.setModel(proxy_model)
        window.setCentralWidget(view)

        try:
            view.fill_start_idx = proxy_model.index(0, StagingColumn.CATEGORY)
            view.current_drag_row = 1
            view.is_filling = True

            release_event = mock.Mock()
            view.mouseReleaseEvent(release_event)

            self.assertEqual(record_a.category, "Kicks")
            self.assertEqual(record_b.category, "Kicks")
            self.assertEqual(undo_stack.count(), 1)
        finally:
            window.close()

    def test_pack_drag_fill_allows_fill_when_candidate_metadata_is_missing(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance() or QApplication([])

        first = mock.Mock(spec=PlanRecord)
        first.pack = "Pack A"
        first.category = "Kicks"
        first.subcategory = None
        first.tags = []
        first.audio_type = "Oneshots"
        first.source_path = Path("Source/a.wav")
        first.confidence = "0.90"
        first.evidence = {}
        first.is_manual = False
        first.is_preserved = False
        first.pack_candidates = [("Pack A", 1.0)]

        second = mock.Mock(spec=PlanRecord)
        second.pack = "Pack B"
        second.category = "Kicks"
        second.subcategory = None
        second.tags = []
        second.audio_type = "Oneshots"
        second.source_path = Path("Source/b.wav")
        second.confidence = "0.90"
        second.evidence = {}
        second.is_manual = False
        second.is_preserved = False
        second.pack_candidates = []

        source_model = StagingTableModel([first, second], undo_stack=None, sync_callback=None)
        proxy_model = MultiFilterProxyModel()
        proxy_model.setSourceModel(source_model)
        view = StagingTableView()
        view.setModel(proxy_model)
        view.fill_start_idx = proxy_model.index(0, StagingColumn.PACK)
        view.is_filling = True
        view.current_drag_row = 0

        move_event = mock.Mock()
        move_event.position.return_value.toPoint.return_value = view.visualRect(proxy_model.index(1, StagingColumn.PACK)).center()
        view.mouseMoveEvent(move_event)

        self.assertEqual(view.current_drag_row, 1)

    def test_plain_drag_selects_cell_rectangle_without_ctrl_export(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance() or QApplication([])

        records = []
        for name in ("a", "b", "c", "d"):
            record = mock.Mock(spec=PlanRecord)
            record.pack = "Pack"
            record.category = "Kicks"
            record.subcategory = None
            record.tags = []
            record.audio_type = "Oneshots"
            record.source_path = Path(f"Source/{name}.wav")
            record.confidence = "0.90"
            record.evidence = {}
            record.is_manual = False
            record.is_preserved = False
            record.pack_candidates = []
            records.append(record)

        source_model = StagingTableModel(records, undo_stack=None, sync_callback=None)
        proxy_model = MultiFilterProxyModel()
        proxy_model.setSourceModel(source_model)
        view = StagingTableView()
        view.setModel(proxy_model)
        view._selection_drag_anchor = proxy_model.index(0, StagingColumn.PACK)

        view._select_drag_range(proxy_model.index(0, StagingColumn.PACK), proxy_model.index(2, StagingColumn.CATEGORY))

        self.assertEqual(
            sorted((index.row(), index.column()) for index in view.selectionModel().selectedIndexes()),
            [(0, 0), (0, 1), (0, 2), (1, 0), (1, 1), (1, 2), (2, 0), (2, 1), (2, 2)],
        )

    def test_ctrl_drag_adds_cell_rectangle_to_existing_selection(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance() or QApplication([])

        records = []
        for name in ("a", "b", "c", "d"):
            record = mock.Mock(spec=PlanRecord)
            record.pack = "Pack"
            record.category = "Kicks"
            record.subcategory = None
            record.tags = []
            record.audio_type = "Oneshots"
            record.source_path = Path(f"Source/{name}.wav")
            record.confidence = "0.90"
            record.evidence = {}
            record.is_manual = False
            record.is_preserved = False
            record.pack_candidates = []
            records.append(record)

        source_model = StagingTableModel(records, undo_stack=None, sync_callback=None)
        proxy_model = MultiFilterProxyModel()
        proxy_model.setSourceModel(source_model)
        view = StagingTableView()
        view.setModel(proxy_model)
        existing = proxy_model.index(3, StagingColumn.TAGS)
        view.selectionModel().select(existing, QItemSelectionModel.Select)

        view._select_drag_range(
            proxy_model.index(0, StagingColumn.PACK),
            proxy_model.index(1, StagingColumn.CATEGORY),
            additive=True,
        )

        self.assertEqual(
            sorted((index.row(), index.column()) for index in view.selectionModel().selectedIndexes()),
            [(0, 0), (0, 1), (0, 2), (1, 0), (1, 1), (1, 2), (3, 4)],
        )

    def test_export_drag_packages_selected_rows_when_ctrl_dragging_selected_cell(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance() or QApplication([])

        records = []
        for name in ("a", "b"):
            record = mock.Mock(spec=PlanRecord)
            record.pack = "Pack"
            record.category = "Kicks"
            record.subcategory = None
            record.tags = []
            record.audio_type = "Oneshots"
            record.source_path = Path(f"Source/{name}.wav")
            record.confidence = "0.90"
            record.evidence = {}
            record.is_manual = False
            record.is_preserved = False
            record.pack_candidates = []
            records.append(record)

        source_model = StagingTableModel(records, undo_stack=None, sync_callback=None)
        proxy_model = MultiFilterProxyModel()
        proxy_model.setSourceModel(source_model)
        view = StagingTableView()
        view.setModel(proxy_model)
        view.selectionModel().select(proxy_model.index(0, StagingColumn.PACK), QItemSelectionModel.Select)
        view.selectionModel().select(proxy_model.index(1, StagingColumn.CATEGORY), QItemSelectionModel.Select)
        captured = {}

        class FakeDrag:
            def __init__(self, parent):
                self.parent = parent

            def setMimeData(self, mime):
                captured["urls"] = [url.toLocalFile() for url in mime.urls()]

            def exec(self, *args):
                captured["exec"] = args

        with mock.patch("gui.views.staging_table.QApplication.keyboardModifiers", return_value=Qt.ControlModifier), mock.patch(
            "gui.views.staging_table.QDrag", FakeDrag
        ):
            view.startDrag(Qt.CopyAction)

        self.assertEqual(len(captured["urls"]), 2)
        self.assertTrue(captured["urls"][0].endswith("Source\\a.wav") or captured["urls"][0].endswith("Source/a.wav"))
        self.assertTrue(captured["urls"][1].endswith("Source\\b.wav") or captured["urls"][1].endswith("Source/b.wav"))
        self.assertIn("exec", captured)

    def test_read_only_library_tree_plain_drag_exports_files(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        from gui.models.library_tree import NODE_TYPE_ROLE
        from gui.views.library_tree import LibraryTreeView

        app = QApplication.instance() or QApplication([])
        view = LibraryTreeView()
        view.set_read_only_discovery(True)
        index = mock.Mock()
        index.data.side_effect = lambda role=None: "file" if role == NODE_TYPE_ROLE else None
        captured = {}

        class FakeDrag:
            def __init__(self, parent):
                self.parent = parent

            def setMimeData(self, mime):
                captured["urls"] = [url.toLocalFile() for url in mime.urls()]

            def setPixmap(self, pixmap):
                captured["pixmap"] = pixmap

            def exec(self, *args):
                captured["exec"] = args

        with mock.patch.object(view, "_selected_source_indexes", return_value=[index]), \
             mock.patch.object(view, "_encode_source_indexes", return_value=b"idx"), \
             mock.patch.object(view, "_build_export_urls", return_value=[QUrl.fromLocalFile(str(Path("Source/a.wav").absolute()))]), \
             mock.patch("gui.views.library_tree.QApplication.keyboardModifiers", return_value=Qt.NoModifier), \
             mock.patch("gui.views.library_tree.QDrag", FakeDrag):
            view.startDrag(Qt.CopyAction)

        self.assertEqual(len(captured["urls"]), 1)
        self.assertTrue(captured["urls"][0].endswith("Source\\a.wav") or captured["urls"][0].endswith("Source/a.wav"))
        self.assertEqual(captured["exec"], (Qt.CopyAction | Qt.MoveAction, Qt.CopyAction))

    def test_library_table_can_show_type_and_path_columns(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        from PySide6.QtGui import QUndoStack
        from gui.widgets.library_tab import LibraryTab

        app = QApplication.instance() or QApplication([])
        source_model = StagingTableModel([], undo_stack=None, sync_callback=None)
        proxy_model = MultiFilterProxyModel()
        proxy_model.setSourceModel(source_model)
        tab = LibraryTab(QUndoStack())
        try:
            tab.set_proxy_model(proxy_model)
            tab.set_column_visible(StagingColumn.TYPE, True)
            tab.set_column_visible(StagingColumn.PATH, True)

            self.assertFalse(tab.view_table.isColumnHidden(StagingColumn.TYPE))
            self.assertFalse(tab.view_table.isColumnHidden(StagingColumn.PATH))
            self.assertIn(StagingColumn.TYPE, tab._visible_table_columns())
            self.assertIn(StagingColumn.PATH, tab._visible_table_columns())
        finally:
            from PySide6.QtCore import QSettings
            settings = QSettings("UmU", "Unshuffle")
            settings.remove("table_column_visible_TYPE")
            settings.remove("table_column_visible_user_set_TYPE")
            settings.remove("table_column_visible_PATH")
            settings.remove("table_column_visible_user_set_PATH")
            settings.sync()
            tab.deleteLater()

    def test_library_context_opposite_type_helper_requires_single_audio_type(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        from PySide6.QtGui import QUndoStack
        from gui.widgets.library_tab import LibraryTab

        app = QApplication.instance() or QApplication([])
        tab = LibraryTab(QUndoStack())
        try:
            loops = [mock.Mock(audio_type="Loops"), mock.Mock(audio_type="Loops")]
            oneshots = [mock.Mock(audio_type="Oneshots")]
            mixed = [mock.Mock(audio_type="Loops"), mock.Mock(audio_type="Oneshots")]

            self.assertEqual(tab._opposite_audio_type_for_records(loops), "Oneshots")
            self.assertEqual(tab._opposite_audio_type_for_records(oneshots), "Loops")
            self.assertEqual(tab._opposite_audio_type_for_records(mixed), "")
        finally:
            tab.deleteLater()


class TreeShortcutTests(unittest.TestCase):
    def test_ctrl_f_requests_global_search_focus(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        from gui.views.library_tree import LibraryTreeView

        app = QApplication.instance() or QApplication([])
        view = LibraryTreeView()
        focus_mock = mock.Mock()
        view.focus_search_requested.connect(focus_mock)

        event = QKeyEvent(QKeyEvent.KeyPress, Qt.Key_F, Qt.ControlModifier)
        view.keyPressEvent(event)

        focus_mock.assert_called_once_with()

    def test_ctrl_shift_f_keeps_quick_filter_behavior(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        from gui.views.library_tree import LibraryTreeView

        app = QApplication.instance() or QApplication([])
        view = LibraryTreeView()
        view._quick_filter_query_for_index = mock.Mock(return_value='cat:"Kicks"')
        filter_mock = mock.Mock()
        view.quick_filter_requested.connect(filter_mock)

        event = QKeyEvent(QKeyEvent.KeyPress, Qt.Key_F, Qt.ControlModifier | Qt.ShiftModifier)
        view.keyPressEvent(event)

        filter_mock.assert_called_once_with('cat:"Kicks"', "or")


class MainWindowShortcutTests(unittest.TestCase):
    def test_main_window_registers_undo_and_redo_shortcuts(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        from gui.main.launcher import ModernApp

        app = QApplication.instance() or QApplication([])
        window = ModernApp()
        try:
            undo_shortcuts = {seq.toString() for seq in window.act_undo.shortcuts()}
            redo_shortcuts = {seq.toString() for seq in window.act_redo.shortcuts()}

            self.assertTrue(any(seq in undo_shortcuts for seq in {"Ctrl+Z", "Undo"}))
            self.assertIn("Ctrl+Y", redo_shortcuts)
            self.assertIn("Ctrl+Shift+Z", redo_shortcuts)
        finally:
            if getattr(window, "engine", None):
                try:
                    window.engine.close()
                except Exception:
                    pass
            window.close()
