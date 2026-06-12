from types import SimpleNamespace
import os
from unittest import mock

from gui.core.coherence_controller import CoherenceController
from gui.utils.constants import StagingColumn


def _action_combo(dialog, row=0):
    from gui.widgets.refinement_popup import RefinementActionCombo, RefinementColumns

    widget = dialog.table.cellWidget(row, RefinementColumns.ACTION)
    if isinstance(widget, RefinementActionCombo):
        return widget
    combo = widget.findChild(RefinementActionCombo) if widget is not None else None
    return combo


def _target_combo(dialog, row=0):
    from gui.widgets.refinement_popup import RefinementColumns, RefinementTargetCombo

    widget = dialog.table.cellWidget(row, RefinementColumns.TARGET)
    if isinstance(widget, RefinementTargetCombo):
        return widget
    return widget.findChild(RefinementTargetCombo)


def _keep_checkbox(dialog, row=0):
    from PySide6.QtWidgets import QCheckBox
    from gui.widgets.refinement_popup import RefinementColumns

    widget = dialog.table.cellWidget(row, RefinementColumns.OUTLIER_KEEP)
    return widget if isinstance(widget, QCheckBox) else None


class _FakeDB:
    def __init__(self):
        self.rows = [
            {
                "candidate_id": "c1",
                "record_id": "7",
                "suggested_category": "Kicks",
                "suggested_subcategory": "Generic",
            }
        ]

    def list_refinement_candidates(self, session_id, state=None):
        assert session_id == "s1"
        return self.rows if state == "auto_staged" else []


class _FakeModel:
    def __init__(self):
        self.records = [SimpleNamespace(staging_row_id=7, audio_type="Loops", category="Uncategorized", subcategory="")]
        self.updates = []
        self.synced_updates = []
        self.draft_updates = []

    def apply_bulk_updates(self, updates, text=""):
        self.draft_updates.extend(updates)
        return True

    def _apply_bulk_values(self, updates):
        self.updates.extend(updates)
        for rec, _col, value in updates:
            if _col == StagingColumn.TYPE:
                rec.audio_type = value
            else:
                rec.category, rec.subcategory = value

    def _sync_bulk_updates(self, updates):
        self.synced_updates.extend(updates)


def test_auto_staged_refinements_bypass_user_draft_and_sync_staging():
    model = _FakeModel()
    footer = SimpleNamespace(logs=[], log=lambda text: footer.logs.append(text))
    app = SimpleNamespace(
        engine=SimpleNamespace(db=_FakeDB(), session_id="s1"),
        model=model,
        footer=footer,
        view_controller=SimpleNamespace(update_library_views=lambda **_kwargs: None),
    )
    controller = CoherenceController()
    controller.app = app

    applied = controller.apply_auto_staged_refinements()

    assert applied == 1
    assert model.updates == [(model.records[0], StagingColumn.CATEGORY, ("Kicks", "Generic"))]
    assert model.synced_updates == model.updates
    assert model.draft_updates == []
    assert model.records[0].category == "Kicks"
    assert footer.logs == ["<b>Coherence:</b> auto-staged 1 refinement(s)."]


def test_auto_staged_refinements_skip_when_session_already_matches():
    model = _FakeModel()
    model.records[0].category = "Kicks"
    model.records[0].subcategory = "Generic"
    footer = SimpleNamespace(logs=[], log=lambda text: footer.logs.append(text))
    app = SimpleNamespace(
        engine=SimpleNamespace(db=_FakeDB(), session_id="s1"),
        model=model,
        footer=footer,
        view_controller=SimpleNamespace(update_library_views=lambda **_kwargs: None),
    )
    controller = CoherenceController()
    controller.app = app

    applied = controller.apply_auto_staged_refinements()

    assert applied == 0
    assert model.updates == []
    assert model.draft_updates == []
    assert footer.logs == []


def test_auto_staged_refinements_apply_suggested_type():
    model = _FakeModel()
    db = _FakeDB()
    db.rows[0]["suggested_audio_type"] = "Oneshots"
    footer = SimpleNamespace(logs=[], log=lambda text: footer.logs.append(text))
    app = SimpleNamespace(
        engine=SimpleNamespace(db=db, session_id="s1"),
        model=model,
        footer=footer,
        view_controller=SimpleNamespace(update_library_views=lambda **_kwargs: None),
    )
    controller = CoherenceController()
    controller.app = app

    applied = controller.apply_auto_staged_refinements()

    assert applied == 2
    assert model.updates[0] == (model.records[0], StagingColumn.TYPE, "Oneshots")
    assert model.updates[1] == (model.records[0], StagingColumn.CATEGORY, ("Kicks", "Generic"))
    assert model.records[0].audio_type == "Oneshots"


def test_reviewed_refinements_apply_directly_without_learning_category_changes():
    from pathlib import Path

    model = _FakeModel()
    model.records[0].source_path = Path("D:/Samples/kick.wav")
    learned = []

    class _Bridge:
        def update_token_adjustments_bulk(self, rows):
            learned.extend(rows)
            return len(rows)

    app = SimpleNamespace(
        model=model,
        data_manager=SimpleNamespace(bridge=_Bridge()),
        search_controller=SimpleNamespace(current_query=""),
        system_controller=SimpleNamespace(refresh_corrections=mock.Mock()),
        view_controller=SimpleNamespace(update_library_views=lambda **_kwargs: None),
    )
    controller = CoherenceController()
    controller.app = app

    applied = controller.apply_refinements(
        [
            {
                "record_id": "7",
                "suggested_category": "Kicks",
                "suggested_subcategory": "Generic",
            }
        ],
        notify=False,
    )

    assert applied == 1
    assert model.updates == [(model.records[0], StagingColumn.CATEGORY, ("Kicks", "Generic"))]
    assert model.synced_updates == model.updates
    assert model.draft_updates == []
    assert learned == []
    app.system_controller.refresh_corrections.assert_not_called()


def test_remembered_review_target_applies_to_current_record_by_path():
    from gui.core.coherence_review_decisions import apply_target_review_decisions

    class _DB:
        def list_coherence_review_decisions(self, source_paths=None, file_hashes=None):
            return [
                {
                    "source_path": "D:/Samples/Pack/kick.wav",
                    "file_hash": "hash-kick",
                    "decision_type": "target",
                    "target_audio_type": "Oneshots",
                    "target_category": "Kicks",
                    "target_subcategory": "Generic",
                }
            ]

    record = SimpleNamespace(
        source_path="D:/Samples/Pack/kick.wav",
        hash="hash-kick",
        audio_type="Loops",
        category="Percussion",
        subcategory="",
    )

    changed = apply_target_review_decisions(_DB(), [record])

    assert changed == 3
    assert (record.audio_type, record.category, record.subcategory) == ("Oneshots", "Kicks", "Generic")


