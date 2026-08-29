import csv
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from gui.core.import_workers import CsvImportWorker, PortableSessionImportWorker
from unshuffle.persistence import UnshuffleDB


class CsvImportWorkerTests(unittest.TestCase):
    def test_streams_csv_into_new_database_backed_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "samples" / "pack"
            source.mkdir(parents=True)
            csv_path = root / "session.csv"
            with csv_path.open("w", encoding="utf-8", newline="") as file_handle:
                writer = csv.DictWriter(
                    file_handle,
                    fieldnames=[
                        "source_directory",
                        "source_filename",
                        "pack",
                        "category",
                        "subcategory",
                        "audio_type",
                        "tags",
                    ],
                )
                writer.writeheader()
                writer.writerow({
                    "source_directory": str(source),
                    "source_filename": "kick.wav",
                    "pack": "Drums",
                    "category": "Kicks",
                    "subcategory": "Acoustic",
                    "audio_type": "Oneshots",
                    "tags": "punchy warm",
                })
                writer.writerow({
                    "source_directory": str(source),
                    "source_filename": "snare.wav",
                    "pack": "Drums",
                    "category": "Snares",
                    "subcategory": "Acoustic",
                    "audio_type": "Oneshots",
                    "tags": "bright",
                })

            database = UnshuffleDB(root / "global.db")
            engine = SimpleNamespace(
                db=database,
                session_id="csv-session",
                session_source_roots=[],
                session_source_root=None,
                close=mock.Mock(),
            )
            completed = []
            errors = []
            worker = CsvImportWorker(csv_path, root / "target")
            worker.completed.connect(completed.append)
            worker.error.connect(errors.append)

            try:
                with mock.patch(
                    "gui.core.import_workers.create_workflow_bridge",
                    return_value=engine,
                ):
                    worker.run()

                self.assertEqual(errors, [])
                self.assertEqual(completed[0]["record_count"], 2)
                rows = database.get_staging_records("csv-session")
                self.assertEqual([row["sample_name"] for row in rows], ["kick.wav", "snare.wav"])
                self.assertEqual([row["category"] for row in rows], ["Kicks", "Snares"])
                self.assertEqual(completed[0]["source_roots"], [source])
                engine.close.assert_not_called()
            finally:
                database.close()

    def test_rejects_csv_without_source_columns_before_creating_engine(self):
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "invalid.csv"
            csv_path.write_text("pack,category\nDrums,Kicks\n", encoding="utf-8")
            errors = []
            worker = CsvImportWorker(csv_path, Path(tmp))
            worker.error.connect(errors.append)

            with mock.patch("gui.core.import_workers.create_workflow_bridge") as create_engine:
                worker.run()

            create_engine.assert_not_called()
            self.assertIn("source_directory", errors[0])

    def test_reuses_same_target_engine_without_mutating_active_session_during_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            csv_path = root / "session.csv"
            csv_path.write_text(
                "source_directory,source_filename,pack,category\n"
                f"{root.as_posix()},kick.wav,Drums,Kicks\n",
                encoding="utf-8",
            )
            database = UnshuffleDB(root / "global.db")
            engine = SimpleNamespace(
                db=database,
                session_id="active-session",
                session_source_roots=[root / "old-source"],
                session_source_root=root / "old-source",
                close=mock.Mock(),
            )
            completed = []
            errors = []
            worker = CsvImportWorker(csv_path, root, existing_engine=engine)
            worker.completed.connect(completed.append)
            worker.error.connect(errors.append)

            try:
                with mock.patch("gui.core.import_workers.create_workflow_bridge") as create_engine:
                    worker.run()

                create_engine.assert_not_called()
                self.assertEqual(errors, [])
                self.assertEqual(engine.session_id, "active-session")
                imported_session_id = completed[0]["session_id"]
                self.assertNotEqual(imported_session_id, engine.session_id)
                self.assertEqual(len(database.get_staging_records(imported_session_id)), 1)
                engine.close.assert_not_called()
            finally:
                database.close()

    def test_cancellation_rolls_back_partial_same_target_import(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            csv_path = root / "large.csv"
            with csv_path.open("w", encoding="utf-8", newline="") as file_handle:
                writer = csv.writer(file_handle)
                writer.writerow(["source_directory", "source_filename", "pack", "category"])
                for index in range(300):
                    writer.writerow([str(root), f"sample-{index}.wav", "Pack", "Kicks"])
            database = UnshuffleDB(root / "global.db")
            engine = SimpleNamespace(db=database, session_id="active-session")
            completed = []
            errors = []
            worker = CsvImportWorker(csv_path, root, existing_engine=engine)
            worker.completed.connect(completed.append)
            worker.error.connect(errors.append)
            worker.progress.connect(
                lambda payload: worker.request_cancel()
                if payload.get("phase") == "Importing CSV"
                else None
            )

            try:
                worker.run()

                self.assertEqual(errors, [])
                self.assertTrue(completed[0]["cancelled"])
                sessions = database.get_recent_sessions(100)
                self.assertEqual([row["session_id"] for row in sessions], [])
            finally:
                database.close()


class PortableSessionImportWorkerTests(unittest.TestCase):
    def test_copies_available_rows_and_reports_missing_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_root = root / "source"
            source_root.mkdir()
            available = source_root / "kick.wav"
            available.write_bytes(b"sample")
            missing = source_root / "missing.wav"
            local_db = UnshuffleDB(root / "portable.db")
            global_db = UnshuffleDB(root / "global.db")
            session_id = "portable-session"
            local_db.register_session(session_id, source_root, root / "target", "pending")
            local_db.set_session_sources(session_id, [source_root])
            local_db.add_staging_records_bulk(
                session_id,
                [
                    (1, str(available), available.name, "Pack", "Kicks", "", "Oneshots", "[]", "1.0", 0.1, "", None, "[]", "{}", None, None, None, None, "[]", None, False),
                    (2, str(missing), missing.name, "Pack", "Kicks", "", "Oneshots", "[]", "1.0", 0.1, "", None, "[]", "{}", None, None, None, None, "[]", None, False),
                ],
            )
            session = local_db.get_session(session_id)
            self.assertIsNotNone(session)
            assert session is not None
            completed = []
            errors = []
            worker = PortableSessionImportWorker(
                local_db.db_path,
                global_db,
                session,
                {},
                [source_root],
                root / "target",
            )
            worker.completed.connect(completed.append)
            worker.error.connect(errors.append)

            try:
                worker.run()

                self.assertEqual(errors, [])
                self.assertEqual(completed[0]["record_count"], 1)
                self.assertEqual(completed[0]["skipped_count"], 1)
                rows = global_db.get_staging_records(session_id)
                self.assertEqual([Path(row["source_path"]) for row in rows], [available])
            finally:
                local_db.close()
                global_db.close()


if __name__ == "__main__":
    unittest.main()
