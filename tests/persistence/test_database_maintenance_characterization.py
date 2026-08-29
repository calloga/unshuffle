import unittest
import json
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from PySide6.QtCore import QObject

from gui.core import workflow_build_completion
from gui.core.workflow_controller import WorkflowController
from gui.core.workflow_records import build_result_compact_lines
from gui.core.workers import (
    ScanWorker,
    StartupRestoreWorker,
    _staging_session_has_rows,
    _staging_session_row_count,
    _db_native_scan_available,
    _streaming_scan_enabled,
)
from unshuffle.core import PlanRecord
from unshuffle.persistence import get_local_db


class DatabaseMaintenanceLifecycleTests(unittest.TestCase):
    def test_streaming_scan_is_default_with_explicit_rollback_override(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertTrue(_streaming_scan_enabled())
        for value in ("0", "false", "no", "off"):
            with self.subTest(value=value), mock.patch.dict(
                "os.environ", {"UNSHUFFLE_STREAMING_SCAN": value}, clear=True
            ):
                self.assertFalse(_streaming_scan_enabled())

    def test_streaming_rollback_selects_legacy_path_for_capable_database(self):
        database = SimpleNamespace(
            iter_classified_scan_session_items=mock.Mock(),
            classified_scan_session_stats=mock.Mock(),
            add_staging_records_iter=mock.Mock(),
            iter_classified_append_items=mock.Mock(),
        )
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertTrue(_db_native_scan_available(database, append=False))
            self.assertTrue(_db_native_scan_available(database, append=True))
        with mock.patch.dict(
            "os.environ", {"UNSHUFFLE_STREAMING_SCAN": "0"}, clear=True
        ):
            self.assertFalse(_db_native_scan_available(database, append=False))
            self.assertFalse(_db_native_scan_available(database, append=True))

    def test_restore_staging_helpers_use_bounded_sql_queries(self):
        connection = mock.Mock()
        connection.execute.side_effect = [
            mock.Mock(fetchone=mock.Mock(return_value=(1,))),
            mock.Mock(fetchone=mock.Mock(return_value=(12,))),
        ]
        db = SimpleNamespace(conn=connection)

        self.assertTrue(_staging_session_has_rows(db, "session"))
        self.assertEqual(_staging_session_row_count(db, "session"), 12)
        self.assertIn("LIMIT 1", connection.execute.call_args_list[0].args[0])
        self.assertIn("COUNT(*)", connection.execute.call_args_list[1].args[0])

    def test_restore_staging_helpers_keep_legacy_database_fallback(self):
        db = SimpleNamespace(
            conn=None,
            get_staging_records=mock.Mock(return_value=[{"row_id": 1}]),
        )

        self.assertTrue(_staging_session_has_rows(db, "session"))
        self.assertEqual(_staging_session_row_count(db, "session"), 1)

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
        engine.db = None
        engine.session_id = "scan-session"
        engine.target_dir = Path("D:/Library")
        engine.prepare_plan.return_value = []

        worker = ScanWorker(engine, [Path("D:/Source")])
        with mock.patch("unshuffle.persistence.get_db", return_value=_DB()):
            worker.run()

        self.assertIn(("prune_ephemeral_state", {"scan-session"}, Path("D:/Library")), calls)
        self.assertNotIn(("compact_if_worthwhile",), calls)
        self.assertEqual(calls[-1], ("close",))

    def test_append_scan_inserts_only_new_staging_rows(self):
        calls = []
        connection = mock.Mock()
        connection.execute.return_value.fetchone.return_value = (9,)

        class _DB:
            conn = connection

            def register_session(self, *args, **kwargs):
                calls.append(("register_session", args, kwargs))

            def clear_staging(self, session_id):
                calls.append(("clear_staging", session_id))

            def list_coherence_review_decisions(self, *args, **kwargs):
                return []

            def add_staging_records_bulk(self, session_id, rows):
                calls.append(("add_staging_records_bulk", session_id, rows))

            def prune_ephemeral_state(self, keep_session_ids, target_root=None):
                pass

        record = PlanRecord(Path("D:/Added/kick.wav"), "Pack", "Kicks", "Oneshots", "0.9")
        engine = mock.Mock()
        engine.db = _DB()
        engine.session_id = "scan-session"
        engine.target_dir = Path("D:/Library")
        engine.prepare_plan.return_value = [record]
        worker = ScanWorker(engine, [Path("D:/Added")], append=True)

        worker.run()

        self.assertNotIn(("clear_staging", "scan-session"), calls)
        inserted = next(call for call in calls if call[0] == "add_staging_records_bulk")
        self.assertEqual(len(inserted[2]), 1)
        self.assertEqual(inserted[2][0][0], 10)

    def test_scan_worker_uses_operation_specific_session_phase(self):
        engine = mock.Mock()

        fresh = ScanWorker(engine, [Path("D:/Source")])
        append = ScanWorker(engine, [Path("D:/Added")], append=True)
        refresh = ScanWorker(
            engine,
            [Path("D:/Source")],
            append=False,
            session_phase="Refreshing Session",
        )

        self.assertEqual(fresh.session_phase, "Creating Session")
        self.assertEqual(append.session_phase, "Updating Session")
        self.assertEqual(refresh.session_phase, "Refreshing Session")
        self.assertFalse(refresh.append)

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
        self.assertEqual(payloads[0]["record_count"], 1)
        self.assertNotIn("plan", payloads[0])

    def test_startup_restore_falls_back_when_requested_session_has_no_staging(self):
        calls = []
        payloads = []

        class _DB:
            conn = None

            def get_staging_records(self, session_id):
                calls.append(("get_staging_records", session_id))
                return [] if session_id == "build-session" else [{"row_id": 1}]

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

        self.assertIn(("newest_restorable_staging_session", Path("D:/Library")), calls)
        self.assertIn(("prune_ephemeral_state", {"scan-session"}, Path("D:/Library")), calls)
        self.assertIn(("get_staging_records", "scan-session"), calls)
        self.assertNotIn(("load_staging_records", "D:/Library", "scan-session"), calls)
        self.assertEqual(payloads[0]["session_id"], "scan-session")
        self.assertEqual(payloads[0]["record_count"], 1)

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
        self.assertEqual(payloads[0]["record_count"], 1)
        self.assertNotIn("plan", payloads[0])

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

    def test_successful_build_completion_uses_worker_finalization_without_gui_prune(self):
        db = mock.Mock()
        engine = SimpleNamespace(db=db, target_dir=Path("D:/Library"), session_id="build-session", session_source_roots=[])
        operation_monitor = mock.Mock(active=True)
        app = SimpleNamespace(settings=mock.Mock(), operation_monitor=operation_monitor)
        controller = WorkflowController(engine, mock.Mock(), mock.Mock(), None)
        controller.app = app
        events = []

        with mock.patch.object(
            controller,
            "_enter_build_handover_state",
            side_effect=lambda *_args: events.append("handover"),
        ) as handover, mock.patch(
            "PySide6.QtWidgets.QMessageBox.information",
            side_effect=lambda *_args: events.append("alert"),
        ) as info:
            operation_monitor.finish.side_effect = lambda *_args: events.append("monitor-finished")
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
        self.assertEqual(events, ["handover", "monitor-finished", "alert"])
        db.prune_ephemeral_state.assert_not_called()
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

    def test_move_build_handover_uses_worker_footprint_without_rescanning_sources(self):
        from gui.core import workflow_handover

        footer = SimpleNamespace(
            set_status=mock.Mock(),
            set_count=mock.Mock(),
            set_build_handover_state=mock.Mock(),
            log=mock.Mock(),
        )
        app = SimpleNamespace(model=SimpleNamespace(rowCount=lambda: 0), footer=footer, settings=mock.Mock())
        engine = SimpleNamespace(
            target_dir=Path("D:/Target"),
            session_id="move-session",
            session_source_roots=[Path("D:/Source")],
        )
        controller = WorkflowController(engine, mock.Mock(), mock.Mock(), None)
        controller.app = app
        controller._last_build_options = {"target": "D:/Target", "move": True}

        with mock.patch.object(workflow_handover, "remaining_source_footprint") as footprint:
            controller._enter_build_handover_state(
                {
                    "session_id": "move-session",
                    "copied": 10,
                    "move": True,
                    "remaining_source_file_count": 3,
                    "remaining_source_bytes": 4096,
                },
                "Moved 10 files.",
            )

        footprint.assert_not_called()
        self.assertEqual(app._build_handover_state["remaining_source_file_count"], 3)
        self.assertEqual(app._build_handover_state["remaining_source_bytes"], 4096)

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

    def test_copy_build_handover_uses_cached_model_count_when_record_store_is_closed(self):
        class _ClosedRecords:
            def __len__(self):
                raise RuntimeError("database handle is closed")

        class _Footer:
            def __init__(self):
                self.count = None

            def set_status(self, _text):
                pass

            def set_count(self, text):
                self.count = text

            def set_build_handover_state(self, *_args, **_kwargs):
                pass

            def log(self, *_args, **_kwargs):
                pass

        app = SimpleNamespace(
            model=SimpleNamespace(rowCount=lambda: 11925, records=_ClosedRecords()),
            footer=_Footer(),
        )
        engine = SimpleNamespace(
            target_dir=Path("D:/Target"),
            session_id="copy-session",
            session_source_roots=[Path("D:/Source")],
        )
        controller = WorkflowController(engine, mock.Mock(), mock.Mock(), None)
        controller.app = app
        controller._last_build_options = {"target": "D:/Target", "move": False}

        controller._enter_build_handover_state(
            {
                "session_id": "copy-session",
                "copied": 11846,
                "duplicates": 0,
                "failed": 0,
                "move": False,
            },
            "Copied 11846 files.",
        )

        self.assertEqual(app.footer.count, "11925 files ready")
        self.assertEqual(app._build_handover_state["workbench_record_count"], 11925)

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

    def test_prompt_scan_restored_sources_scans_all_restored_sources(self):
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
                if label == "Scan Sources":
                    return self.scan_button
                return object()

            def exec(self):
                pass

            def clickedButton(self):
                return self.scan_button

        with mock.patch("PySide6.QtWidgets.QMessageBox", _Dialog):
            selected = controller._prompt_scan_restored_sources("Undo complete.", ["D:/Source", "E:/Other"])

        self.assertTrue(selected)
        settings.setValue.assert_has_calls(
            [
                mock.call("last_library_target", "D:/Source"),
                mock.call("last_scan_source", "D:/Source"),
                mock.call("last_target", "D:/Source"),
            ]
        )
        controller.start_scan.assert_called_once_with(
            ["D:/Source", "E:/Other"],
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
        controller._prompt_scan_restored_sources = mock.Mock(return_value=False)

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

    def test_successful_undo_rebinds_session_before_offering_source_scan(self):
        controller = WorkflowController(mock.Mock(), mock.Mock(), mock.Mock(), None)
        calls = []

        class _ClosedStore:
            @property
            def conn(self):
                raise RuntimeError("database is closed")

        controller.app = SimpleNamespace(
            settings=mock.Mock(),
            history_page=SimpleNamespace(mark_undone=mock.Mock(), refresh_from_target=mock.Mock()),
            model=SimpleNamespace(store=_ClosedStore()),
            current_records=mock.Mock(side_effect=lambda: calls.append("rebind")),
        )
        controller._prompt_scan_restored_sources = mock.Mock(
            side_effect=lambda *_args: calls.append("prompt") or False
        )

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

        self.assertEqual(calls, ["rebind", "prompt"])

    def test_successful_move_undo_persists_all_restored_sources_as_startup_roots(self):
        import json
        from gui.core.settings_controller import STARTUP_LAUNCHER_LAST_CHOICE_KEY

        controller = WorkflowController(mock.Mock(), mock.Mock(), mock.Mock(), None)
        settings = mock.Mock()
        controller.app = SimpleNamespace(
            settings=settings,
            history_page=SimpleNamespace(mark_undone=mock.Mock(), refresh_from_target=mock.Mock()),
        )
        controller._prompt_scan_restored_sources = mock.Mock(return_value=False)

        with mock.patch("PySide6.QtWidgets.QMessageBox.information"):
            controller.handle_undo_finished(
                {
                    "session_id": "move-session",
                    "target_root": "D:/Target",
                    "undone": 4,
                    "already_undone": 0,
                    "sources": ["D:/Source", "E:/Other"],
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
                "roots": ["D:/Source", "E:/Other"],
            },
        )
        controller._prompt_scan_restored_sources.assert_called_once_with(
            "Undo complete. 4 item(s) undone.",
            ["D:/Source", "E:/Other"],
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
                        "error": "[WinError 32] The process cannot access the file",
                    }
                ],
                "failed_record_count": 1,
            }

            message = controller._build_error_message(result, ["0/1 moved", "1 failed"], "1 file(s) failed during build.")

            self.assertIn("0/1 moved\n1 failed", message)
            self.assertIn("Error file:", message)
            self.assertIn(str(source), message)
            self.assertIn("D:/Library/Oneshots/Kicks/bad.wav", message)
            self.assertIn("Reason: [WinError 32] The process cannot access the file", message)
            self.assertIn("Retry available: 1 file", message)
            self.assertEqual(controller._retryable_failed_records(result), [record])

    def test_retry_build_error_message_uses_cumulative_display_counts(self):
        import tempfile

        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "bad.wav"
            source.write_bytes(b"still here")
            record = SimpleNamespace(source_path=source)
            controller = WorkflowController(None, mock.Mock(), mock.Mock(), None)
            controller.app = SimpleNamespace(model=SimpleNamespace(records=[record]))
            controller._last_build_options = {
                "move": True,
                "display_total": 1001,
                "display_committed_base": 1000,
            }
            result = {
                "total": 1,
                "copied": 0,
                "failed": 1,
                "move": True,
                "error": "1 file(s) failed during build.",
                "failed_records": [{"source_path": str(source), "target_path": "D:/Library/bad.wav"}],
            }

            workflow_build_completion.apply_retry_display_counts(result, controller._last_build_options)
            lines = build_result_compact_lines(result)
            message = controller._build_error_message(result, lines, str(result["error"]))

            self.assertIn("1000/1001 moved", message)
            self.assertNotIn("0/1 moved", message)

    def test_start_commit_releases_preview_player_before_build(self):
        engine = SimpleNamespace(
            target_dir=Path("D:/Target"),
            session_source_roots=[],
            _init_db_and_hashes=mock.Mock(),
        )
        worker_manager = mock.Mock()
        preview_player = mock.Mock()

        class _App(QObject):
            def __init__(self):
                super().__init__()
                self.settings = mock.Mock()
                self.audio_controller = SimpleNamespace(player=preview_player)

        app = _App()
        controller = WorkflowController(engine, worker_manager, mock.Mock(), app)

        controller.start_commit([mock.Mock()], "D:/Target", move=True)

        preview_player.release.assert_called_once()
        worker_manager.start_commit.assert_called_once()

    def test_start_commit_filters_duplicate_shadow_records_before_build(self):
        engine = SimpleNamespace(
            target_dir=Path("D:/Target"),
            session_source_roots=[],
            _init_db_and_hashes=mock.Mock(),
        )
        worker_manager = mock.Mock()

        class _App(QObject):
            def __init__(self):
                super().__init__()
                self.settings = mock.Mock()
                self.audio_controller = SimpleNamespace(player=None)

        app = _App()
        controller = WorkflowController(engine, worker_manager, mock.Mock(), app)
        normal = PlanRecord(Path("D:/Samples/original.wav"), "Pack", "Kicks", "Oneshots", "0.9")
        shadow = PlanRecord(
            Path("D:/Samples/dupe.wav"),
            "Pack",
            "Kicks",
            "Oneshots",
            "0.9",
            is_duplicate_shadow=True,
            duplicate_of_hash="hash-a",
            duplicate_of_path=normal.source_path,
        )

        controller.start_commit([normal, shadow], "D:/Target", move=True)

        worker_manager.start_commit.assert_called_once()
        records_arg = worker_manager.start_commit.call_args.args[0]
        self.assertEqual(records_arg, [normal])

    def test_start_commit_includes_duplicate_shadows_when_skipping_is_disabled(self):
        engine = SimpleNamespace(
            target_dir=Path("D:/Target"),
            session_source_roots=[],
            _init_db_and_hashes=mock.Mock(),
        )
        worker_manager = mock.Mock()

        class _App(QObject):
            def __init__(self):
                super().__init__()
                self.settings = mock.Mock()
                self.audio_controller = SimpleNamespace(player=None)

        app = _App()
        controller = WorkflowController(engine, worker_manager, mock.Mock(), app)
        normal = PlanRecord(Path("D:/Samples/original.wav"), "Pack", "Kicks", "Oneshots", "0.9")
        shadow = PlanRecord(
            Path("D:/Samples/dupe.wav"),
            "Pack",
            "Kicks",
            "Oneshots",
            "0.9",
            is_duplicate_shadow=True,
            duplicate_of_hash="hash-a",
            duplicate_of_path=normal.source_path,
        )

        controller.start_commit(
            [normal, shadow],
            "D:/Target",
            move=False,
            skip_confirmed_duplicates=False,
        )

        args = worker_manager.start_commit.call_args.args
        self.assertEqual(args[0], [normal, shadow])
        self.assertFalse(args[-1])


if __name__ == "__main__":
    unittest.main()