def test_remembered_review_target_hash_fallback_requires_unique_current_record():
    from gui.core.coherence_review_decisions import apply_target_review_decisions

    class _DB:
        def list_coherence_review_decisions(self, source_paths=None, file_hashes=None):
            return [
                {
                    "source_path": "D:/Old/renamed.wav",
                    "file_hash": "shared-hash",
                    "decision_type": "target",
                    "target_audio_type": "Oneshots",
                    "target_category": "Kicks",
                    "target_subcategory": "",
                }
            ]

    records = [
        SimpleNamespace(source_path="D:/New/a.wav", hash="shared-hash", audio_type="Loops", category="Bass", subcategory=""),
        SimpleNamespace(source_path="D:/New/b.wav", hash="shared-hash", audio_type="Loops", category="Bass", subcategory=""),
    ]

    changed = apply_target_review_decisions(_DB(), records)

    assert changed == 0
    assert [record.category for record in records] == ["Bass", "Bass"]


def test_review_refinements_persists_file_specific_decisions():
    from PySide6.QtWidgets import QDialog

    class _DB:
        def __init__(self):
            self.decisions = []
            self.accepted = []

        def set_refinement_candidate_state(self, _session_id, candidate_ids, state):
            if state == "accepted":
                self.accepted.extend(candidate_ids)

        def upsert_coherence_review_decisions(self, session_id, decisions):
            self.decisions.append((session_id, decisions))

    class _Dialog:
        audioPreviewRequested = SimpleNamespace(connect=lambda *_args, **_kwargs: None)

        def __init__(self, _rows, _parent):
            pass

        def exec(self):
            return QDialog.Accepted

        def accepted_candidate_ids(self):
            return ["move-7"]

        def ignored_candidate_ids(self):
            return ["outlier:8:Oneshots:Kicks:"]

        def accepted_refinement_rows(self):
            return [
                {
                    "candidate_id": "move-7",
                    "record_id": "7",
                    "source_path": "D:/Samples/move.wav",
                    "file_hash": "hash-move",
                    "current_audio_type": "Loops",
                    "current_category": "Percussion",
                    "current_subcategory": "",
                    "suggested_audio_type": "Oneshots",
                    "suggested_category": "Kicks",
                    "suggested_subcategory": "",
                }
            ]

        def anchor_confirmed_record_ids(self):
            return []

    db = _DB()
    app = SimpleNamespace(
        engine=SimpleNamespace(db=db, session_id="s1"),
        model=_FakeModel(),
        footer=SimpleNamespace(set_coherence_state=mock.Mock()),
    )
    controller = CoherenceController()
    controller.app = app
    rows = [
        {
            "candidate_id": "move-7",
            "record_id": "7",
            "source_path": "D:/Samples/move.wav",
            "file_hash": "hash-move",
        },
        {
            "candidate_id": "outlier:8:Oneshots:Kicks:",
            "record_id": "8",
            "source_path": "D:/Samples/keep.wav",
            "file_hash": "hash-keep",
            "current_audio_type": "Oneshots",
            "current_category": "Kicks",
            "current_subcategory": "",
            "kind": "strong_outlier",
        },
    ]

    with mock.patch("gui.widgets.refinement_popup.RefinementReviewDialog", _Dialog):
        with mock.patch.object(controller, "_review_rows", side_effect=[rows, []]):
            with mock.patch.object(controller, "apply_refinements", return_value=1):
                controller.review_refinements()

    assert db.accepted == ["move-7"]
    assert len(db.decisions) == 1
    session_id, decisions = db.decisions[0]
    assert session_id == "s1"
    assert [decision["decision_type"] for decision in decisions] == ["target", "accepted_current"]
    assert decisions[0]["target_category"] == "Kicks"
    assert decisions[1]["target_category"] == "Kicks"


def test_refinement_dialog_target_displays_suggested_type_without_extra_column():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QHeaderView
    from gui.widgets.refinement_popup import RefinementColumns, RefinementReviewDialog, RefinementTargetCombo

    app = QApplication.instance() or QApplication([])
    dialog = RefinementReviewDialog(
        [
            {
                "candidate_id": "c1",
                "record_id": "7",
                "current_audio_type": "Loops",
                "current_category": "Kicks",
                "suggested_audio_type": "Oneshots",
                "suggested_category": "Kicks",
                "suggested_subcategory": "Generic",
            }
        ]
    )
    try:
        assert dialog.table.columnCount() == 6
        current = dialog.table.item(0, RefinementColumns.CURRENT)
        target = _target_combo(dialog)

        assert current.text() == "L/Kicks"
        assert isinstance(target, RefinementTargetCombo)
        assert target.value() == "Kicks"
        assert target.display_text() == "O/Kicks/Generic"
        assert dialog.table.horizontalHeader().sectionResizeMode(RefinementColumns.CURRENT) == QHeaderView.Interactive
        assert dialog.table.horizontalHeader().sectionResizeMode(RefinementColumns.TARGET) == QHeaderView.Interactive
    finally:
        dialog.deleteLater()


def test_refinement_target_filters_categories_by_audio_type():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from gui.widgets.refinement_popup import RefinementTargetCombo

    app = QApplication.instance() or QApplication([])
    combo = RefinementTargetCombo(["Bass", "Full Drums"], "Bass", display_prefix="Oneshots")
    try:
        assert "Bass" in combo.category_values_for_audio_type("Oneshots")
        assert "Full Drums" not in combo.category_values_for_audio_type("Oneshots")
        assert "Full Drums" in combo.category_values_for_audio_type("Loops")
    finally:
        combo.deleteLater()


def test_refinement_dialog_does_not_fallback_to_current_subcategory_for_blank_target_subcategory():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from gui.widgets.refinement_popup import RefinementReviewDialog, RefinementTargetCombo

    app = QApplication.instance() or QApplication([])
    dialog = RefinementReviewDialog(
        [
            {
                "candidate_id": "c1",
                "record_id": "7",
                "current_audio_type": "Oneshots",
                "current_category": "Percussion",
                "current_subcategory": "Membranophones",
                "suggested_audio_type": "Oneshots",
                "suggested_category": "Kicks",
                "suggested_subcategory": "",
                "initial_action": "accept",
            }
        ]
    )
    try:
        target = _target_combo(dialog)

        assert isinstance(target, RefinementTargetCombo)
        assert target.value() == "Kicks"
        assert target.subcategory() == ""
        assert target.display_text() == "O/Kicks"
        assert dialog.accepted_refinement_rows()[0]["suggested_subcategory"] == ""
    finally:
        dialog.deleteLater()


