import os
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest import mock

from PySide6.QtCore import QObject, Signal

from gui.core.search_controller import SearchController
from gui.core.search_engine import SearchEngine
from gui.core.workers import SearchWorker
from gui.models.proxy import MultiFilterProxyModel
from gui.models.staging_table import StagingTableModel
from unshuffle.core import PlanRecord


class SearchControllerFooterCountTests(unittest.TestCase):
    def test_search_finished_uses_visible_proxy_rows_for_footer_count(self):
        proxy_model = mock.Mock()
        proxy_model.rowCount.return_value = 7

        class _Parent(QObject):
            def __init__(self):
                super().__init__()
                self.footer = mock.Mock()
                self.view_controller = mock.Mock()

        parent = _Parent()

        controller = SearchController(engine=None, model=None, proxy_model=proxy_model, parent=parent)
        controller.on_search_finished_logic(
            {
                "request_id": 4,
                "query_text": "",
                "matched_ids": [1, 2, 3, 4, 5, 6, 7],
            }
        )

        parent.view_controller.update_footer_count.assert_called_once_with()
        parent.view_controller.schedule_tree_rebuild.assert_called_once_with(0)
        parent.footer.set_count.assert_not_called()


class AcousticControllerFooterCountTests(unittest.TestCase):
    def test_clear_vibe_refreshes_visible_row_count(self):
        from gui.core.acoustic_controller import AcousticController

        class _Parent(QObject):
            def __init__(self):
                super().__init__()
                self.view_controller = mock.Mock()

        parent = _Parent()
        proxy_model = mock.Mock()

        controller = AcousticController(model=None, proxy_model=proxy_model, parent=parent)
        controller.clear_vibe()

        proxy_model.clear_similarity.assert_called_once_with()
        parent.view_controller.update_library_views.assert_called_once_with(tree_delay_ms=0)

    def test_anchor_similarity_uses_background_worker_result(self):
        from gui.core.acoustic_controller import AcousticController

        class _Parent(QObject):
            def __init__(self):
                super().__init__()
                self.view_controller = mock.Mock()

        class _FakeWorker(QObject):
            finished = Signal(dict)
            error = Signal(str)

            def __init__(self, request_id, anchor_row, anchor_blob, anchor_duration, candidates):
                super().__init__()
                self.request_id = request_id
                self.anchor_row = anchor_row
                self.candidates = candidates

            def start(self):
                self.finished.emit(
                    {
                        "request_id": self.request_id,
                        "anchor_row": self.anchor_row,
                        "distances": {self.anchor_row: 0.0, self.candidates[-1][0]: 0.5},
                        "avg_dist": 0.5,
                    }
                )

            def deleteLater(self):
                return None

        parent = _Parent()
        proxy_model = mock.Mock()
        model = mock.Mock()
        anchor = mock.Mock()
        anchor.category = "Kicks"
        anchor.audio_type = "Oneshots"
        anchor.acoustic_vector = b"anchor"
        anchor.duration = 0.4
        anchor.source_path = Path("Source/kick.wav")
        other = mock.Mock()
        other.category = "Kicks"
        other.audio_type = "Oneshots"
        other.acoustic_vector = b"other"
        other.duration = 0.6
        model.records = [anchor, other]

        controller = AcousticController(model=model, proxy_model=proxy_model, parent=parent)

        with mock.patch("gui.core.workers.SimilarityWorker", _FakeWorker):
            controller.anchor_similarity(anchor)

        proxy_model.set_matched_ids.assert_called_with(None)
        proxy_model.set_similarity_data.assert_called_once_with({0: 0.0, 1: 0.5}, 0.5, 0)
        parent.view_controller.update_library_views.assert_called_with(tree_delay_ms=0)

    def test_anchor_similarity_scopes_candidates_to_same_type_and_category(self):
        from gui.core.acoustic_controller import AcousticController

        class _Parent(QObject):
            def __init__(self):
                super().__init__()
                self.view_controller = mock.Mock()

        captured = {}

        class _FakeWorker(QObject):
            finished = Signal(dict)
            error = Signal(str)

            def __init__(self, request_id, anchor_row, anchor_blob, anchor_duration, candidates):
                super().__init__()
                captured["candidates"] = candidates
                self.request_id = request_id
                self.anchor_row = anchor_row

            def start(self):
                self.finished.emit(
                    {
                        "request_id": self.request_id,
                        "anchor_row": self.anchor_row,
                        "distances": {0: 0.0, 1: 0.5},
                        "avg_dist": 0.5,
                    }
                )

            def deleteLater(self):
                return None

        anchor = mock.Mock(category="Kicks", audio_type="Oneshots", acoustic_vector=b"anchor", duration=0.4)
        anchor.source_path = Path("Source/kick.wav")
        same_bucket = mock.Mock(category="Kicks", audio_type="Oneshots", acoustic_vector=b"same", duration=0.5)
        same_category_wrong_type = mock.Mock(category="Kicks", audio_type="Loops", acoustic_vector=b"loop", duration=2.0)
        wrong_category = mock.Mock(category="Snares", audio_type="Oneshots", acoustic_vector=b"snare", duration=0.3)
        model = mock.Mock(records=[anchor, same_bucket, same_category_wrong_type, wrong_category])
        proxy_model = mock.Mock()
        controller = AcousticController(model=model, proxy_model=proxy_model, parent=_Parent())

        with mock.patch("gui.core.workers.SimilarityWorker", _FakeWorker):
            controller.anchor_similarity(anchor)

        self.assertEqual([row for row, _blob, _duration in captured["candidates"]], [0, 1])

    def test_anchor_similarity_keeps_category_filter_while_vibe_is_active(self):
        from gui.core.acoustic_controller import AcousticController

        class _Parent(QObject):
            def __init__(self):
                super().__init__()
                self.view_controller = mock.Mock()
                self.search_controller = mock.Mock()
                self.search_controller.current_query = 'category:"Bass" AND name:"808"'

        class _FakeWorker(QObject):
            finished = Signal(dict)
            error = Signal(str)

            def __init__(self, request_id, anchor_row, anchor_blob, anchor_duration, candidates):
                super().__init__()
                self.request_id = request_id
                self.anchor_row = anchor_row
                self.candidates = candidates

            def start(self):
                self.finished.emit(
                    {
                        "request_id": self.request_id,
                        "anchor_row": self.anchor_row,
                        "distances": {self.anchor_row: 0.0, self.candidates[-1][0]: 0.5},
                        "avg_dist": 0.5,
                    }
                )

            def deleteLater(self):
                return None

        parent = _Parent()
        proxy_model = mock.Mock()
        anchor = mock.Mock(category="Bass", audio_type="Oneshots", acoustic_vector=b"anchor", duration=0.4)
        anchor.source_path = Path("Source/bass.wav")
        other = mock.Mock(category="Bass", audio_type="Oneshots", acoustic_vector=b"other", duration=0.5)
        other.source_path = Path("Source/other.wav")
        model = mock.Mock(records=[anchor, other])
        controller = AcousticController(model=model, proxy_model=proxy_model, parent=parent)

        with mock.patch("gui.core.workers.SimilarityWorker", _FakeWorker):
            controller.anchor_similarity(anchor)

        parent.search_controller.clear_query_state.assert_not_called()
        parent.search_controller.set_query.assert_called_once_with('category:"Bass"', immediate=True)

        controller.clear_vibe()

        parent.search_controller.set_query.assert_any_call('category:"Bass" AND name:"808"', immediate=True)

    def test_anchor_similarity_restores_query_when_no_candidates_are_available(self):
        from gui.core.acoustic_controller import AcousticController

        class _Parent(QObject):
            def __init__(self):
                super().__init__()
                self.view_controller = mock.Mock()
                self.search_controller = mock.Mock()
                self.search_controller.current_query = 'category:"Bass" AND name:"808"'

        parent = _Parent()
        proxy_model = mock.Mock()
        anchor = mock.Mock(category="Bass", audio_type="Oneshots", acoustic_vector=b"anchor", duration=0.4)
        anchor.source_path = Path("Source/bass.wav")
        model = mock.Mock(records=[])
        controller = AcousticController(model=model, proxy_model=proxy_model, parent=parent)

        controller.anchor_similarity(anchor)

        parent.search_controller.set_query.assert_any_call('category:"Bass"', immediate=True)
        parent.search_controller.set_query.assert_any_call('category:"Bass" AND name:"808"', immediate=True)

    def test_similarity_request_ignores_stale_proxy_index(self):
        from PySide6.QtCore import QModelIndex
        from gui.core.acoustic_controller import AcousticController

        model = mock.Mock()
        model.records = []
        proxy_model = mock.Mock()
        proxy_model.mapToSource.return_value = QModelIndex()
        parent = QObject()
        controller = AcousticController(model=model, proxy_model=proxy_model, parent=parent)

        self.assertIsNone(controller.handle_similarity_request(QModelIndex()))


