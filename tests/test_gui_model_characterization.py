import os
import unittest
from pathlib import Path
from unittest import mock
from typing import Any, cast

from PySide6.QtCore import QModelIndex, QRect, Qt
from PySide6.QtWidgets import QLineEdit, QStyleOptionViewItem, QStyledItemDelegate

from gui.core.data_manager import DataManager
from gui.models.library_tree import LibraryTreeModel, build_tree_payload
from gui.models.proxy import MultiFilterProxyModel
from gui.models.staging_table import StagingTableModel
from gui.utils.constants import StagingColumn
from unshuffle.core import LibNode, NodeType, PlanRecord
from unshuffle.logic.classification import classify_node, reset_scoring_engine


class StagingTableModelTests(unittest.TestCase):
    def test_reconstruct_plan_records_restores_pack_candidates(self):
        manager = DataManager()
        records = manager.reconstruct_plan_records(
            [
                {
                    "source_path": "Source/kick.wav",
                    "pack": "Pack A",
                    "category": "Kicks",
                    "subcategory": "",
                    "audio_type": "Oneshots",
                    "confidence": "0.90",
                    "duration": 0.2,
                    "hash": "hash-a",
                    "tags": "[]",
                    "pack_candidates": '[["Pack A", 1.0], ["Source", 0.7]]',
                    "acoustic_vector": None,
                }
            ]
        )

        self.assertEqual(records[0].pack_candidates, [("Pack A", 1.0), ("Source", 0.7)])

    def test_reconstruct_plan_records_restores_classification_evidence(self):
        manager = DataManager()
        records = manager.reconstruct_plan_records(
            [
                {
                    "source_path": "Source/kick.wav",
                    "pack": "Pack A",
                    "category": "Kicks",
                    "subcategory": "",
                    "audio_type": "Oneshots",
                    "confidence": "0.90",
                    "duration": 0.2,
                    "hash": "hash-a",
                    "tags": "[]",
                    "pack_candidates": "[]",
                    "evidence_json": '{"stage": "final", "raw": {"Kicks": 1.2}}',
                }
            ]
        )

        self.assertEqual(records[0].evidence["stage"], "final")
        self.assertEqual(records[0].evidence["raw"]["Kicks"], 1.2)

    def test_imported_metadata_rows_are_filtered_by_staging_record_id(self):
        from gui.core.data_manager import filter_imported_metadata_rows, imported_staging_record_ids

        imported_ids = imported_staging_record_ids([
            {"row_id": 7, "source_path": "D:/Samples/kick.wav"},
            {"id": 9, "source_path": "D:/Samples/snare.wav"},
        ])

        self.assertEqual(imported_ids, {"7", "9"})
        self.assertEqual(
            filter_imported_metadata_rows(
                [
                    {"record_id": "7", "value": "kept"},
                    {"record_id": "D:/Samples/kick.wav", "value": "old-path-bug"},
                    {"record_id": "11", "value": "not-imported"},
                ],
                imported_ids,
            ),
            [{"record_id": "7", "value": "kept"}],
        )

    def test_category_and_subcategory_are_shown_in_separate_columns(self):
        record = mock.Mock(spec=PlanRecord)
        record.pack = "Pack A"
        record.category = "Kicks"
        record.subcategory = "Boomy"
        record.tags = []
        record.audio_type = "Oneshots"
        record.source_path = Path("Source/kick.wav")
        record.confidence = "0.90"
        record.evidence = {}
        record.is_manual = False
        record.is_preserved = False

        model = StagingTableModel([record], undo_stack=None, sync_callback=None)

        self.assertEqual(
            model.data(model.index(0, StagingColumn.CATEGORY), Qt.DisplayRole),
            "Kicks",
        )
        self.assertEqual(
            model.data(model.index(0, StagingColumn.SUBCATEGORY), Qt.DisplayRole),
            "Boomy",
        )

    def test_filtered_source_records_skips_invalid_proxy_rows(self):
        from PySide6.QtCore import QModelIndex
        from gui.core.view_modes import filtered_source_records

        model = mock.Mock()
        proxy_model = mock.Mock()
        proxy_model.rowCount.return_value = 2
        invalid = QModelIndex()
        valid_proxy = mock.Mock()
        valid_proxy.isValid.return_value = True
        valid_source = mock.Mock()
        valid_source.isValid.return_value = True
        valid_source.row.return_value = 0
        proxy_model.index.side_effect = [invalid, valid_proxy]
        proxy_model.mapToSource.side_effect = [valid_source]
        model.record.return_value = "record"

        self.assertEqual(filtered_source_records(model, proxy_model), ["record"])

    def test_coherence_math_helpers_tolerate_malformed_inputs(self):
        import numpy as np
        from gui.widgets.coherence_math import _normalize_coords, _vector_signature
        from unshuffle.core.vector_math import calculate_tonalness, cosine_distance

        self.assertIsInstance(_vector_signature(cast(Any, ["bad", float("nan"), 0.25])), int)
        self.assertEqual(_normalize_coords(np.array([]), margin=0.1).shape, (0, 2))
        self.assertEqual(calculate_tonalness(cast(Any, [float("nan"), "bad"])), 0.0)
        self.assertEqual(cosine_distance([1.0, float("inf")], [1.0, 0.0]), float("inf"))

    def test_bulk_updates_do_not_crash_without_undo_stack(self):
        record = mock.Mock(spec=PlanRecord)
        record.pack = "Pack A"
        record.category = "Kicks"
        record.subcategory = None
        record.tags = []
        record.audio_type = "Oneshots"
        record.source_path = Path("Source/kick.wav")
        record.confidence = "0.90"
        record.evidence = {}
        record.is_manual = False
        record.is_preserved = False

        model = StagingTableModel([record], undo_stack=None, sync_callback=None)
        changed = model.apply_bulk_updates([(record, 0, "Pack B")], "Bulk Pack")

        self.assertTrue(changed)
        self.assertEqual(record.pack, "Pack B")

    def test_set_data_uses_draft_callback_when_available(self):
        record = mock.Mock(spec=PlanRecord)
        record.pack = "Pack A"
        record.category = "Kicks"
        record.subcategory = None
        record.tags = []
        record.audio_type = "Oneshots"
        record.source_path = Path("Source/kick.wav")
        record.confidence = "0.90"
        record.evidence = {}
        record.is_manual = False
        record.is_preserved = False

        calls = []
        model = StagingTableModel(
            [record],
            undo_stack=None,
            sync_callback=None,
            draft_edit_callback=lambda rec, col, value: calls.append((rec, col, value)) or True,
        )

        changed = model.setData(model.index(0, StagingColumn.CATEGORY), "Bass", Qt.EditRole)

        self.assertTrue(changed)
        self.assertEqual(calls, [(record, StagingColumn.CATEGORY, "Bass")])
        self.assertEqual(record.category, "Kicks")

    def test_bulk_updates_use_draft_callback_when_available(self):
        record = mock.Mock(spec=PlanRecord)
        record.pack = "Pack A"
        record.category = "Kicks"
        record.subcategory = None
        record.tags = []
        record.audio_type = "Oneshots"
        record.source_path = Path("Source/kick.wav")
        record.confidence = "0.90"
        record.evidence = {}
        record.is_manual = False
        record.is_preserved = False

        calls = []
        model = StagingTableModel(
            [record],
            undo_stack=None,
            sync_callback=None,
            draft_bulk_callback=lambda updates, text: calls.append((updates, text)) or True,
        )

        changed = model.apply_bulk_updates([(record, StagingColumn.PACK, "Pack B")], "Fill Pack")

        self.assertTrue(changed)
        self.assertEqual(calls, [([(record, StagingColumn.PACK, "Pack B")], "Fill Pack")])
        self.assertEqual(record.pack, "Pack A")

    def test_category_edit_is_separate_from_subcategory_and_clears_invalid_sub(self):
        record = mock.Mock(spec=PlanRecord)
        record.pack = "Pack A"
        record.category = "Claps"
        record.subcategory = "Snaps"
        record.tags = []
        record.audio_type = "Oneshots"
        record.source_path = Path("Source/kick.wav")
        record.confidence = "0.90"
        record.evidence = {}
        record.is_manual = False
        record.is_preserved = False

        model = StagingTableModel([record], undo_stack=None, sync_callback=None)
        model.setData(model.index(0, StagingColumn.CATEGORY), "Bass", Qt.EditRole)

        self.assertEqual(record.category, "Bass")
        self.assertIsNone(record.subcategory)

    def test_subcategory_edit_only_accepts_values_from_selected_category(self):
        record = mock.Mock(spec=PlanRecord)
        record.pack = "Pack A"
        record.category = "Claps"
        record.subcategory = None
        record.tags = []
        record.audio_type = "Oneshots"
        record.source_path = Path("Source/kick.wav")
        record.confidence = "0.90"
        record.evidence = {}
        record.is_manual = False
        record.is_preserved = False

        model = StagingTableModel([record], undo_stack=None, sync_callback=None)
        model.setData(model.index(0, StagingColumn.SUBCATEGORY), "Snaps", Qt.EditRole)
        self.assertEqual(record.subcategory, "Snaps")

        model.setData(model.index(0, StagingColumn.SUBCATEGORY), "Not A Real Kick Sub", Qt.EditRole)
        self.assertIsNone(record.subcategory)

    def test_tooltip_uses_stored_trace_without_retokenizing(self):
        record = mock.Mock(spec=PlanRecord)
        record.pack = "Pack A"
        record.category = "Kicks"
        record.subcategory = None
        record.tags = []
        record.audio_type = "Oneshots"
        record.source_path = Path("Source/kick.wav")
        record.confidence = "0.90"
        record.is_manual = False
        record.is_preserved = False
        record.evidence = {
            "stage": "final",
            "raw": {"Kicks": 0.9, "Bass": 0.2},
            "trace": {
                "components": {
                    "filename": {
                        "token_trace": [
                            {
                                "token": "kick",
                                "status": "matched",
                                "matches": [{"category": "Kicks", "weight": 1.0, "specificity": 1.0, "before": 0.0, "after": 1.0, "contribution": 1.0}],
                            }
                        ]
                    },
                    "parent": {"token_trace": []},
                    "pack": {"token_trace": []},
                }
            },
        }

        model = StagingTableModel([record], undo_stack=None, sync_callback=None)

        tooltip = model.data(model.index(0, StagingColumn.CATEGORY), Qt.ToolTipRole)
        tooltip = cast(str, tooltip)
        self.assertIn('"kick"', tooltip)

    def test_tooltip_key_fallback_does_not_claim_context_carried_weight(self):
        runtime = {
            "alias_table": {},
            "noise_words": set(),
            "category_suppression_rules": {},
            "loop_indicators": [],
            "weak_loop_indicators": [],
            "oneshot_indicators": [],
            "oneshot_hint_tokens": [],
            "percussive_categories": [],
            "sub_taxonomy_map": {},
            "default_sub_map": {},
            "model_numbers": set(),
        }
        reset_scoring_engine()
        try:
            node = LibNode(
                path=Path(r"Source/Cymatics - Agony - 115 BPM C Min.wav"),
                name="Cymatics - Agony - 115 BPM C Min.wav",
                node_type=NodeType.FILE,
                extension=".wav",
            )
            category, confidence, evidence = classify_node(node, runtime=runtime)
        finally:
            reset_scoring_engine()

        record = PlanRecord(
            Path("Source/Cymatics - Agony - 115 BPM C Min.wav"),
            "Pharaoh Premium Drum Samples (BETA)",
            category,
            "Loops",
            f"{confidence:.2f}",
            evidence=evidence,
        )
        model = StagingTableModel([record], undo_stack=None, sync_callback=None)

        tooltip = cast(str, model.data(model.index(0, StagingColumn.CATEGORY), Qt.ToolTipRole))

        self.assertIn("musical key was detected", tooltip)
        self.assertNotIn("context carried more weight", tooltip)

    def test_tooltip_names_learned_correction_tokens_for_contextless_category(self):
        record = PlanRecord(
            Path("Source/Cymatics - Agony - 115 BPM C Min.wav"),
            "Pharaoh Premium Drum Samples (BETA)",
            "Percussion",
            "Loops",
            "0.13",
            evidence={
                "stage": "final",
                "raw": {"Percussion": 0.2},
                "trace": {
                    "components": {
                        "filename": {"token_trace": []},
                        "parent": {"token_trace": []},
                        "pack": {"token_trace": []},
                    },
                    "token_adjustments": [
                        {"token": "cymatics", "category": "Percussion", "offset": 0.2},
                    ],
                    "global_boosts": [],
                },
            },
        )
        model = StagingTableModel([record], undo_stack=None, sync_callback=None)

        tooltip = cast(str, model.data(model.index(0, StagingColumn.CATEGORY), Qt.ToolTipRole))

        self.assertIn('Learned Corrections boosted this category from "cymatics"', tooltip)
        self.assertNotIn("context carried more weight", tooltip)

    def test_unique_values_cache_refreshes_after_edit(self):
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

        second = mock.Mock(spec=PlanRecord)
        second.pack = "Pack B"
        second.category = "Snares"
        second.subcategory = None
        second.tags = []
        second.audio_type = "Oneshots"
        second.source_path = Path("Source/b.wav")
        second.confidence = "0.80"
        second.evidence = {}
        second.is_manual = False
        second.is_preserved = False

        model = StagingTableModel([first, second], undo_stack=None, sync_callback=None)
        self.assertEqual(model.get_unique_values(StagingColumn.CATEGORY), ["Kicks", "Snares"])

        model.setData(model.index(1, StagingColumn.CATEGORY), "Claps", Qt.EditRole)

        self.assertEqual(model.get_unique_values(StagingColumn.CATEGORY), ["Claps", "Kicks"])