def test_refinement_dialog_action_defaults_and_one_click_toggle():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from gui.widgets.refinement_popup import RefinementColumns, RefinementReviewDialog, RefinementActionCombo

    app = QApplication.instance() or QApplication([])
    dialog = RefinementReviewDialog(
        [
            {"candidate_id": "auto", "record_id": "7", "initial_action": "accept"},
            {"candidate_id": "pending", "record_id": "8", "initial_action": "reject"},
        ]
    )
    try:
        assert dialog.tabs.count() == 2
        assert dialog.tabs.tabText(0) == "Suggestions (2)"
        assert dialog.tabs.tabText(1) == "Outliers (0)"
        assert dialog.lbl_file_count.text() == "2 files"

        auto_action = dialog.table.cellWidget(0, RefinementColumns.ACTION)
        pending_action = dialog.table.cellWidget(1, RefinementColumns.ACTION)

        assert isinstance(auto_action, RefinementActionCombo)
        assert isinstance(pending_action, RefinementActionCombo)
        assert auto_action.currentData() == "accept"
        assert pending_action.currentData() == "reject"

        pending_action.click()

        assert pending_action.currentData() == "accept"
    finally:
        dialog.deleteLater()


def test_refinement_dialog_strong_outlier_is_reject_only_and_prompts_anchor():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QDialog, QMessageBox
    from gui.widgets import refinement_popup
    from gui.widgets.refinement_popup import RefinementColumns, RefinementReviewDialog, RefinementActionCombo, RefinementTargetCombo

    app = QApplication.instance() or QApplication([])
    dialog = RefinementReviewDialog(
        [
            {
                "candidate_id": "outlier:7:Oneshots:Kicks:Generic",
                "record_id": "7",
                "kind": "strong_outlier",
                "initial_action": "reject",
                "anchor_prompt_eligible": True,
                "file_name": "basslike-kick.wav",
                "current_audio_type": "Oneshots",
                "current_category": "Kicks",
                "current_subcategory": "Generic",
                "suggested_audio_type": "Oneshots",
                "suggested_category": "Kicks",
                "suggested_subcategory": "Generic",
                "outlier_ratio": 2.1,
            }
        ]
    )
    try:
        target = _target_combo(dialog)
        keep = _keep_checkbox(dialog)
        assert isinstance(target, RefinementTargetCombo)
        assert target.display_text() == "O/Kicks/Generic"
        assert keep is not None
        assert keep.text() == ""
        assert not keep.isChecked()
        assert target.isEnabled()
        assert dialog.tabs.tabText(1) == "Outliers (1)"

        keep.setChecked(True)

        class _AnchorDialog:
            audioPreviewRequested = SimpleNamespace(connect=lambda *_args, **_kwargs: None)

            def __init__(self, rows, _parent):
                self.rows = rows

            def exec(self):
                return QDialog.Accepted

            def selected_record_ids(self):
                return ["7"]

        with mock.patch.object(refinement_popup, "OutlierAnchorPromptDialog", _AnchorDialog):
            with mock.patch.object(refinement_popup.QMessageBox, "warning", return_value=QMessageBox.Yes):
                dialog.btn_ok.click()

        assert dialog.anchor_confirmed_record_ids() == ["7"]
        assert dialog.ignored_candidate_ids() == ["outlier:7:Oneshots:Kicks:Generic"]
    finally:
        dialog.deleteLater()


def test_refinement_dialog_strong_outlier_can_accept_manual_target():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from gui.widgets.refinement_popup import RefinementColumns, RefinementReviewDialog, RefinementActionCombo, RefinementTargetCombo

    app = QApplication.instance() or QApplication([])
    dialog = RefinementReviewDialog(
        [
            {
                "candidate_id": "outlier:7:Loops:Hats & Cymbals:Hats",
                "record_id": "7",
                "kind": "strong_outlier",
                "initial_action": "reject",
                "current_audio_type": "Loops",
                "current_category": "Hats & Cymbals",
                "current_subcategory": "Hats",
                "suggested_audio_type": "Loops",
                "suggested_category": "Hats & Cymbals",
                "suggested_subcategory": "Hats",
            }
        ]
    )
    try:
        target = _target_combo(dialog)
        keep = _keep_checkbox(dialog)
        assert isinstance(target, RefinementTargetCombo)
        assert keep is not None

        target.set_value("Melodics", "Oneshots", "Electronic")

        rows = dialog.accepted_refinement_rows()
        assert rows[0]["kind"] == "strong_outlier"
        assert rows[0]["suggested_audio_type"] == "Oneshots"
        assert rows[0]["suggested_category"] == "Melodics"
        assert rows[0]["suggested_subcategory"] == "Electronic"
    finally:
        dialog.deleteLater()


def test_refinement_dialog_anchor_prompt_cancel_prevents_accept():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QDialog, QMessageBox
    from gui.widgets import refinement_popup
    from gui.widgets.refinement_popup import RefinementColumns, RefinementReviewDialog

    app = QApplication.instance() or QApplication([])
    dialog = RefinementReviewDialog(
        [
            {
                "candidate_id": "outlier:7:Oneshots:Kicks:Generic",
                "record_id": "7",
                "kind": "strong_outlier",
                "anchor_prompt_eligible": True,
                "current_audio_type": "Oneshots",
                "current_category": "Kicks",
                "current_subcategory": "Generic",
            }
        ]
    )
    try:
        keep_widget = dialog.outliers_table.cellWidget(0, RefinementColumns.OUTLIER_KEEP)
        keep_widget.setChecked(True)
        class _AnchorDialog:
            audioPreviewRequested = SimpleNamespace(connect=lambda *_args, **_kwargs: None)

            def __init__(self, _rows, _parent):
                pass

            def exec(self):
                return QDialog.Rejected

            def selected_record_ids(self):
                return []

        with mock.patch.object(refinement_popup, "OutlierAnchorPromptDialog", _AnchorDialog):
            with mock.patch.object(refinement_popup.QMessageBox, "warning") as warning:
                dialog.btn_ok.click()

        warning.assert_not_called()
        assert dialog.result() == 0
    finally:
        dialog.deleteLater()