class AudioControllerSafetyTests(unittest.TestCase):
    def test_play_request_ignores_stale_proxy_index(self):
        from PySide6.QtCore import QModelIndex
        from PySide6.QtWidgets import QApplication, QWidget
        from gui.core.audio_controller import AudioController

        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        _app = QApplication.instance() or QApplication([])
        audio_bar = QWidget()
        audio_bar.TARGET_HEIGHT = 48
        parent = QObject()
        controller = AudioController(audio_bar, parent=parent)
        controller.player = mock.Mock()
        proxy_model = mock.Mock()
        proxy_model.mapToSource.return_value = QModelIndex()
        model = mock.Mock(records=[])

        controller.handle_play_request(QModelIndex(), model, proxy_model)

        controller.player.play.assert_not_called()

    def test_audio_bar_does_not_expand_when_player_rejects_path(self):
        from PySide6.QtWidgets import QApplication, QWidget
        from gui.core.audio_controller import AudioController

        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        _app = QApplication.instance() or QApplication([])
        audio_bar = QWidget()
        audio_bar.TARGET_HEIGHT = 48
        parent = QObject()
        controller = AudioController(audio_bar, parent=parent)
        controller.player = mock.Mock()
        controller.player.is_playing.return_value = False
        controller.player.play.return_value = False
        controller.toggle_audio_bar = mock.Mock()

        controller.play_path(Path("missing.wav"))

        controller.toggle_audio_bar.assert_not_called()

    def test_preview_bar_dragout_exports_current_player_path(self):
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication
        from gui.widgets.preview_control_bar import PreviewControlBar

        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        _app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as temp_dir:
            audio_path = Path(temp_dir) / "sample.wav"
            audio_path.write_bytes(b"sound")
            bar = PreviewControlBar()
            bar.player.current_path = audio_path
            captured = {}

            class FakeDrag:
                def __init__(self, parent):
                    self.parent = parent

                def setMimeData(self, mime):
                    captured["urls"] = [url.toLocalFile() for url in mime.urls()]

                def exec(self, *args):
                    captured["exec"] = args

            with mock.patch("gui.widgets.preview_control_bar.QDrag", FakeDrag):
                self.assertTrue(bar.btn_dragout.start_export_drag())

            self.assertEqual(Path(captured["urls"][0]), audio_path.absolute())
            self.assertEqual(captured["exec"], (Qt.CopyAction | Qt.MoveAction, Qt.CopyAction))

    def test_preview_bar_dragout_ignores_missing_current_path(self):
        from PySide6.QtWidgets import QApplication
        from gui.widgets.preview_control_bar import PreviewControlBar

        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        _app = QApplication.instance() or QApplication([])
        bar = PreviewControlBar()
        bar.player.current_path = None

        with mock.patch("gui.widgets.preview_control_bar.QDrag") as drag:
            self.assertFalse(bar.btn_dragout.start_export_drag())

        drag.assert_not_called()


