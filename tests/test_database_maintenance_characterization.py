import unittest
import json
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from gui.core.workflow_controller import WorkflowController
from gui.core.workers import ScanWorker, StartupRestoreWorker
from unshuffle.persistence import get_local_db


class DatabaseMaintenanceLifecycleTests(unittest.TestCase):
    def test_scan_worker_prunes_after_writing_current_staging(self):
        calls = []

        class _DB:
            def register_session(self, *args, **kwargs):
                calls.append(("register_session", args, kwargs))

            def clear_staging(self, session_id):
                calls.append(("clear_staging", session_id))

            def ensure_verified_anchors_for_session(self, session_id):
                calls.append(("ensure_verified_anchors_for_session", session_id))

            def list_coherence_review_decisions(self, *args, **kwargs):
                return []

            def add_staging_records_bulk(self, session_id, rows):
                calls.append(("add_staging_records_bulk", session_id, rows))

            def prune_ephemeral_state(self, keep_session_ids, target_root=None):
                calls.append(("prune_ephemeral_state", set(keep_session_ids), target_root))

            def compact_if_worthwhile(self):
                calls.append(("compact_if_worthwhile",))
                return {"skipped": True, "reason": "below_threshold"}

            def close(self):
                calls.append(("close",))

        engine = mock.Mock()
        engine.session_id = "scan-session"
        engine.target_dir = Path("D:/Library")
        engine.prepare_plan.return_value = []

        worker = ScanWorker(engine, [Path("D:/Source")])
        with mock.patch("unshuffle.persistence.get_db", return_value=_DB()):
            worker.run()

        self.assertIn(("prune_ephemeral_state", {"scan-session"}, Path("D:/Library")), calls)
        self.assertNotIn(("compact_if_worthwhile",), calls)
        self.assertEqual(calls[-1], ("close",))

    def test_startup_restore_prunes_before_loading_and_uses_newest_fallback(self):
        calls = []
        payloads = []

        class _DB:
            def newest_restorable_staging_session(self, target_root=None):
                calls.append(("newest_restorable_staging_session", target_root))
                return "newest-session"

            def prune_ephemeral_state(self, keep_session_ids, target_root=None):
                calls.append(("prune_ephemeral_state", set(keep_session_ids), target_root))

            def close(self):
                calls.append(("close",))

        def _load_staging(target, session_id):
            calls.append(("load_staging_records", target, session_id))
            return [{"row_id": 1}]

        def _load_sources(target, session_id):
            calls.append(("load_session_sources", target, session_id))
            return ["D:/Source"]

        worker = StartupRestoreWorker("D:/Library", "")
        worker.finished.connect(payloads.append)
        with mock.patch("unshuffle.persistence.get_db", return_value=_DB()), \
             mock.patch("gui.utils.history.load_staging_records", side_effect=_load_staging), \
             mock.patch("gui.utils.history.load_session_sources", side_effect=_load_sources), \
             mock.patch("gui.utils.history.invalidate_history_cache") as invalidate, \
             mock.patch("gui.utils.session.plan_records_from_staging", return_value=["plan-record"]):
            worker.run()

        self.assertIn(("newest_restorable_staging_session", Path("D:/Library")), calls)
        self.assertIn(("prune_ephemeral_state", {"newest-session"}, Path("D:/Library")), calls)
        self.assertLess(
            calls.index(("prune_ephemeral_state", {"newest-session"}, Path("D:/Library"))),
            calls.index(("load_staging_records", "D:/Library", "newest-session")),
        )
        invalidate.assert_called_once_with("D:/Library")
        self.assertEqual(payloads[0]["session_id"], "newest-session")
        self.assertEqual(payloads[0]["plan"], ["plan-record"])

    def test_startup_restore_falls_back_when_requested_session_has_no_staging(self):
        calls = []
        payloads = []

        class _DB:
            conn = None

            def get_staging_records(self, session_id):
                calls.append(("get_staging_records", session_id))
                return []

            def newest_restorable_staging_session(self, target_root=None):
                calls.append(("newest_restorable_staging_session", target_root))
                return "scan-session"

            def prune_ephemeral_state(self, keep_session_ids, target_root=None):
                calls.append(("prune_ephemeral_state", set(keep_session_ids), target_root))

            def close(self):
                calls.append(("close",))

        def _load_staging(target, session_id):
            calls.append(("load_staging_records", target, session_id))
            return [{"row_id": 1}]

        worker = StartupRestoreWorker("D:/Library", "build-session")
        worker.finished.connect(payloads.append)
        with mock.patch("unshuffle.persistence.get_db", return_value=_DB()), \
             mock.patch("gui.utils.history.load_staging_records", side_effect=_load_staging), \
             mock.patch("gui.utils.history.load_session_sources", return_value=["D:/Source"]), \
             mock.patch("gui.utils.history.invalidate_history_cache"), \
             mock.patch("gui.utils.session.plan_records_from_staging", return_value=["plan-record"]):
            worker.run()

        self.assertIn(("get_staging_records", "build-session"), calls)
        self.assertIn(("newest_restorable_staging_session", Path("D:/Library")), calls)
        self.assertIn(("prune_ephemeral_state", {"scan-session"}, Path("D:/Library")), calls)
        self.assertIn(("load_staging_records", "D:/Library", "scan-session"), calls)
        self.assertEqual(payloads[0]["session_id"], "scan-session")

    def test_startup_restore_falls_back_to_target_local_staging_session(self):
        import tempfile

        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "Library"
            source = target / "source.wav"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"sound")
            db = get_local_db(target)
            try:
                db.register_session("local-session", source.parent, target, "pending")
                db.set_session_sources("local-session", [source.parent])
                db.add_staging_records_bulk(
                    "local-session",
                    [(
                        1,
                        str(source),
                        "source.wav",
                        "Pack",
                        "Kicks",
                        "",
                        "Oneshots",
                        "",
                        "1.00",
                        1.0,
                        "abc",
                        "",
                        None,
                        None,
                        0,
                    )],
                )
            finally:
                db.close()

            payloads = []
            worker = StartupRestoreWorker(str(target), "missing-build-session")
            worker.finished.connect(payloads.append)
            worker.run()

            self.assertEqual(payloads[0]["session_id"], "local-session")
            self.assertEqual(payloads[0]["db_scope"], "local")
            self.assertEqual(payloads[0]["sources"], [str(source.parent)])
            self.assertEqual(len(payloads[0]["plan"]), 1)
            self.assertEqual(str(payloads[0]["plan"][0].source_path), str(source))

    def test_restore_plan_records_skip_reserved_internal_paths(self):
        from gui.utils.session import plan_records_from_staging

        rows = [
            {
                "row_id": 1,
                "source_path": "D:/Library/.unshuffle/lock.json.tmp",
                "sample_name": "lock.json.tmp",
                "pack": "Internal",
                "category": "FX",
                "subcategory": "",
                "audio_type": "Oneshots",
            },
            {
                "row_id": 2,
                "source_path": "D:/Library/Pack/kick.wav",
                "sample_name": "kick.wav",
                "pack": "Pack",
                "category": "Kicks",
                "subcategory": "",
                "audio_type": "Oneshots",
            },
        ]

        plan = plan_records_from_staging(rows)

        self.assertEqual(len(plan), 1)
        self.assertEqual(plan[0].source_path.as_posix(), "D:/Library/Pack/kick.wav")

    def test_restore_plan_records_keep_output_category_named_paths(self):
        from gui.utils.session import plan_records_from_staging

        rows = [
            {
                "row_id": 1,
                "source_path": "D:/Library/Non-Audio Assets/Pack/manual.pdf",
                "sample_name": "manual.pdf",
                "pack": "Pack",
                "category": "Non-Audio Assets",
                "subcategory": "",
                "audio_type": "Non-Audio Assets",
            },
            {
                "row_id": 2,
                "source_path": "D:/Library/Organized/kick.wav",
                "sample_name": "kick.wav",
                "pack": "Organized",
                "category": "Kicks",
                "subcategory": "",
                "audio_type": "Oneshots",
            },
            {
                "row_id": 3,
                "source_path": "D:/Library/Uncategorized/mystery.wav",
                "sample_name": "mystery.wav",
                "pack": "Uncategorized",
                "category": "Uncategorized",
                "subcategory": "",
                "audio_type": "Oneshots",
            },
            {
                "row_id": 4,
                "source_path": "D:/Library/__MACOSX/._junk.wav",
                "sample_name": "._junk.wav",
                "pack": "Internal",
                "category": "FX",
                "subcategory": "",
                "audio_type": "Oneshots",
            },
        ]

        plan = plan_records_from_staging(rows)

        self.assertEqual(
            [record.source_path.as_posix() for record in plan],
            [
                "D:/Library/Non-Audio Assets/Pack/manual.pdf",
                "D:/Library/Organized/kick.wav",
                "D:/Library/Uncategorized/mystery.wav",
            ],
        )

    def test_successful_build_prunes_ephemeral_state_without_fallback(self):
        db = mock.Mock()
        engine = SimpleNamespace(db=db, target_dir=Path("D:/Library"), session_id="build-session", session_source_roots=[])
        app = SimpleNamespace(settings=mock.Mock())
        controller = WorkflowController(engine, mock.Mock(), mock.Mock(), None)
        controller.app = app

        with mock.patch.object(controller, "_enter_build_handover_state") as handover, \
             mock.patch("PySide6.QtWidgets.QMessageBox.information") as info:
            controller.handle_commit_finished(
                {
                    "session_id": "build-session",
                    "copied": 1,
                    "duplicates": 0,
                    "failed": 0,
                    "stale": 0,
                    "interrupted": 0,
                }
            )

        handover.assert_called_once()
        info.assert_called_once()
        self.assertEqual(info.call_args.args[1:3], ("Build Complete", "Build complete."))
        db.prune_ephemeral_state.assert_called_once_with(
            {"build-session"},
            target_root=Path("D:/Library"),
            use_restorable_fallback=False,
        )
        app.settings.setValue.assert_any_call("last_target", str(Path("D:/Library")))

    def test_successful_build_enters_handover_without_starting_target_scan(self):
        db = mock.Mock()
        engine = SimpleNamespace(db=db, target_dir=Path("D:/Target"), session_id="build-session", session_source_roots=[])
        controller = WorkflowController(engine, mock.Mock(), mock.Mock(), None)
        controller.app = SimpleNamespace(settings=mock.Mock(), history_page=mock.Mock())
        controller.start_scan = mock.Mock()
        controller._enter_build_handover_state = mock.Mock()
        controller._last_build_options = {"target": "D:/Target", "move": False}

        with mock.patch("PySide6.QtWidgets.QMessageBox.information"):
            controller.handle_commit_finished(
                {
                    "session_id": "build-session",
                    "copied": 1,
                    "duplicates": 0,
                    "failed": 0,
                    "stale": 0,
                    "interrupted": 0,
                }
            )

        controller.start_scan.assert_not_called()
        controller._enter_build_handover_state.assert_called_once()

    def test_move_build_handover_clears_workbench_and_reports_leftovers(self):
        import tempfile

        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as target_dir:
            source = Path(source_dir)
            target = Path(target_dir)
            (source / "left.wav").write_bytes(b"abc")
            (source / "DO_NOT_DELETE_unshuffle").mkdir()
            (source / "DO_NOT_DELETE_unshuffle" / "internal.db").write_bytes(b"ignored")

            class _Footer:
                def __init__(self):
                    self.handover = None
                    self.status = None
                    self.count = None

                def set_status(self, text):
                    self.status = text

                def set_count(self, text):
                    self.count = text

                def set_build_handover_state(self, *args, **kwargs):
                    self.handover = (args, kwargs)

                def log(self, *_args, **_kwargs):
                    pass

                def clear_build_handover_state(self):
                    pass

            app = SimpleNamespace(
                undo_stack=None,
                data_manager=SimpleNamespace(sync_record_to_db=None),
                footer=_Footer(),
                proxy_model=mock.Mock(),
                search_controller=SimpleNamespace(clear_query_state=mock.Mock()),
                view_controller=SimpleNamespace(update_library_views=mock.Mock()),
                set_runtime_context=mock.Mock(side_effect=lambda *, model: setattr(app, "model", model)),
            )
            engine = SimpleNamespace(target_dir=target, session_id="move-session", session_source_roots=[source])
            controller = WorkflowController(engine, mock.Mock(), mock.Mock(), None)
            controller.app = app
            controller._last_build_options = {"target": str(target), "move": True}

            controller._enter_build_handover_state(
                {
                    "session_id": "move-session",
                    "copied": 10,
                    "duplicates": 0,
                    "failed": 0,
                    "move": True,
                },
                "Moved 10 of 10 files.",
            )

        self.assertEqual(app.model.records, [])
        self.assertEqual(app.footer.status, "Move complete")
        self.assertEqual(app.footer.count, "0 files ready")
        text = app.footer.handover[0][0]
        self.assertIn("Move complete", text)
        self.assertIn("10 files moved", text)
        self.assertIn("Source has 1 file / 3 B remaining", text)
        self.assertTrue(app.footer.handover[1]["can_open_target"])
        self.assertTrue(app.footer.handover[1]["can_open_source"])
        self.assertTrue(app.footer.handover[1]["can_undo"])

    def test_copy_build_handover_keeps_workbench_and_hides_undo(self):
        records = [SimpleNamespace(source_path=Path("D:/Source/kick.wav"))]

        class _Footer:
            def __init__(self):
                self.handover = None
                self.count = None

            def set_status(self, _text):
                pass

            def set_count(self, text):
                self.count = text

            def set_build_handover_state(self, *args, **kwargs):
                self.handover = (args, kwargs)

            def log(self, *_args, **_kwargs):
                pass

        app = SimpleNamespace(model=SimpleNamespace(records=records), footer=_Footer())
        engine = SimpleNamespace(target_dir=Path("D:/Target"), session_id="copy-session", session_source_roots=[Path("D:/Source")])
        controller = WorkflowController(engine, mock.Mock(), mock.Mock(), None)
        controller.app = app
        controller._last_build_options = {"target": "D:/Target", "move": False}

        controller._enter_build_handover_state(
            {
                "session_id": "copy-session",
                "copied": 1,
                "duplicates": 0,
                "failed": 0,
                "move": False,
            },
            "Copied 1 of 1 files.",
        )

        self.assertEqual(app.model.records, records)
        self.assertEqual(app.footer.count, "1 files ready")
        self.assertIn("Copy complete", app.footer.handover[0][0])
        self.assertFalse(app.footer.handover[1]["can_undo"])

    def test_build_handover_persists_for_relaunch(self):
        from gui.core.workflow_handover import BUILD_HANDOVER_STATE_KEY

        class _Settings:
            def __init__(self):
                self.values = {}

            def setValue(self, key, value):
                self.values[key] = value

        class _Footer:
            def set_status(self, _text):
                pass

            def set_count(self, _text):
                pass

            def set_build_handover_state(self, *_args, **_kwargs):
                pass

            def log(self, *_args, **_kwargs):
                pass

        settings = _Settings()
        app = SimpleNamespace(model=SimpleNamespace(records=[]), footer=_Footer(), settings=settings)
        engine = SimpleNamespace(target_dir=Path("D:/Target"), session_id="move-session", session_source_roots=[Path("D:/Source")])
        controller = WorkflowController(engine, mock.Mock(), mock.Mock(), None)
        controller.app = app
        controller._last_build_options = {"target": "D:/Target", "move": False}

        controller._enter_build_handover_state(
            {
                "session_id": "move-session",
                "copied": 3,
                "duplicates": 1,
                "failed": 0,
                "move": False,
            },
            "Copied 3 of 3 files.",
        )

        payload = json.loads(settings.values[BUILD_HANDOVER_STATE_KEY])
        self.assertEqual(payload["mode"], "copy")
        self.assertEqual(Path(payload["target_path"]).as_posix(), "D:/Target")
        self.assertEqual([Path(path).as_posix() for path in payload["source_paths"]], ["D:/Source"])
        self.assertEqual(payload["session_id"], "move-session")

    def test_restore_build_handover_state_repaints_footer_buttons(self):
        from gui.core.workflow_handover import BUILD_HANDOVER_STATE_KEY

        class _Settings:
            def __init__(self, payload):
                self.payload = payload

            def value(self, key, default=""):
                if key == BUILD_HANDOVER_STATE_KEY:
                    return self.payload
                return default

        class _Footer:
            def __init__(self):
                self.status = None
                self.count = None
                self.handover = None

            def set_status(self, text):
                self.status = text

            def set_count(self, text):
                self.count = text

            def set_build_handover_state(self, *args, **kwargs):
                self.handover = (args, kwargs)

        payload = json.dumps(
            {
                "mode": "move",
                "source_paths": ["D:/Source", "E:/Other"],
                "target_path": "D:/Target",
                "moved_or_copied_count": 8,
                "remaining_source_file_count": 0,
                "remaining_source_bytes": 0,
                "session_id": "move-session",
            }
        )
        app = SimpleNamespace(settings=_Settings(payload), footer=_Footer(), model=SimpleNamespace(records=[]))
        controller = WorkflowController(mock.Mock(), mock.Mock(), mock.Mock(), None)
        controller.app = app

        self.assertTrue(controller.restore_build_handover_state())
        self.assertEqual(app._build_handover_state["session_id"], "move-session")
        self.assertEqual(app.footer.status, "Move complete")
        self.assertEqual(app.footer.count, "0 files ready")
        text = app.footer.handover[0][0]
        self.assertIn("Move complete", text)
        self.assertTrue(app.footer.handover[1]["can_open_target"])
        self.assertTrue(app.footer.handover[1]["can_open_source"])
        self.assertTrue(app.footer.handover[1]["can_undo"])

    def test_build_handover_actions_open_paths_and_undo_session(self):
        worker_manager = mock.Mock()
        controller = WorkflowController(mock.Mock(), worker_manager, mock.Mock(), None)
        app = SimpleNamespace(
            _build_handover_state={
                "target_path": "D:/Target",
                "source_paths": ["D:/Source", "E:/Other"],
                "session_id": "move-session",
            },
            footer=SimpleNamespace(clear_build_handover_state=mock.Mock()),
        )
        controller.app = app

        with mock.patch("gui.utils.ui_helpers.open_explorer_path") as open_path:
            controller.open_build_handover_target()
            controller.open_build_handover_source()

        open_path.assert_has_calls(
            [
                mock.call(app, "D:/Target"),
                mock.call(app, "D:/Source"),
            ]
        )

        controller.undo_build_handover()

        worker_manager.start_undo.assert_called_once_with("move-session", confirm_preserved=False)
        self.assertIsNone(app._build_handover_state)

    def test_cancelled_build_starts_undo_for_committed_records(self):
        db = mock.Mock()
        worker_manager = mock.Mock()
        engine = SimpleNamespace(db=db, target_dir=Path("D:/Target"), session_id="build-session", session_source_roots=[])
        controller = WorkflowController(engine, worker_manager, mock.Mock(), None)
        controller.app = SimpleNamespace(settings=mock.Mock(), history_page=mock.Mock(), footer=mock.Mock())
        controller._last_build_options = {"target": "D:/Target", "move": False}

        controller.handle_commit_finished(
            {
                "session_id": "build-session",
                "copied": 2,
                "duplicates": 0,
                "failed": 0,
                "stale": 0,
                "interrupted": 5,
                "cancelled": True,
            }
        )

        rollback = controller._cancelled_build_rollback
        self.assertIsNotNone(rollback)
        worker_manager.start_undo.assert_called_once_with("build-session", confirm_preserved=False)
        if rollback is not None:
            self.assertEqual(rollback["committed_count"], 2)
        controller.app.footer.set_status.assert_called_with("Undoing canceled build...")

    def test_cancelled_build_rollback_success_uses_cancel_message(self):
        controller = WorkflowController(mock.Mock(), mock.Mock(), mock.Mock(), None)
        controller._cancelled_build_rollback = {
            "session_id": "build-session",
            "committed_count": 2,
        }
        controller.app = SimpleNamespace(
            settings=mock.Mock(),
            history_page=SimpleNamespace(mark_undone=mock.Mock(), refresh_from_target=mock.Mock()),
            footer=mock.Mock(),
        )

        with mock.patch("PySide6.QtWidgets.QMessageBox.information") as info:
            controller.handle_undo_finished(
                {
                    "session_id": "build-session",
                    "target_root": "D:/Target",
                    "undone": 2,
                    "already_undone": 0,
                    "sources": ["D:/Source"],
                }
            )

        info.assert_called_once()
        self.assertIn("Build canceled.", info.call_args.args[2])
        controller.app.footer.set_status.assert_called_with("Build canceled. Changes were undone.")
        self.assertIsNone(controller._cancelled_build_rollback)

    def test_prompt_scan_restored_source_reuses_open_session_prompt(self):
        controller = WorkflowController(mock.Mock(), mock.Mock(), mock.Mock(), None)
        settings = mock.Mock()
        controller.app = SimpleNamespace(settings=settings)
        controller.start_scan = mock.Mock()

        class _Dialog:
            Information = object()
            ActionRole = object()
            RejectRole = object()
            scan_button = object()

            def __init__(self, *_args, **_kwargs):
                pass

            def setIcon(self, *_args):
                pass

            def setWindowTitle(self, *_args):
                pass

            def setText(self, text):
                self.text = text

            def addButton(self, label, _role):
                if label == "Scan Source":
                    return self.scan_button
                return object()

            def exec(self):
                pass

            def clickedButton(self):
                return self.scan_button

        with mock.patch("PySide6.QtWidgets.QMessageBox", _Dialog):
            selected = controller._prompt_scan_restored_source("Undo complete.", "D:/Source")

        self.assertTrue(selected)
        settings.setValue.assert_has_calls(
            [
                mock.call("last_library_target", "D:/Source"),
                mock.call("last_scan_source", "D:/Source"),
                mock.call("last_target", "D:/Source"),
            ]
        )
        controller.start_scan.assert_called_once_with(
            ["D:/Source"],
            append=False,
            last_target="D:/Source",
            require_clear_draft=False,
        )

    def test_successful_move_undo_persists_restored_source_as_startup_target(self):
        import json
        from gui.core.settings_controller import STARTUP_LAUNCHER_LAST_CHOICE_KEY

        controller = WorkflowController(mock.Mock(), mock.Mock(), mock.Mock(), None)
        settings = mock.Mock()
        controller.app = SimpleNamespace(
            settings=settings,
            history_page=SimpleNamespace(mark_undone=mock.Mock(), refresh_from_target=mock.Mock()),
        )
        controller._prompt_scan_restored_source = mock.Mock(return_value=False)

        with mock.patch("PySide6.QtWidgets.QMessageBox.information"):
            controller.handle_undo_finished(
                {
                    "session_id": "move-session",
                    "target_root": "D:/Target",
                    "undone": 4,
                    "already_undone": 0,
                    "sources": ["D:/Source"],
                }
            )

        settings.setValue.assert_any_call("last_library_target", "D:/Source")
        settings.setValue.assert_any_call("last_scan_source", "D:/Source")
        settings.setValue.assert_any_call("last_target", "D:/Source")
        startup_payload = next(
            call.args[1]
            for call in settings.setValue.call_args_list
            if call.args[0] == STARTUP_LAUNCHER_LAST_CHOICE_KEY
        )
        self.assertEqual(
            json.loads(startup_payload),
            {
                "mode": "restore",
                "target": "D:/Source",
                "session_id": "move-session",
                "roots": ["D:/Source"],
            },
        )

    def test_category_build_target_can_promote_to_parent_library_root(self):
        controller = WorkflowController(mock.Mock(), mock.Mock(), mock.Mock(), None)
        controller.app = SimpleNamespace()

        class _Dialog:
            Warning = object()
            AcceptRole = object()
            RejectRole = object()
            use_parent = object()

            def __init__(self, *_args, **_kwargs):
                pass

            def setIcon(self, *_args):
                pass

            def setWindowTitle(self, *_args):
                pass

            def setText(self, text):
                self.text = text

            def addButton(self, label, _role):
                if label == "Use Parent":
                    return self.use_parent
                return object()

            def exec(self):
                pass

            def clickedButton(self):
                return self.use_parent

        with mock.patch("PySide6.QtWidgets.QMessageBox", _Dialog):
            target = controller._confirm_category_target_root(Path("D:/Library/Oneshots"))

        self.assertEqual(target, Path("D:/Library"))

    def test_partial_build_error_refreshes_history_for_committed_records(self):
        db = mock.Mock()
        engine = SimpleNamespace(
            db=db,
            target_dir=Path("D:/Library"),
            session_id="build-session",
            session_source_roots=[Path("D:/Source")],
        )
        history_page = mock.Mock()
        app = SimpleNamespace(settings=mock.Mock(), history_page=history_page)
        controller = WorkflowController(engine, mock.Mock(), mock.Mock(), None)
        controller.app = app
        controller._pending_build_skipped_duplicates = {
            "skipped_duplicates": 384,
            "skipped_session_duplicates": 384,
            "skipped_library_duplicates": 0,
        }

        with mock.patch("PySide6.QtWidgets.QMessageBox.warning") as warning, \
             mock.patch("gui.utils.history.invalidate_history_cache") as invalidate:
            controller.handle_commit_finished(
                {
                    "session_id": "build-session",
                    "copied": 4774,
                    "duplicates": 0,
                    "failed": 21,
                    "stale": 0,
                    "interrupted": 0,
                    "move": True,
                    "error": "21 file(s) failed during build.",
                }
            )

        db.prune_ephemeral_state.assert_not_called()
        app.settings.setValue.assert_any_call("last_scan_session_id", "build-session")
        app.settings.setValue.assert_any_call("last_target", str(Path("D:/Library")))
        invalidate.assert_called_once_with(str(Path("D:/Library")))
        history_page.refresh_from_target.assert_called_once_with(str(Path("D:/Library")))
        message = warning.call_args.args[2]
        self.assertIn("384 duplicate source file(s) were skipped during scan", message)
        self.assertIn("not part of this undo session", message)

    def test_build_error_message_lists_failed_files_and_retryable_records(self):
        import tempfile

        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "bad.wav"
            source.write_bytes(b"still here")
            record = SimpleNamespace(source_path=source)
            controller = WorkflowController(None, mock.Mock(), mock.Mock(), None)
            controller.app = SimpleNamespace(model=SimpleNamespace(records=[record]))
            result = {
                "failed_records": [
                    {
                        "source_path": str(source),
                        "target_path": "D:/Library/Oneshots/Kicks/bad.wav",
                    }
                ],
                "failed_record_count": 1,
            }

            message = controller._build_error_message(result, ["Moved 0 of 1 files.", "Failed 1."], "1 file(s) failed during build.")

            self.assertIn("Moved 0 of 1 files.\nFailed 1.", message)
            self.assertIn(str(source), message)
            self.assertIn("D:/Library/Oneshots/Kicks/bad.wav", message)
            self.assertIn("You can retry 1 failed file now.", message)
            self.assertEqual(controller._retryable_failed_records(result), [record])


if __name__ == "__main__":
    unittest.main()