def test_outlier_anchor_prompt_dialog_selects_rows_and_previews():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from gui.widgets.refinement_popup import OutlierAnchorPromptDialog, RefinementColumns

    app = QApplication.instance() or QApplication([])
    dialog = OutlierAnchorPromptDialog(
        [
            {
                "candidate_id": "outlier:7",
                "record_id": "7",
                "display_index": "7",
                "pack": "Pack A",
                "file_name": "one.wav",
                "source_path": "D:/Samples/one.wav",
                "kind": "strong_outlier",
                "current_audio_type": "Loops",
                "current_category": "Melodics",
                "current_subcategory": "",
            },
            {
                "candidate_id": "outlier:8",
                "record_id": "8",
                "display_index": "8",
                "pack": "Pack B",
                "file_name": "two.wav",
                "source_path": "D:/Samples/two.wav",
                "kind": "strong_outlier",
                "current_audio_type": "Oneshots",
                "current_category": "Kicks",
                "current_subcategory": "",
            },
        ]
    )
    try:
        previews = []
        dialog.audioPreviewRequested.connect(previews.append)
        dialog.table.selectRow(1)
        dialog._preview_audio_for_selected()
        assert previews == ["D:/Samples/two.wav"]

        first_check = dialog.table.cellWidget(0, RefinementColumns.TARGET)
        second_check = dialog.table.cellWidget(1, RefinementColumns.TARGET)
        first_check.setChecked(True)
        second_check.setChecked(False)
        assert dialog.selected_record_ids() == ["7"]
    finally:
        dialog.deleteLater()


def test_refinement_dialog_rejected_refinement_can_trigger_anchor_prompt():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QDialog, QMessageBox
    from gui.widgets import refinement_popup
    from gui.widgets.refinement_popup import RefinementReviewDialog

    app = QApplication.instance() or QApplication([])
    dialog = RefinementReviewDialog(
        [
            {
                "candidate_id": "move-7",
                "record_id": "7",
                "kind": "refinement",
                "initial_action": "reject",
                "anchor_prompt_eligible": True,
                "file_name": "jack oh.wav",
                "current_audio_type": "Loops",
                "current_category": "Hats & Cymbals",
                "current_subcategory": "Hats",
                "suggested_audio_type": "Oneshots",
                "suggested_category": "Melodics",
                "suggested_subcategory": "",
            }
        ]
    )
    try:
        class _AnchorDialog:
            audioPreviewRequested = SimpleNamespace(connect=lambda *_args, **_kwargs: None)

            def __init__(self, rows, _parent):
                self.rows = rows

            def exec(self):
                return QDialog.Accepted

            def selected_record_ids(self):
                return ["7"]

        with mock.patch.object(refinement_popup, "OutlierAnchorPromptDialog", _AnchorDialog):
            with mock.patch.object(refinement_popup.QMessageBox, "warning", return_value=QMessageBox.Yes):
                dialog.btn_ok.click()

        assert dialog.anchor_confirmed_record_ids() == ["7"]
        assert dialog.ignored_candidate_ids() == ["move-7"]
    finally:
        dialog.deleteLater()


def test_refinement_dialog_ok_warns_before_accepting():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QDialog, QMessageBox
    from gui.widgets import refinement_popup
    from gui.widgets.refinement_popup import RefinementReviewDialog

    app = QApplication.instance() or QApplication([])
    dialog = RefinementReviewDialog(
        [
            {"candidate_id": "accept", "record_id": "7", "initial_action": "accept"},
            {"candidate_id": "reject", "record_id": "8", "initial_action": "reject"},
        ]
    )
    try:
        with mock.patch.object(refinement_popup.QMessageBox, "warning", return_value=QMessageBox.No) as warning:
            dialog.btn_ok.click()

        assert dialog.result() == 0
        message = warning.call_args.args[2]
        assert "apply 1 coherence refinement" in message
        assert "immediately" in message
        assert "mark 1 suggestion as rejected" in message

        with mock.patch.object(refinement_popup.QMessageBox, "warning", return_value=QMessageBox.Yes):
            dialog.btn_ok.click()

        assert dialog.result() == QDialog.Accepted
    finally:
        dialog.deleteLater()


def test_strong_outlier_rows_skip_active_refinements_and_return_all_eligible_per_bucket():
    class _Settings:
        def value(self, _key, default=""):
            return default

    class _DB:
        def list_coherence_results(self, session_id):
            assert session_id == "s1"
            rows = []
            for record_id, distance, is_outlier in (
                ("1", 1.0, False),
                ("2", 1.0, False),
                ("3", 1.0, False),
                ("4", 1.0, False),
                ("5", 8.0, True),
                ("6", 7.0, True),
                ("7", 6.5, True),
                ("8", 9.0, True),
            ):
                rows.append(
                    {
                        "record_id": record_id,
                        "category": "Kicks",
                        "subcategory": "Generic",
                        "is_outlier": int(is_outlier),
                        "cluster_id": "oneshots_kicks_generic_000",
                        "anchor_fit_status": "distant",
                        "nearest_neighbor_summary_json": f'{{"distance_to_cluster_medoid": {distance}}}',
                    }
                )
            return rows

    records = [
        SimpleNamespace(staging_row_id=idx, audio_type="Oneshots", category="Kicks", subcategory="Generic")
        for idx in range(1, 9)
    ]
    app = SimpleNamespace(
        engine=SimpleNamespace(db=_DB(), session_id="s1"),
        model=SimpleNamespace(records=records),
        settings=_Settings(),
    )
    controller = CoherenceController()
    controller.app = app

    rows = controller._derive_strong_outlier_rows(active_refinement_record_ids={"8"})

    assert [row["record_id"] for row in rows] == ["5", "6", "7"]
    assert all(row["kind"] == "strong_outlier" for row in rows)
    assert all(row["anchor_prompt_eligible"] for row in rows)


def test_review_row_sort_groups_by_current_classification_and_filename():
    rows = [
        {
            "kind": "strong_outlier",
            "current_audio_type": "Oneshots",
            "current_category": "Kicks",
            "current_subcategory": "Hard",
            "pack": "Pack B",
            "file_name": "zeta.wav",
            "outlier_ratio": 9.0,
        },
        {
            "kind": "strong_outlier",
            "current_audio_type": "Oneshots",
            "current_category": "Snares",
            "current_subcategory": "Hard",
            "pack": "Pack A",
            "file_name": "alpha.wav",
            "outlier_ratio": 2.0,
        },
        {
            "kind": "strong_outlier",
            "current_audio_type": "Oneshots",
            "current_category": "Kicks",
            "current_subcategory": "Hard",
            "pack": "Pack A",
            "file_name": "alpha.wav",
            "outlier_ratio": 2.0,
        },
        {
            "kind": "strong_outlier",
            "current_audio_type": "Oneshots",
            "current_category": "Kicks",
            "current_subcategory": "Hard",
            "pack": "Pack A",
            "file_name": "alpha copy.wav",
            "outlier_ratio": 8.0,
        },
    ]

    rows.sort(key=CoherenceController._review_row_sort_key)

    assert [(row["current_category"], row["pack"], row["file_name"]) for row in rows] == [
        ("Kicks", "Pack A", "alpha copy.wav"),
        ("Kicks", "Pack A", "alpha.wav"),
        ("Kicks", "Pack B", "zeta.wav"),
        ("Snares", "Pack A", "alpha.wav"),
    ]