class SearchEngineProxyOnlyFilterTests(unittest.TestCase):
    def test_confidence_only_query_skips_db_search(self):
        fake_db = mock.Mock()
        engine = SimpleNamespace(db=fake_db, session_id="session-1")

        result = SearchEngine.run_query(engine, 'confidence:"25-100"')

        self.assertIsNone(result)
        fake_db.search_staging.assert_not_called()

    def test_database_term_detection_ignores_proxy_only_confidence_filter(self):
        self.assertFalse(SearchEngine.has_database_terms('confidence:"25-100"'))
        self.assertFalse(SearchEngine.has_database_terms('conf:">80"'))
        self.assertTrue(SearchEngine.has_database_terms('confidence:"25-100", category:"Kicks"'))


class SearchWorkerConstructorTests(unittest.TestCase):
    def test_search_worker_requires_bridge_constructor_contract(self):
        with self.assertRaises(TypeError):
            cast(Any, SearchWorker)(1, engine=object())


class IconRenderingTests(unittest.TestCase):
    def test_animated_icon_button_defaults_to_theme_gray_tint(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        from gui.widgets.buttons import AnimatedIconButton
        from gui.utils.styles import ColorPalette

        _app = QApplication.instance() or QApplication([])
        button = AnimatedIconButton("icons/table.png")
        self.assertEqual(button.color.name().lower(), ColorPalette.TEXT_GRAY.lower())

    def test_animated_icon_button_resolves_configured_asset_root(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        from gui.widgets.buttons import AnimatedIconButton

        _app = QApplication.instance() or QApplication([])
        old_asset_root = os.environ.get("UNSHUFFLE_ASSET_ROOT")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / "icons").mkdir()
                shutil.copy2(Path("../icons") / "table.png", root / "icons" / "table.png")
                os.environ["UNSHUFFLE_ASSET_ROOT"] = str(root)

                button = AnimatedIconButton("icons/table.png")

                self.assertEqual(Path(button.icon_path), root / "icons" / "table.png")
                self.assertIsNotNone(button._get_cached_icon())
        finally:
            if old_asset_root is None:
                os.environ.pop("UNSHUFFLE_ASSET_ROOT", None)
            else:
                os.environ["UNSHUFFLE_ASSET_ROOT"] = old_asset_root

    def test_animated_icon_button_preserves_absolute_icon_paths(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        from gui.utils.constants import UNDO_ICON
        from gui.widgets.buttons import AnimatedIconButton

        _app = QApplication.instance() or QApplication([])
        button = AnimatedIconButton(UNDO_ICON)

        self.assertEqual(Path(button.icon_path), UNDO_ICON)
        self.assertIsNotNone(button._get_cached_icon())


class SearchControllerConfidenceFilterTests(unittest.TestCase):
    def test_and_filter_is_rendered_as_word_for_readability(self):
        controller = SearchController(engine=None, model=None, proxy_model=mock.Mock(), parent=None)
        controller._current_query = 'category:"Kicks"'
        controller.set_query = mock.Mock()

        controller.apply_filter('tag:"warm"', True, mode="and")

        controller.set_query.assert_called_once_with('category:"Kicks" AND tag:"warm"')

    def test_removing_filter_preserves_readable_and_separator(self):
        from gui.core.filter_query import remove_filter_query

        self.assertEqual(
            remove_filter_query('category:"Kicks" AND tag:"warm" AND type:"Oneshots"', 'tag:"warm"'),
            'category:"Kicks" AND type:"Oneshots"',
        )

    def test_confidence_filter_uses_readable_and_separator(self):
        controller = SearchController(engine=None, model=None, proxy_model=mock.Mock(), parent=None)
        controller._current_query = 'category:"Kicks"'
        controller.set_query = mock.Mock()

        controller.set_confidence_range(0.25, 1.0)

        controller.set_query.assert_called_once_with('category:"Kicks" AND confidence:"25-100"')

    def test_search_sync_preserves_trailing_operator_space_while_composing(self):
        from gui.main.window_search import sync_search_ui_state

        class _Edit:
            def __init__(self):
                self._text = 'category:"Kicks" AND '
                self.blocked = []

            def text(self):
                return self._text

            def blockSignals(self, blocked):
                self.blocked.append(blocked)

            def setText(self, text):
                self._text = text

        edit = _Edit()
        library_tab = SimpleNamespace(
            edit_search=edit,
            _refresh_search_button_state=mock.Mock(),
            set_active_saved_filters=mock.Mock(),
            set_active_source_filters=mock.Mock(),
            category_carousel=SimpleNamespace(set_active_values=mock.Mock()),
            signal_floor_control=SimpleNamespace(set_range=mock.Mock()),
            sync_map_filters=mock.Mock(),
        )
        window = SimpleNamespace(
            library_tab=library_tab,
            sync_type_filter_state=mock.Mock(),
        )

        sync_search_ui_state(
            window,
            query='category:"Kicks" AND',
            active_saved_filters=set(),
            active_source_filters=set(),
            active_categories=set(),
            confidence_range=(0.0, 1.0),
        )

        self.assertEqual(edit.text(), 'category:"Kicks" AND ')
        self.assertEqual(edit.blocked, [])

    def test_clear_query_state_skips_library_page_state_while_restore_applies(self):
        class _Parent(QObject):
            def __init__(self):
                super().__init__()
                self._restoring_library_page_state = True
                self.save_library_page_state = mock.Mock()

        parent = _Parent()
        controller = SearchController(engine=None, model=None, proxy_model=mock.Mock(), parent=parent)

        controller.clear_query_state(sync_ui=True)

        parent.save_library_page_state.assert_not_called()

    def test_confidence_only_result_clears_stale_matched_ids(self):
        class _Parent(QObject):
            def __init__(self):
                super().__init__()
                self.footer = mock.Mock()
                self.view_controller = mock.Mock()

        parent = _Parent()
        proxy_model = mock.Mock()
        proxy_model.rowCount.return_value = 5
        model = mock.Mock()
        model.scores = {}
        controller = SearchController(
            engine=SimpleNamespace(db=mock.Mock()),
            model=model,
            proxy_model=proxy_model,
            parent=parent,
        )
        controller._current_query = 'confidence:"25-100"'
        controller.searchFinished.connect(controller.on_search_finished_logic)

        with mock.patch("gui.core.workers.SearchWorker") as worker_cls:
            controller.execute_search()

        worker_cls.assert_not_called()
        proxy_model.set_matched_ids.assert_called_once_with(None)
        parent.view_controller.update_footer_count.assert_called()

    def test_search_worker_error_surfaces_status_without_false_empty_results(self):
        class _Parent(QObject):
            def __init__(self):
                super().__init__()
                self.set_search_status = mock.Mock()
                self.view_controller = mock.Mock()

        class _FakeWorker(QObject):
            finished = Signal(dict)
            error = Signal(str)

            def __init__(self, request_id, engine, query_text):
                super().__init__()
                self.request_id = request_id

            def start(self):
                self.error.emit("FTS standard search failed: malformed MATCH expression")

            def deleteLater(self):
                return None

        parent = _Parent()
        proxy_model = mock.Mock()
        model = mock.Mock()
        controller = SearchController(
            engine=SimpleNamespace(db=mock.Mock()),
            model=model,
            proxy_model=proxy_model,
            parent=parent,
        )
        controller._current_query = 'category:"Kicks"'

        with mock.patch("gui.core.workers.SearchWorker", _FakeWorker):
            controller.execute_search()

        parent.set_search_status.assert_called_once_with(
            "Search Error: FTS standard search failed: malformed MATCH expression"
        )
        proxy_model.set_matched_ids.assert_called_once_with(set())
        model.clear_similarity_scores.assert_not_called()
        parent.view_controller.update_footer_count.assert_not_called()


class SearchControllerSemanticSuggestionTests(unittest.TestCase):
    def test_plain_search_suggests_semantic_pack_filter_when_all_matches_agree(self):
        records = [
            PlanRecord(Path("D:/Samples/Aden/kick.wav"), "Aden", "Kicks", "Oneshots", "0.9", staging_row_id=10),
            PlanRecord(Path("D:/Samples/Aden/snare.wav"), "Aden Vol 2", "Snares", "Oneshots", "0.8", staging_row_id=11),
        ]
        model = mock.Mock()
        model.records = records
        model.record_id.side_effect = lambda row: records[row].staging_row_id
        controller = SearchController(engine=None, model=model, proxy_model=mock.Mock(), parent=None)

        suggestion = controller._semantic_filter_suggestion({"query_text": "Aden", "matched_ids": [10, 11]})

        self.assertEqual(suggestion, ("Aden", 'packname:"Aden"'))

    def test_plain_search_does_not_suggest_when_matches_support_multiple_fields(self):
        records = [
            PlanRecord(
                Path("D:/Samples/Aden/kick.wav"),
                "Aden",
                "Aden Category",
                "Oneshots",
                "0.9",
                staging_row_id=10,
            ),
        ]
        model = mock.Mock()
        model.records = records
        model.record_id.return_value = 10
        controller = SearchController(engine=None, model=model, proxy_model=mock.Mock(), parent=None)

        suggestion = controller._semantic_filter_suggestion({"query_text": "Aden", "matched_ids": [10]})

        self.assertIsNone(suggestion)

    def test_prefixed_search_does_not_suggest_semantic_conversion(self):
        records = [PlanRecord(Path("D:/Samples/Aden/kick.wav"), "Aden", "Kicks", "Oneshots", "0.9", staging_row_id=10)]
        model = mock.Mock()
        model.records = records
        model.record_id.return_value = 10
        controller = SearchController(engine=None, model=model, proxy_model=mock.Mock(), parent=None)

        suggestion = controller._semantic_filter_suggestion({"query_text": 'packname:"Aden"', "matched_ids": [10]})

        self.assertIsNone(suggestion)


class LibrarySearchSuggestionTests(unittest.TestCase):
    def test_library_search_bar_uses_saved_filters_and_record_values(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtGui import QUndoStack
        from PySide6.QtWidgets import QApplication
        from gui.widgets.filter_suggestion_line_edit import FilterSuggestionLineEdit
        from gui.widgets.library_tab import LibraryTab

        _app = QApplication.instance() or QApplication([])
        record = PlanRecord(
            Path("D:/Samples/Aden/kick.wav"),
            "ADEN Massamolla",
            "Kicks",
            "Oneshots",
            "0.9",
            tags=["warm"],
            staging_row_id=10,
        )
        model = StagingTableModel([record], QUndoStack())
        proxy = MultiFilterProxyModel()
        proxy.setSourceModel(model)
        tab = LibraryTab(QUndoStack())
        tab.set_proxy_model(proxy)
        tab.set_saved_filters([{"name": "Warm kicks", "query": 'category:"Kicks" tag:"warm"'}])

        self.assertIsInstance(tab.edit_search, FilterSuggestionLineEdit)
        self.assertIn('category:"Kicks" tag:"warm"', tab.edit_search._saved_filter_suggestions)
        self.assertIn('packname:"ADEN Massamolla"', tab.edit_search._suggestions)
        self.assertIn('tag:"warm"', tab.edit_search._matching_suggestions("warm"))
