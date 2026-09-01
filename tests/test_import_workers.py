import csv
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from gui.core.import_workers import CsvImportWorker, PortableSessionImportWorker
from gui.core.data_manager import DataManager
from unshuffle.core import parse_tags, plan_records_from_staging_rows
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

    def test_preserves_preferred_library_roots_instead_of_collapsing_to_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            samples_root = root / "SAMPLES"
            drum_kits_root = root / "Drum Kits"
            sample_pack = samples_root / "Sample Pack"
            drum_pack = drum_kits_root / "Drum Pack"
            sample_pack.mkdir(parents=True)
            drum_pack.mkdir(parents=True)
            csv_path = root / "session.csv"
            csv_path.write_text(
                "source_directory,source_filename,pack,category\n"
                f"{sample_pack.as_posix()},lead.wav,Sample Pack,Melodics\n"
                f"{drum_pack.as_posix()},kick.wav,Drum Pack,Kicks\n",
                encoding="utf-8",
            )
            database = UnshuffleDB(root / "global.db")
            engine = SimpleNamespace(db=database, session_id="csv-session", close=mock.Mock())
            completed = []
            worker = CsvImportWorker(
                csv_path,
                root / "target",
                preferred_source_roots=[samples_root, drum_kits_root],
            )
            worker.completed.connect(completed.append)

            try:
                with mock.patch(
                    "gui.core.import_workers.create_workflow_bridge",
                    return_value=engine,
                ):
                    worker.run()

                self.assertEqual(
                    completed[0]["source_roots"],
                    [samples_root, drum_kits_root],
                )
                self.assertEqual(
                    database.get_session_sources("csv-session"),
                    [str(samples_root), str(drum_kits_root)],
                )
            finally:
                database.close()

    def test_fallback_keeps_distinct_csv_directories_not_their_common_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "SAMPLES" / "Pack A"
            second = root / "Drum Kits" / "Pack B"
            first.mkdir(parents=True)
            second.mkdir(parents=True)
            csv_path = root / "session.csv"
            csv_path.write_text(
                "source_directory,source_filename\n"
                f"{first.as_posix()},one.wav\n"
                f"{second.as_posix()},two.wav\n",
                encoding="utf-8",
            )
            database = UnshuffleDB(root / "global.db")
            engine = SimpleNamespace(db=database, session_id="csv-session", close=mock.Mock())
            completed = []
            worker = CsvImportWorker(csv_path, root / "target")
            worker.completed.connect(completed.append)

            try:
                with mock.patch(
                    "gui.core.import_workers.create_workflow_bridge",
                    return_value=engine,
                ):
                    worker.run()

                self.assertEqual(completed[0]["source_roots"], [first, second])
                self.assertNotIn(root, completed[0]["source_roots"])
            finally:
                database.close()

    def test_recovers_specific_roots_when_active_session_only_has_filesystem_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            samples_root = root / "SAMPLES"
            drum_kits_root = root / "Drum Kits"
            sample_pack = samples_root / "Sample Pack"
            drum_pack = drum_kits_root / "Drum Pack"
            sample_pack.mkdir(parents=True)
            drum_pack.mkdir(parents=True)
            csv_path = root / "session.csv"
            csv_path.write_text(
                "source_directory,source_filename,pack,category\n"
                f"{sample_pack.as_posix()},lead.wav,Sample Pack,Melodics\n"
                f"{drum_pack.as_posix()},kick.wav,Drum Pack,Kicks\n",
                encoding="utf-8",
            )
            database = UnshuffleDB(root / "global.db")
            engine = SimpleNamespace(db=database, session_id="csv-session", close=mock.Mock())
            completed = []
            worker = CsvImportWorker(
                csv_path,
                root / "target",
                # Path.anchor is the portable equivalent of the broken D:\ root.
                preferred_source_roots=[Path(root.anchor)],
            )
            worker.completed.connect(completed.append)

            try:
                with mock.patch(
                    "gui.core.import_workers.create_workflow_bridge",
                    return_value=engine,
                ):
                    worker.run()

                self.assertEqual(
                    completed[0]["source_roots"],
                    [samples_root, drum_kits_root],
                )
                self.assertNotIn(Path(root.anchor), completed[0]["source_roots"])
            finally:
                database.close()

    def test_matches_legacy_defaults_for_missing_and_empty_csv_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            csv_path = root / "session.csv"
            csv_path.write_text(
                "source_directory,source_filename,pack,category,subcategory,audio_type,tags\n"
                f"{root.as_posix()},blank.wav,,,,,\n",
                encoding="utf-8",
            )
            database = UnshuffleDB(root / "global.db")
            engine = SimpleNamespace(db=database, session_id="csv-session", close=mock.Mock())
            worker = CsvImportWorker(csv_path, root / "target")

            try:
                with mock.patch(
                    "gui.core.import_workers.create_workflow_bridge",
                    return_value=engine,
                ):
                    worker.run()

                row = database.get_staging_records("csv-session")[0]
                self.assertEqual(row["pack"], "")
                self.assertEqual(row["category"], "")
                self.assertEqual(row["subcategory"], "")
                self.assertEqual(row["audio_type"], "")
                self.assertEqual(row["pack_candidates"], "[]")
                self.assertEqual(row["evidence_json"], "{}")
                self.assertIsNone(row["analysis_tags_json"])
            finally:
                database.close()

    def test_database_backed_rows_match_legacy_import_record_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "SAMPLES" / "Pack"
            source.mkdir(parents=True)
            csv_path = root / "session.csv"
            csv_path.write_text(
                "source_directory,source_filename,pack,category,subcategory,audio_type,tags\n"
                f"{source.as_posix()},kick.wav,Pack,Kicks,Acoustic,Oneshots,punchy warm\n"
                f"{source.as_posix()},blank.wav,,,,,\n",
                encoding="utf-8",
            )
            legacy_records = DataManager().import_from_csv(csv_path)
            self.assertIsNotNone(legacy_records)
            assert legacy_records is not None

            database = UnshuffleDB(root / "global.db")
            engine = SimpleNamespace(db=database, session_id="csv-session", close=mock.Mock())
            worker = CsvImportWorker(csv_path, root / "target")

            try:
                with mock.patch(
                    "gui.core.import_workers.create_workflow_bridge",
                    return_value=engine,
                ):
                    worker.run()

                imported_records = plan_records_from_staging_rows(
                    database.get_staging_records("csv-session"),
                    parse_tags,
                )
                fields = (
                    "source_path",
                    "pack",
                    "category",
                    "subcategory",
                    "audio_type",
                    "confidence",
                    "evidence",
                    "is_preserved",
                    "preserved_root",
                    "is_manual",
                    "duration",
                    "pack_candidates",
                    "hash",
                    "fast_hash",
                    "tags",
                    "feature_vector",
                    "acoustic_vector",
                    "feature_space_version",
                    "feature_schema_json",
                    "analysis_status",
                    "analysis_tags_json",
                    "is_duplicate_shadow",
                    "duplicate_of_hash",
                    "duplicate_of_path",
                )
                self.assertEqual(len(imported_records), len(legacy_records))
                for legacy, imported in zip(legacy_records, imported_records):
                    self.assertEqual(
                        {field: getattr(imported, field) for field in fields},
                        {field: getattr(legacy, field) for field in fields},
                    )
            finally:
                database.close()

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
    def test_same_database_import_keeps_source_rows_intact(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_root = root / "source"
            source_root.mkdir()
            sample = source_root / "kick.wav"
            sample.write_bytes(b"sample")
            database_path = root / "portable.db"
            source_db = UnshuffleDB(database_path)
            destination_handle = UnshuffleDB(database_path)
            session_id = "same-database-session"
            source_db.register_session(session_id, source_root, root / "target", "pending")
            source_db.set_session_sources(session_id, [source_root])
            source_db.set_session_metadata(session_id, "saved_filters", '[{"name":"Kicks"}]')
            source_db.add_staging_records_bulk(
                session_id,
                [
                    (
                        1,
                        str(sample),
                        sample.name,
                        "Pack",
                        "Kicks",
                        "",
                        "Oneshots",
                        "[]",
                        "1.0",
                        0.1,
                        "full-hash",
                        "fast-hash",
                        "[]",
                        "{}",
                        None,
                        None,
                        None,
                        "ok",
                        "[]",
                        None,
                        False,
                    )
                ],
            )
            session = source_db.get_session(session_id)
            self.assertIsNotNone(session)
            assert session is not None
            completed = []
            errors = []
            worker = PortableSessionImportWorker(
                database_path,
                destination_handle,
                session,
                {str(source_root): source_root},
                [source_root],
                root / "target",
            )
            worker.completed.connect(completed.append)
            worker.error.connect(errors.append)

            try:
                worker.run()

                self.assertEqual(errors, [])
                self.assertEqual(completed[0]["record_count"], 1)
                self.assertEqual(completed[0]["saved_filters_json"], '[{"name":"Kicks"}]')
                rows = source_db.get_staging_records(session_id)
                self.assertEqual(len(rows), 1)
                self.assertEqual(Path(rows[0]["source_path"]), sample)
            finally:
                source_db.close()
                destination_handle.close()

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
            local_db.set_session_metadata(session_id, "saved_filters", '[{"name":"Kicks","query":"cat:Kicks"}]')
            local_db.set_session_metadata(session_id, "portable_session_manifest", '{"format_version":2}')
            local_db.add_staging_records_bulk(
                session_id,
                [
                    (1, str(available), available.name, "Pack", "Kicks", "Acoustic", "Oneshots", "punchy warm", "0.75", 0.1, "full-hash", "fast-hash", '[["Pack", 1.0]]', '{"source":"csv"}', None, "space-v1", '["duration"]', "ok", '["cached"]', str(source_root), True),
                    (2, str(missing), missing.name, "Pack", "Kicks", "", "Oneshots", "[]", "1.0", 0.1, "", None, "[]", "{}", None, None, None, None, "[]", None, False),
                ],
            )
            session = local_db.get_session(session_id)
            self.assertIsNotNone(session)
            assert session is not None
            completed = []
            errors = []
            progress = []
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
            worker.progress.connect(progress.append)

            try:
                worker.run()

                self.assertEqual(errors, [])
                self.assertEqual(completed[0]["record_count"], 1)
                self.assertEqual(completed[0]["skipped_count"], 1)
                self.assertEqual(
                    completed[0]["saved_filters_json"],
                    '[{"name":"Kicks","query":"cat:Kicks"}]',
                )
                self.assertEqual(
                    completed[0]["portable_manifest_json"],
                    '{"format_version":2}',
                )
                rows = global_db.get_staging_records(session_id)
                self.assertEqual([Path(row["source_path"]) for row in rows], [available])
                imported = rows[0]
                source_row = local_db.get_staging_records(session_id)[0]
                preserved_fields = (
                    "sample_name",
                    "pack",
                    "category",
                    "subcategory",
                    "audio_type",
                    "tags",
                    "confidence",
                    "duration",
                    "hash",
                    "fast_hash",
                    "pack_candidates",
                    "evidence_json",
                    "feature_vector",
                    "feature_space_version",
                    "feature_schema_json",
                    "analysis_status",
                    "analysis_tags_json",
                    "preserved_root",
                    "is_preserved",
                )
                self.assertEqual(
                    {field: imported[field] for field in preserved_fields},
                    {field: source_row[field] for field in preserved_fields},
                )
                initial_phases = {
                    payload["phase"]
                    for payload in progress
                    if payload.get("current") == 0
                }
                self.assertIn("Copying Session Records", initial_phases)
                self.assertIn("Restoring Audio Cache", initial_phases)
                self.assertIn("Restoring Review Decisions", initial_phases)
                self.assertTrue(any(phase.startswith("Restoring Session Metadata") for phase in initial_phases))
            finally:
                local_db.close()
                global_db.close()


if __name__ == "__main__":
    unittest.main()