def test_strong_outlier_rows_skip_uncategorized_bucket():
    class _Settings:
        def value(self, _key, default=""):
            return default

    class _DB:
        def list_coherence_results(self, session_id):
            assert session_id == "s1"
            return [
                {
                    "record_id": str(idx),
                    "category": "Uncategorized",
                    "subcategory": "",
                    "is_outlier": int(idx == 3),
                    "cluster_id": "oneshots_uncategorized_000",
                    "anchor_fit_status": "distant",
                    "nearest_neighbor_summary_json": f'{{"distance_to_cluster_medoid": {3.0 if idx == 3 else 1.0}}}',
                }
                for idx in range(1, 4)
            ]

    records = [
        SimpleNamespace(staging_row_id=idx, audio_type="Oneshots", category="Uncategorized", subcategory="")
        for idx in range(1, 4)
    ]
    app = SimpleNamespace(
        engine=SimpleNamespace(db=_DB(), session_id="s1"),
        model=SimpleNamespace(records=records),
        settings=_Settings(),
    )
    controller = CoherenceController()
    controller.app = app

    assert controller._derive_strong_outlier_rows(active_refinement_record_ids=set()) == []


def test_strong_outlier_evidence_includes_nearest_adjacent_cluster():
    class _Settings:
        def value(self, _key, default=""):
            return default

    class _DB:
        def list_coherence_results(self, session_id):
            assert session_id == "s1"
            return [
                {
                    "record_id": "1",
                    "category": "Percussion",
                    "subcategory": "Shakers",
                    "is_outlier": 0,
                    "cluster_id": "oneshots_percussion_shakers_000",
                    "nearest_neighbor_summary_json": '{"distance_to_cluster_medoid": 1.0}',
                },
                {
                    "record_id": "3",
                    "category": "Percussion",
                    "subcategory": "Shakers",
                    "is_outlier": 0,
                    "cluster_id": "oneshots_percussion_shakers_000",
                    "nearest_neighbor_summary_json": '{"distance_to_cluster_medoid": 1.0}',
                },
                {
                    "record_id": "2",
                    "category": "Percussion",
                    "subcategory": "Shakers",
                    "is_outlier": 1,
                    "cluster_id": "oneshots_percussion_shakers_000",
                    "anchor_fit_status": "distant",
                    "nearest_neighbor_summary_json": (
                        '{"distance_to_cluster_medoid": 2.0, '
                        '"nearest_adjacent_cluster": {'
                        '"audio_type": "Oneshots", "category": "Hats & Cymbals", '
                        '"subcategory": "Hats", "adjacency_ratio": 0.82, "is_close": true}}'
                    ),
                },
            ]

    records = [
        SimpleNamespace(staging_row_id=1, audio_type="Oneshots", category="Percussion", subcategory="Shakers"),
        SimpleNamespace(staging_row_id=2, audio_type="Oneshots", category="Percussion", subcategory="Shakers"),
        SimpleNamespace(staging_row_id=3, audio_type="Oneshots", category="Percussion", subcategory="Shakers"),
    ]
    app = SimpleNamespace(
        engine=SimpleNamespace(db=_DB(), session_id="s1"),
        model=SimpleNamespace(records=records),
        settings=_Settings(),
    )
    controller = CoherenceController()
    controller.app = app

    rows = controller._derive_strong_outlier_rows(active_refinement_record_ids=set())

    assert len(rows) == 1
    assert "Nearest neighboring cluster: close Oneshots / Hats & Cymbals / Hats, 0.82x separation" in rows[0]["evidence"]


def test_review_rows_marks_outlier_refinements_anchor_prompt_eligible():
    class _DB:
        def list_refinement_candidates(self, _session_id, state=None):
            if state == "pending":
                return [
                    {
                        "candidate_id": "move-7",
                        "record_id": "7",
                        "state": "pending",
                        "current_audio_type": "Loops",
                        "current_category": "Hats & Cymbals",
                        "current_subcategory": "Hats",
                        "suggested_audio_type": "Oneshots",
                        "suggested_category": "Melodics",
                        "suggested_subcategory": "",
                    }
                ]
            return []

        def list_coherence_results(self, _session_id):
            return [
                {
                    "record_id": "7",
                    "is_outlier": 1,
                    "anchor_fit_status": "distant",
                    "nearest_neighbor_summary_json": '{"distance_to_cluster_medoid": 2.4}',
                }
            ]

    records = [
        SimpleNamespace(
            staging_row_id=7,
            source_path="jack oh.wav",
            audio_type="Loops",
            category="Hats & Cymbals",
            subcategory="Hats",
            pack="Foreign Girls",
        )
    ]
    app = SimpleNamespace(
        engine=SimpleNamespace(db=_DB(), session_id="s1"),
        model=SimpleNamespace(records=records),
        settings=SimpleNamespace(value=lambda _key, default="": default),
    )
    controller = CoherenceController()
    controller.app = app

    rows = controller._review_rows()

    assert len(rows) == 1
    assert rows[0]["candidate_id"] == "move-7"
    assert rows[0]["anchor_prompt_eligible"] is True
    assert rows[0]["medoid_distance"] == 2.4


def test_manual_coherence_ready_prompts_build():
    from PySide6.QtWidgets import QMessageBox

    class _DB:
        def list_refinement_candidates(self, _session_id, state=None):
            return []

        def list_coherence_results(self, _session_id):
            return []

    footer = SimpleNamespace(set_coherence_state=mock.Mock())
    app = SimpleNamespace(
        engine=SimpleNamespace(db=_DB(), session_id="s1"),
        model=SimpleNamespace(records=[]),
        footer=footer,
        open_build_workspace=mock.Mock(),
    )
    controller = CoherenceController()
    controller.app = app

    with mock.patch("gui.core.coherence_controller.QMessageBox.question", return_value=QMessageBox.Yes):
        controller.apply_coherence_result({"ran": True}, mode="manual")

    app.open_build_workspace.assert_called_once_with()