class TreePayloadTests(unittest.TestCase):
    def test_tree_payload_respects_confidence_floor_for_unlocked_records(self):
        low_conf = mock.Mock(spec=PlanRecord)
        low_conf.audio_type = "Oneshots"
        low_conf.category = "Kicks"
        low_conf.pack = "Pack A"
        low_conf.confidence = "0.10"
        low_conf.is_preserved = False
        low_conf.is_manual = False
        low_conf.source_path = Path("Source/low.wav")

        kept_conf = mock.Mock(spec=PlanRecord)
        kept_conf.audio_type = "Oneshots"
        kept_conf.category = "Snares"
        kept_conf.pack = "Pack B"
        kept_conf.confidence = "0.95"
        kept_conf.is_preserved = False
        kept_conf.is_manual = False
        kept_conf.source_path = Path("Source/high.wav")

        payload = build_tree_payload(
            [low_conf, kept_conf],
            [("audio_type", "type"), ("category", "category"), ("pack", "pack")],
            confidence_floor=0.50,
            confidence_filter_enabled=True,
        )
        payload = cast(dict[str, dict[str, object]], payload)

        self.assertIn("Oneshots", payload)
        oneshots = payload["Oneshots"]
        self.assertIn("Uncategorized", oneshots)
        self.assertIn("Snares", oneshots)

    def test_tree_model_exposes_name_and_info_columns(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        _app = QApplication.instance() or QApplication([])
        record = mock.Mock(spec=PlanRecord)
        record.audio_type = "Oneshots"
        record.category = "Kicks"
        record.pack = "Pack A"
        record.confidence = "0.91"
        record.is_preserved = False
        record.is_manual = False
        record.subcategory = None
        record.tags = ["124bpm", "f#m"]
        record.source_path = Path("Source/kick.wav")

        model = LibraryTreeModel()
        model.rebuild([record])

        self.assertEqual(model.columnCount(), 2)
        self.assertEqual(model.headerData(0, Qt.Horizontal), "Name")
        self.assertEqual(model.headerData(1, Qt.Horizontal), "Info")

    def test_tree_model_shows_folder_collision_warning_in_info_column(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        _app = QApplication.instance() or QApplication([])

        def make_record(row_id: int):
            record = mock.Mock(spec=PlanRecord)
            record.audio_type = "Oneshots"
            record.category = "Kicks"
            record.pack = "Pack A"
            record.confidence = "0.91"
            record.is_preserved = False
            record.is_manual = False
            record.subcategory = None
            record.tags = []
            record.source_path = Path("Source/kick.wav")
            record.staging_row_id = row_id
            return record

        model = LibraryTreeModel()
        model.rebuild([make_record(1), make_record(2)])

        name_index = model.index(0, 0)
        info_index = model.index(0, 1)
        self.assertNotIn("naming collision", str(name_index.data(Qt.DisplayRole)))
        self.assertIn("naming collision", str(info_index.data(Qt.DisplayRole)))


class SimilarityFilterTests(unittest.TestCase):
    def test_similarity_bias_narrows_results_toward_near_or_far_matches(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        _app = QApplication.instance() or QApplication([])

        def make_record(name):
            rec = mock.Mock(spec=PlanRecord)
            rec.pack = "Pack A"
            rec.category = "Kicks"
            rec.subcategory = None
            rec.tags = []
            rec.audio_type = "Oneshots"
            rec.source_path = Path(f"Source/{name}")
            rec.confidence = "0.91"
            rec.evidence = {}
            rec.is_manual = False
            rec.is_preserved = False
            return rec

        model = StagingTableModel(
            [make_record("anchor.wav"), make_record("near.wav"), make_record("far.wav")],
            undo_stack=None,
            sync_callback=None,
        )
        proxy = MultiFilterProxyModel()
        proxy.setSourceModel(model)
        proxy.set_similarity_data({0: 0.0, 1: 0.2, 2: 0.9}, avg_dist=0.55, anchor_row=0)

        self.assertEqual(proxy.rowCount(), 3)

        proxy.set_similarity_bias(-60)
        self.assertEqual(proxy.rowCount(), 2)

        proxy.set_similarity_bias(60)
        self.assertEqual(proxy.rowCount(), 2)

    def test_similarity_anchor_still_respects_column_filters(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        _app = QApplication.instance() or QApplication([])

        def make_record(name, category):
            rec = mock.Mock(spec=PlanRecord)
            rec.pack = "Pack A"
            rec.category = category
            rec.subcategory = None
            rec.tags = []
            rec.audio_type = "Oneshots"
            rec.source_path = Path(f"Source/{name}")
            rec.confidence = "0.91"
            rec.evidence = {}
            rec.is_manual = False
            rec.is_preserved = False
            return rec

        model = StagingTableModel(
            [make_record("anchor.wav", "Kicks"), make_record("near.wav", "Snares")],
            undo_stack=None,
            sync_callback=None,
        )
        proxy = MultiFilterProxyModel()
        proxy.setSourceModel(model)
        proxy.set_similarity_data({0: 0.0, 1: 0.2}, avg_dist=0.1, anchor_row=0)
        proxy.set_similarity_bias(60)
        proxy.set_column_filters(StagingColumn.CATEGORY, {"Snares"})

        self.assertEqual(proxy.rowCount(), 1)
        self.assertEqual(proxy.index(0, StagingColumn.CATEGORY).data(Qt.DisplayRole), "Snares")

    def test_proxy_filter_setters_skip_redundant_invalidations(self):
        proxy = MultiFilterProxyModel()
        refresh = mock.Mock()
        proxy._refresh_filter = refresh

        proxy.set_path_filter("D:/Samples", True)
        proxy.set_path_filter("D:/Samples", True)
        proxy.set_similarity_bias(0)
        proxy.clear_similarity()
        proxy.set_similarity_data({1: 0.1, 2: 0.4}, avg_dist=0.25, anchor_row=1)
        proxy.set_similarity_data({1: 0.1, 2: 0.4}, avg_dist=0.25, anchor_row=1)
        proxy.set_matched_ids({7})
        proxy.set_matched_ids({7})

        self.assertEqual(refresh.call_count, 3)


class ProxySortTests(unittest.TestCase):
    def test_confidence_sort_uses_numeric_edit_role(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        _app = QApplication.instance() or QApplication([])

        def make_record(name: str, confidence: str):
            rec = mock.Mock(spec=PlanRecord)
            rec.pack = "Pack A"
            rec.category = "Kicks"
            rec.subcategory = None
            rec.tags = []
            rec.audio_type = "Oneshots"
            rec.source_path = Path(f"Source/{name}")
            rec.confidence = confidence
            rec.evidence = {}
            rec.is_manual = False
            rec.is_preserved = False
            return rec

        model = StagingTableModel(
            [make_record("high.wav", "0.9"), make_record("low.wav", "0.2")],
            undo_stack=None,
            sync_callback=None,
        )
        proxy = MultiFilterProxyModel()
        proxy.setSourceModel(model)

        low_idx = model.index(1, StagingColumn.CONFIDENCE)
        high_idx = model.index(0, StagingColumn.CONFIDENCE)

        self.assertTrue(proxy.lessThan(low_idx, high_idx))

    def test_matched_id_filter_uses_persisted_row_id_after_source_sort(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        _app = QApplication.instance() or QApplication([])

        def make_record(name: str, row_id: int):
            rec = mock.Mock(spec=PlanRecord)
            rec.pack = "Pack A"
            rec.category = "Kicks"
            rec.subcategory = None
            rec.tags = []
            rec.audio_type = "Oneshots"
            rec.source_path = Path(f"Source/{name}")
            rec.confidence = "0.91"
            rec.evidence = {}
            rec.is_manual = False
            rec.is_preserved = False
            rec.staging_row_id = row_id
            return rec

        model = StagingTableModel(
            [make_record("z.wav", 7), make_record("a.wav", 2)],
            undo_stack=None,
            sync_callback=None,
        )
        model.set_group_column(StagingColumn.FILENAME)
        proxy = MultiFilterProxyModel()
        proxy.setSourceModel(model)
        proxy.set_matched_ids({7})

        self.assertEqual(proxy.rowCount(), 1)
        self.assertEqual(proxy.index(0, StagingColumn.FILENAME).data(Qt.DisplayRole), "z.wav")

    def test_direct_sync_uses_stable_staging_row_id(self):
        calls = []
        record = PlanRecord(Path("Source/kick.wav"), "Pack", "Kicks", "Oneshots", "0.9")
        record.evidence = {}
        record.staging_row_id = 42
        model = StagingTableModel([record], undo_stack=None, sync_callback=lambda row_id, rec: calls.append((row_id, rec.pack)))

        model.setData(model.index(0, StagingColumn.PACK), "New Pack", Qt.EditRole)

        self.assertEqual(calls, [(42, "New Pack")])


class DelegateCharacterizationTests(unittest.TestCase):
    def test_tag_delegate_uses_shared_parser_and_preserves_comma_separator(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        from gui.widgets.delegates.tag_pill_delegate import TagPillDelegate

        _app = QApplication.instance() or QApplication([])
        delegate = TagPillDelegate()
        editor = QLineEdit()
        editor.setText(" warm, dry  128.0bpm ")
        model = mock.Mock()
        index = mock.Mock()

        delegate.setModelData(editor, model, index)

        model.setData.assert_called_once_with(index, ["warm", "dry", "128bpm"], Qt.EditRole)

    def test_compare_tree_delegate_paints_with_copied_style_option(self):
        from gui.widgets.delegates.compare_tree_delegate import CompareTreeDelegate

        delegate = CompareTreeDelegate(horizontal_padding=4, vertical_padding=3)
        option = QStyleOptionViewItem()
        option.rect = QRect(0, 0, 100, 40)
        captured = {}

        def fake_paint(self, painter, padded, index):
            captured["same_object"] = padded is option
            rect = padded.rect
            captured["rect"] = (rect.x(), rect.y(), rect.width(), rect.height())

        with mock.patch.object(QStyledItemDelegate, "paint", fake_paint):
            delegate.paint(mock.Mock(), option, QModelIndex())

        self.assertFalse(captured["same_object"])
        self.assertEqual(option.rect, QRect(0, 0, 100, 40))
        self.assertNotEqual(captured["rect"], (0, 0, 100, 40))

    def test_pack_delegate_submits_typed_pack_text(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication, QComboBox
        from gui.widgets.delegates.combo_delegate import ComboDelegate

        _app = QApplication.instance() or QApplication([])
        delegate = ComboDelegate()
        editor = QComboBox()
        editor.setEditable(True)
        editor.addItem("Old Pack", "Old Pack")
        editor.setCurrentIndex(0)
        editor.setCurrentText("New Pack")
        model = mock.Mock()
        index = mock.Mock()
        index.column.return_value = StagingColumn.PACK

        delegate.setModelData(editor, model, index)

        model.setData.assert_called_once_with(index, "New Pack", Qt.EditRole)

    def test_pack_delegate_submits_candidate_data_without_confidence_label(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication, QComboBox
        from gui.widgets.delegates.combo_delegate import ComboDelegate

        _app = QApplication.instance() or QApplication([])
        delegate = ComboDelegate()
        editor = QComboBox()
        editor.setEditable(True)
        editor.addItem("24bit 48kHz (85%)", "24bit 48kHz")
        editor.setCurrentIndex(0)
        model = mock.Mock()
        index = mock.Mock()
        index.column.return_value = StagingColumn.PACK

        delegate.setModelData(editor, model, index)

        model.setData.assert_called_once_with(index, "24bit 48kHz", Qt.EditRole)

    def test_pack_delegate_editor_uses_table_cell_style(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication, QComboBox, QWidget
        from gui.widgets.delegates.combo_delegate import ComboDelegate

        _app = QApplication.instance() or QApplication([])
        delegate = ComboDelegate()
        option = QStyleOptionViewItem()
        option.rect = QRect(10, 20, 120, 26)
        parent = QWidget()
        index = mock.Mock()
        index.column.return_value = StagingColumn.PACK
        index.data.side_effect = lambda role=None: [("24bit 48kHz", 0.85)] if role == Qt.UserRole else "24bit 48kHz"

        editor = delegate.createEditor(parent, option, index)
        delegate.updateEditorGeometry(editor, option, index)

        self.assertIsInstance(editor, QComboBox)
        self.assertTrue(editor.property("tableEditor"))
        self.assertTrue(editor.autoFillBackground())
        self.assertTrue(editor.testAttribute(Qt.WA_StyledBackground))
        self.assertFalse(editor.hasFrame())
        self.assertEqual(editor.geometry(), QRect(10, 20, 119, 25))

    def test_tag_delegate_editor_uses_table_cell_style(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication, QWidget
        from gui.widgets.delegates.tag_pill_delegate import TagPillDelegate

        _app = QApplication.instance() or QApplication([])
        delegate = TagPillDelegate()
        option = QStyleOptionViewItem()
        option.rect = QRect(6, 12, 140, 28)
        parent = QWidget()
        index = mock.Mock()
        index.column.return_value = StagingColumn.TAGS

        editor = delegate.createEditor(parent, option, index)
        delegate.updateEditorGeometry(editor, option, index)

        self.assertIsInstance(editor, QLineEdit)
        self.assertTrue(editor.property("tableEditor"))
        self.assertTrue(editor.autoFillBackground())
        self.assertTrue(editor.testAttribute(Qt.WA_StyledBackground))
        self.assertFalse(editor.hasFrame())
        self.assertEqual(editor.geometry(), QRect(6, 12, 139, 27))