def test_manual_review_empty_local_queue_rechecks_before_build_prompt():
    from PySide6.QtWidgets import QMessageBox

    class _DB:
        pass

    app = SimpleNamespace(
        engine=SimpleNamespace(db=_DB(), session_id="s1"),
        footer=SimpleNamespace(set_coherence_state=mock.Mock()),
    )
    controller = CoherenceController()
    controller.app = app

    with mock.patch.object(controller, "_review_rows", return_value=[]):
        with mock.patch.object(controller, "start_coherence_audit") as rerun:
            with mock.patch("gui.core.coherence_controller.QMessageBox.question") as prompt:
                controller.review_refinements()

    prompt.assert_not_called()
    rerun.assert_called_once_with(force=True, mode="manual")
    message = app.footer.set_coherence_state.call_args.args[0]
    assert "Rechecking" in message


def test_continuous_review_reruns_after_applied_refinement():
    class _DB:
        def __init__(self):
            self.accepted = []

        def list_refinement_candidates(self, _session_id, state=None):
            if state == "pending":
                return [{"candidate_id": "pending", "record_id": "7", "state": "pending", "confidence_score": 0.5}]
            return []

        def list_coherence_results(self, _session_id):
            return []

        def set_refinement_candidate_state(self, _session_id, candidate_ids, state):
            if state == "accepted":
                self.accepted.extend(candidate_ids)

    class _Dialog:
        audioPreviewRequested = SimpleNamespace(connect=lambda *_args, **_kwargs: None)

        def __init__(self, _rows, _parent):
            pass

        def exec(self):
            from PySide6.QtWidgets import QDialog

            return QDialog.Accepted

        def accepted_candidate_ids(self):
            return ["pending"]

        def ignored_candidate_ids(self):
            return []

        def accepted_refinement_rows(self):
            return [{"record_id": "7"}]

        def anchor_confirmed_record_ids(self):
            return []

    db = _DB()
    app = SimpleNamespace(
        engine=SimpleNamespace(db=db, session_id="s1"),
        model=_FakeModel(),
        footer=SimpleNamespace(set_coherence_state=lambda *_args: None),
    )
    controller = CoherenceController()
    controller.app = app

    with mock.patch("gui.widgets.refinement_popup.RefinementReviewDialog", _Dialog):
        with mock.patch.object(controller, "apply_refinements", return_value=1):
            with mock.patch.object(controller, "start_coherence_audit") as rerun:
                controller.review_refinements(continuous=True)

    rerun.assert_called_once_with(force=True, mode="continuous")
    assert db.accepted == ["pending"]


def test_manual_review_defers_anchor_rerun_until_queue_is_clear():
    from PySide6.QtWidgets import QDialog

    class _DB:
        def set_refinement_candidate_state(self, *_args):
            pass

    class _Dialog:
        audioPreviewRequested = SimpleNamespace(connect=lambda *_args, **_kwargs: None)
        rows_seen = []

        def __init__(self, rows, _parent):
            self.rows_seen.append([str(row.get("record_id") or "") for row in rows])

        def exec(self):
            return QDialog.Accepted

        def accepted_candidate_ids(self):
            return []

        def ignored_candidate_ids(self):
            return []

        def accepted_refinement_rows(self):
            return []

        def anchor_confirmed_record_ids(self):
            return ["7"]

    app = SimpleNamespace(
        engine=SimpleNamespace(db=_DB(), session_id="s1"),
        footer=SimpleNamespace(set_coherence_state=mock.Mock()),
    )
    controller = CoherenceController()
    controller.app = app

    with mock.patch("gui.widgets.refinement_popup.RefinementReviewDialog", _Dialog):
        with mock.patch.object(controller, "_review_rows", return_value=[{"record_id": "7"}, {"record_id": "8"}]) as review_rows:
            with mock.patch.object(controller, "_promote_matching_anchors_for_records", return_value=(1, {"7"})):
                with mock.patch.object(controller, "start_coherence_audit") as rerun:
                    controller.review_refinements()

    rerun.assert_not_called()
    review_rows.assert_called_once()
    assert _Dialog.rows_seen == [["7", "8"]]
    assert [row["record_id"] for row in controller._manual_review_session_rows] == ["8"]
    message = app.footer.set_coherence_state.call_args.args[0]
    assert "New anchor saved" in message


def test_manual_review_uses_remaining_session_rows_on_next_click():
    from PySide6.QtWidgets import QDialog

    class _DB:
        def __init__(self):
            self.ignored = []

        def set_refinement_candidate_state(self, _session_id, candidate_ids, state):
            if state == "ignored":
                self.ignored.extend(candidate_ids)

    class _Dialog:
        audioPreviewRequested = SimpleNamespace(connect=lambda *_args, **_kwargs: None)
        rows_seen = []

        def __init__(self, rows, _parent):
            self.rows = rows
            self.rows_seen.append([str(row.get("candidate_id") or "") for row in rows])

        def exec(self):
            return QDialog.Accepted

        def accepted_candidate_ids(self):
            return []

        def ignored_candidate_ids(self):
            return [str(self.rows[0].get("candidate_id") or "")]

        def accepted_refinement_rows(self):
            return []

        def anchor_confirmed_record_ids(self):
            return []

    app = SimpleNamespace(
        engine=SimpleNamespace(db=_DB(), session_id="s1"),
        footer=SimpleNamespace(set_coherence_state=mock.Mock()),
    )
    controller = CoherenceController()
    controller.app = app
    controller._manual_review_session_rows = [{"candidate_id": "pending-8", "record_id": "8"}]

    with mock.patch("gui.widgets.refinement_popup.RefinementReviewDialog", _Dialog):
        with mock.patch.object(controller, "_review_rows") as review_rows:
            with mock.patch.object(controller, "_promote_matching_anchors_for_records", return_value=(0, set())):
                with mock.patch.object(controller, "start_coherence_audit") as rerun:
                    controller.review_refinements()

    review_rows.assert_not_called()
    assert _Dialog.rows_seen == [["pending-8"]]
    assert controller._manual_review_session_rows == []
    rerun.assert_called_once_with(force=True, mode="manual")


def test_cached_coherence_restores_persisted_manual_review_session():
    import json

    class _Settings:
        def __init__(self):
            self.values = {}

        def value(self, key, default=""):
            return self.values.get(key, default)

        def setValue(self, key, value):
            self.values[key] = value

    class _DB:
        def list_coherence_results(self, _session_id):
            return [{"record_id": "8"}]

        def list_refinement_candidates(self, _session_id, state=None):
            return []

    settings = _Settings()
    settings.setValue(
        "coherence/review_session/s1",
        json.dumps(
            {
                "state_key": "state-1",
                "rows": [{"candidate_id": "pending-8", "record_id": "8", "kind": "strong_outlier"}],
            }
        ),
    )
    app = SimpleNamespace(
        engine=SimpleNamespace(db=_DB(), session_id="s1"),
        settings=settings,
        acoustic_session_state=SimpleNamespace(current_key=lambda: "state-1", staging_record_ids=lambda: {"8"}),
        footer=SimpleNamespace(set_coherence_state=mock.Mock()),
        view_controller=SimpleNamespace(prewarm_library_map=mock.Mock()),
    )
    controller = CoherenceController()
    controller.app = app

    with mock.patch.object(controller, "_review_rows") as review_rows:
        assert controller._use_cached_coherence_state(app.engine)

    review_rows.assert_not_called()
    assert controller._manual_review_session_rows == [
        {"candidate_id": "pending-8", "record_id": "8", "kind": "strong_outlier"}
    ]
    app.footer.set_coherence_state.assert_called_once_with("1 library suggestion to review.", True, can_review=True)


def test_manual_review_reruns_after_last_outlier_anchor_promotion():
    from PySide6.QtWidgets import QDialog

    class _DB:
        def set_refinement_candidate_state(self, *_args):
            pass

    class _Dialog:
        audioPreviewRequested = SimpleNamespace(connect=lambda *_args, **_kwargs: None)

        def __init__(self, _rows, _parent):
            pass

        def exec(self):
            return QDialog.Accepted

        def accepted_candidate_ids(self):
            return []

        def ignored_candidate_ids(self):
            return []

        def accepted_refinement_rows(self):
            return []

        def anchor_confirmed_record_ids(self):
            return ["7"]

    app = SimpleNamespace(
        engine=SimpleNamespace(db=_DB(), session_id="s1"),
        footer=SimpleNamespace(set_coherence_state=mock.Mock()),
    )
    controller = CoherenceController()
    controller.app = app

    with mock.patch("gui.widgets.refinement_popup.RefinementReviewDialog", _Dialog):
        with mock.patch.object(controller, "_review_rows", side_effect=[[{"record_id": "7"}], []]):
            with mock.patch.object(controller, "_promote_matching_anchors_for_records", return_value=(1, {"7"})):
                with mock.patch.object(controller, "start_coherence_audit") as rerun:
                    controller.review_refinements()

    rerun.assert_called_once_with(force=True, mode="manual")


def test_manual_review_rechecks_when_decision_batch_empties_local_queue():
    from PySide6.QtWidgets import QDialog

    class _DB:
        def __init__(self):
            self.decisions = []

        def set_refinement_candidate_state(self, *_args):
            pass

        def upsert_coherence_review_decisions(self, _session_id, decisions):
            self.decisions.extend(decisions)

    class _Dialog:
        audioPreviewRequested = SimpleNamespace(connect=lambda *_args, **_kwargs: None)

        def __init__(self, _rows, _parent):
            pass

        def exec(self):
            return QDialog.Accepted

        def accepted_candidate_ids(self):
            return []

        def ignored_candidate_ids(self):
            return ["outlier:7:Oneshots:Kicks:"]

        def accepted_refinement_rows(self):
            return []

        def anchor_confirmed_record_ids(self):
            return []

    db = _DB()
    app = SimpleNamespace(
        engine=SimpleNamespace(db=db, session_id="s1"),
        footer=SimpleNamespace(set_coherence_state=mock.Mock()),
    )
    controller = CoherenceController()
    controller.app = app
    outlier_row = {
        "candidate_id": "outlier:7:Oneshots:Kicks:",
        "record_id": "7",
        "kind": "strong_outlier",
        "source_path": "D:/Samples/kick.wav",
        "file_hash": "h1",
        "current_audio_type": "Oneshots",
        "current_category": "Kicks",
        "current_subcategory": "",
        "suggested_audio_type": "Oneshots",
        "suggested_category": "Kicks",
        "suggested_subcategory": "",
    }

    with mock.patch("gui.widgets.refinement_popup.RefinementReviewDialog", _Dialog):
        with mock.patch.object(controller, "_review_rows", side_effect=[[outlier_row], []]):
            with mock.patch.object(controller, "_promote_matching_anchors_for_records", return_value=(0, set())):
                with mock.patch.object(controller, "start_coherence_audit") as rerun:
                    controller.review_refinements()

    assert len(db.decisions) == 1
    rerun.assert_called_once_with(force=True, mode="manual")


def test_promote_matching_anchor_uses_existing_generated_candidate_only():
    class _DB:
        def __init__(self):
            self.verified = []

        def list_coherence_results(self, _session_id):
            return [
                {"record_id": "7", "cluster_id": "cluster-a"},
                {"record_id": "8", "cluster_id": "cluster-b"},
            ]

        def list_anchor_candidates(self, _session_id, state=None):
            assert state == "candidate"
            return [
                {
                    "anchor_id": "anchor-a",
                    "cluster_id": "cluster-a",
                    "audio_type": "Oneshots",
                    "category": "Kicks",
                    "subcategory": "Generic",
                }
            ]

        def set_anchor_candidate_state(self, _session_id, anchor_ids, state):
            assert state == "verified"
            self.verified.extend(anchor_ids)

    db = _DB()
    records = [
        SimpleNamespace(staging_row_id=7, audio_type="Oneshots", category="Kicks", subcategory="Generic"),
        SimpleNamespace(staging_row_id=8, audio_type="Oneshots", category="Snares", subcategory=""),
    ]
    system_controller = SimpleNamespace(refresh_anchor_candidates=mock.Mock())
    app = SimpleNamespace(
        engine=SimpleNamespace(db=db, session_id="s1"),
        model=SimpleNamespace(records=records),
        system_controller=system_controller,
    )
    controller = CoherenceController()
    controller.app = app

    count, promoted_record_ids = controller._promote_matching_anchors_for_records(["7", "8"])

    assert count == 1
    assert promoted_record_ids == {"7"}
    assert db.verified == ["anchor-a"]
    system_controller.refresh_anchor_candidates.assert_called_once_with()


def test_review_refinements_includes_auto_staged_rows_with_accept_default():
    class _ReviewDB:
        def __init__(self):
            self.accepted = []
            self.ignored = []

        def list_refinement_candidates(self, session_id, state=None):
            assert session_id == "s1"
            if state == "auto_staged":
                return [{"candidate_id": "auto", "record_id": "7", "state": "auto_staged", "confidence_score": 1.0}]
            if state == "pending":
                return [{"candidate_id": "pending", "record_id": "8", "state": "pending", "confidence_score": 0.5}]
            return []

        def set_refinement_candidate_state(self, session_id, candidate_ids, state):
            if state == "accepted":
                self.accepted.extend(candidate_ids)
            elif state == "ignored":
                self.ignored.extend(candidate_ids)

    class _Dialog:
        rows_seen = []
        audioPreviewRequested = SimpleNamespace(connect=lambda *_args, **_kwargs: None)

        def __init__(self, rows, _parent):
            type(self).rows_seen = rows

        def exec(self):
            from PySide6.QtWidgets import QDialog

            return QDialog.Rejected

        def ignored_candidate_ids(self):
            return []

    db = _ReviewDB()
    footer = SimpleNamespace(states=[], set_coherence_state=lambda *args: footer.states.append(args))
    app = SimpleNamespace(
        engine=SimpleNamespace(db=db, session_id="s1"),
        model=_FakeModel(),
        footer=footer,
    )
    controller = CoherenceController()
    controller.app = app

    with mock.patch("gui.widgets.refinement_popup.RefinementReviewDialog", _Dialog):
        controller.review_refinements()

    assert [row["candidate_id"] for row in _Dialog.rows_seen] == ["auto", "pending"]
    assert _Dialog.rows_seen[0]["initial_action"] == "accept"
    assert _Dialog.rows_seen[1]["initial_action"] == "accept"


def test_review_refinements_weak_pending_rows_keep_reject_default():
    class _ReviewDB:
        def list_refinement_candidates(self, session_id, state=None):
            assert session_id == "s1"
            if state == "pending":
                return [{"candidate_id": "pending", "record_id": "8", "state": "pending", "confidence_score": 0.12}]
            return []

    class _Dialog:
        rows_seen = []
        audioPreviewRequested = SimpleNamespace(connect=lambda *_args, **_kwargs: None)

        def __init__(self, rows, _parent):
            type(self).rows_seen = rows

        def exec(self):
            from PySide6.QtWidgets import QDialog

            return QDialog.Rejected

    app = SimpleNamespace(
        engine=SimpleNamespace(db=_ReviewDB(), session_id="s1"),
        model=_FakeModel(),
        footer=SimpleNamespace(set_coherence_state=lambda *_args: None),
    )
    controller = CoherenceController()
    controller.app = app

    with mock.patch("gui.widgets.refinement_popup.RefinementReviewDialog", _Dialog):
        controller.review_refinements()

    assert _Dialog.rows_seen[0]["initial_action"] == "reject"


def test_review_refinements_cancel_does_not_persist_rejected_defaults():
    class _ReviewDB:
        def __init__(self):
            self.ignored = []

        def list_refinement_candidates(self, session_id, state=None):
            assert session_id == "s1"
            if state == "pending":
                return [{"candidate_id": "pending", "record_id": "8", "state": "pending", "confidence_score": 0.5}]
            return []

        def set_refinement_candidate_state(self, session_id, candidate_ids, state):
            if state == "ignored":
                self.ignored.extend(candidate_ids)

    class _Dialog:
        audioPreviewRequested = SimpleNamespace(connect=lambda *_args, **_kwargs: None)

        def __init__(self, _rows, _parent):
            pass

        def exec(self):
            from PySide6.QtWidgets import QDialog

            return QDialog.Rejected

        def ignored_candidate_ids(self):
            return ["pending"]

    db = _ReviewDB()
    app = SimpleNamespace(
        engine=SimpleNamespace(db=db, session_id="s1"),
        model=_FakeModel(),
        footer=SimpleNamespace(set_coherence_state=lambda *_args: None),
    )
    controller = CoherenceController()
    controller.app = app

    with mock.patch("gui.widgets.refinement_popup.RefinementReviewDialog", _Dialog):
        controller.review_refinements()

    assert db.ignored == []


def test_refinement_dialog_disables_edge_drag_autoscroll():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from gui.widgets.refinement_popup import RefinementReviewDialog

    app = QApplication.instance() or QApplication([])
    dialog = RefinementReviewDialog([{"candidate_id": str(idx), "record_id": str(idx)} for idx in range(40)])
    try:
        assert not dialog.table.hasAutoScroll()
    finally:
        dialog.deleteLater()


def test_auto_staged_refinements_uncategorized_directly_accepted():
    class _TestDB(_FakeDB):
        def __init__(self):
            super().__init__()
            self.rows[0]["current_category"] = "Uncategorized"
            self.candidate_states = []

        def set_refinement_candidate_state(self, session_id, candidate_ids, state):
            self.candidate_states.append((candidate_ids, state))

    model = _FakeModel()
    db = _TestDB()
    footer = SimpleNamespace(logs=[], log=lambda text: footer.logs.append(text))
    app = SimpleNamespace(
        engine=SimpleNamespace(db=db, session_id="s1"),
        model=model,
        footer=footer,
        view_controller=SimpleNamespace(update_library_views=lambda **_kwargs: None),
    )
    controller = CoherenceController()
    controller.app = app

    applied = controller.apply_auto_staged_refinements()

    assert applied == 1
    assert db.candidate_states == [(["c1"], "accepted")]


def test_find_audio_path_docked_mode():
    from pathlib import Path


    app = SimpleNamespace(
        open_library_workspace=mock.Mock(),
        search_controller=mock.Mock(searchFinished=mock.Mock()),
        dock_view=SimpleNamespace(),
        stack=SimpleNamespace(currentWidget=lambda: "some_other_widget"),
    )
    controller = CoherenceController()
    controller.app = app
    controller._select_library_path = mock.Mock()

    controller.find_audio_path("test_sample.wav")
    app.open_library_workspace.assert_called_once()


    app_docked = SimpleNamespace(
        open_library_workspace=mock.Mock(),
        search_controller=mock.Mock(searchFinished=mock.Mock()),
        dock_view=SimpleNamespace(),
    )
    app_docked.stack = SimpleNamespace(currentWidget=lambda: app_docked.dock_view)
    
    controller_docked = CoherenceController()
    controller_docked.app = app_docked
    controller_docked._select_library_path = mock.Mock()

    controller_docked.find_audio_path("test_sample.wav")
    app_docked.open_library_workspace.assert_not_called()
