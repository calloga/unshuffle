import struct
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from gui.core.search_controller import SearchController
from gui.core.search_engine import SearchEngine
from unshuffle.core.features import FEATURE_VECTOR_SIZE
from unshuffle.persistence import UnshuffleDB
from unshuffle.persistence.search import SearchExecutionError


class QueryContractTests(unittest.TestCase):
    def _seed_staging_fixture(self, db: UnshuffleDB, session_id: str):
        db.register_session(session_id, Path("Source"), Path("Target"), "copy")
        records = [
            (
                1,
                r"C:\LIB\Drums\Kicks\kick_loop.wav",
                "kick_loop.wav",
                "Drum Pack A",
                "Kicks",
                "",
                "Loops",
                "128bpm cmin",
                "0.95",
                1.2,
                None,
                "[]",
                struct.pack("f" * FEATURE_VECTOR_SIZE, *([0.0] * FEATURE_VECTOR_SIZE)),
                None,
                0,
            ),
            (
                2,
                r"C:\LIB\Drums\Snares\snare_hit.wav",
                "snare_hit.wav",
                "Drum Pack A",
                "Snares",
                "",
                "Oneshots",
                "oneshot",
                "0.90",
                0.2,
                None,
                "[]",
                struct.pack("f" * FEATURE_VECTOR_SIZE, *([0.2] * FEATURE_VECTOR_SIZE)),
                None,
                0,
            ),
            (
                3,
                r"C:\LIB\Synths\Aden\aden_pad_loop.wav",
                "aden_pad_loop.wav",
                "Aden Pack",
                "Melodics",
                "Pads",
                "Loops",
                "120bpm amin",
                "0.88",
                2.8,
                None,
                "[]",
                struct.pack("f" * FEATURE_VECTOR_SIZE, *([2.0] * FEATURE_VECTOR_SIZE)),
                None,
                0,
            ),
            (
                4,
                r"C:\LIB\Hats\hat_one.wav",
                "hat_one.wav",
                "Hats Vol 1",
                "Hats & Cymbals",
                "",
                "Oneshots",
                "oneshot",
                "0.87",
                0.12,
                None,
                "[]",
                None,
                None,
                0,
            ),
            (
                5,
                r"C:\LIB\Non-Audio Assets\Pack A\LICENSE.pdf",
                "LICENSE.pdf",
                "Pack A",
                "Non-Audio Assets",
                "",
                "Non-Audio Assets",
                "",
                "1.0",
                0.0,
                None,
                "[]",
                None,
                None,
                0,
            ),
        ]
        db.add_staging_records_bulk(session_id, records)

    def _search_engine(self, db: UnshuffleDB, session_id: str) -> SearchEngine:
        class _Engine:
            pass

        engine = _Engine()
        engine.db = db
        engine.session_id = session_id
        return SearchEngine(engine)

    def _assert_result_set(self, result, expected: set[int]):
        self.assertIsInstance(result, (set, list))
        self.assertEqual(set(result), expected)

    def test_parse_groups_and_precedence(self):
        groups = SearchEngine.parse_query_groups('cat:"Kicks", type:"Loops" OR pack:"Aden Pack"')
        self.assertEqual(groups, [['cat:"Kicks"', 'type:"Loops"'], ['pack:"Aden Pack"']])

    def test_canonicalize_prefix_aliases(self):
        self.assertEqual(SearchEngine.canonicalize_term('cat:"Kicks"'), 'category:"Kicks"')
        self.assertEqual(SearchEngine.canonicalize_term('packname:"Aden Pack"'), 'pack:"Aden Pack"')
        self.assertEqual(SearchEngine.canonicalize_term('root:"C:/LIB/Drums"'), 'source:"C:/LIB/Drums"')
        self.assertEqual(SearchEngine.canonicalize_term('type:"Utility"'), 'audio_type:"Non-Audio Assets"')
        self.assertEqual(SearchEngine.canonicalize_term('type:"non_audio_assets"'), 'audio_type:"Non-Audio Assets"')

    def test_db_and_search_engine_contract_matrix(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = UnshuffleDB(Path(tmp) / "query_contract.db")
            session_id = "q1"
            try:
                self._seed_staging_fixture(db, session_id)
                se = self._search_engine(db, session_id)

                ids = db.search_staging(session_id, 'category:"Kicks",audio_type:"Loops"')
                self.assertEqual(set(ids), {1})

                ids = se.execute_search('cat:"Kicks" OR cat:"Snares"')
                self._assert_result_set(ids, {1, 2})

                ids = se.execute_search('cat:"Kicks", type:"Loops" OR cat:"Snares"')
                self._assert_result_set(ids, {1, 2})

                ids = se.execute_search('pack:"Aden Pack"')
                self._assert_result_set(ids, {3})

                ids = se.execute_search('source:"C:/LIB/Drums"')
                self._assert_result_set(ids, {1, 2})

                ids = se.execute_search('type:"Utility"')
                self._assert_result_set(ids, {5})

                ids = se.execute_search('type:"Non-Audio Assets"')
                self._assert_result_set(ids, {5})

                ids = se.execute_search('similar:1, cat:"Kicks"')
                self.assertEqual(ids, [1])
            finally:
                db.close()

    def test_path_search_treats_percent_and_underscore_literally(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = UnshuffleDB(Path(tmp) / "query_literal_paths.db")
            session_id = "q_literal"
            try:
                db.register_session(session_id, Path("Source"), Path("Target"), "copy")
                records = [
                    (
                        1,
                        r"C:\LIB\Vendor_100%\Pack\kick.wav",
                        "kick.wav",
                        "Pack",
                        "Kicks",
                        "",
                        "Oneshots",
                        "",
                        "0.90",
                        0.2,
                        None,
                        "[]",
                        None,
                        None,
                        0,
                    ),
                    (
                        2,
                        r"C:\LIB\VendorA100X\Pack\snare.wav",
                        "snare.wav",
                        "Pack",
                        "Snares",
                        "",
                        "Oneshots",
                        "",
                        "0.90",
                        0.2,
                        None,
                        "[]",
                        None,
                        None,
                        0,
                    ),
                ]
                db.add_staging_records_bulk(session_id, records)

                ids = db.search_staging(session_id, r'source:"C:/LIB/Vendor_100%"')

                self.assertEqual(set(ids), {1})
            finally:
                db.close()

    def test_remove_staging_by_source_treats_percent_and_underscore_literally(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = UnshuffleDB(Path(tmp) / "remove_literal_source.db")
            session_id = "remove_literal"
            try:
                db.register_session(session_id, Path("Source"), Path("Target"), "copy")
                records = [
                    (
                        1,
                        r"C:\LIB\Vendor_100%\Pack\kick.wav",
                        "kick.wav",
                        "Pack",
                        "Kicks",
                        "",
                        "Oneshots",
                        "",
                        "0.90",
                        0.2,
                        None,
                        "[]",
                        None,
                        None,
                        0,
                    ),
                    (
                        2,
                        r"C:\LIB\VendorA100X\Pack\snare.wav",
                        "snare.wav",
                        "Pack",
                        "Snares",
                        "",
                        "Oneshots",
                        "",
                        "0.90",
                        0.2,
                        None,
                        "[]",
                        None,
                        None,
                        0,
                    ),
                ]
                db.add_staging_records_bulk(session_id, records)

                db.remove_staging_by_source(session_id, r"C:\LIB\Vendor_100%")

                rows = db.get_staging_records(session_id)
                self.assertEqual([row["row_id"] for row in rows], [2])
            finally:
                db.close()

    def test_filename_queries_with_dots_hyphens_and_numbers_do_not_break_fts(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = UnshuffleDB(Path(tmp) / "query_filename_fts.db")
            session_id = "q_filename"
            try:
                db.register_session(session_id, Path("Source"), Path("Target"), "copy")
                records = [
                    (
                        1,
                        r"C:\LIB\Breaks\15_DefocusedJungle.wav",
                        "15_DefocusedJungle.wav",
                        "Break Pack",
                        "Breaks",
                        "",
                        "Loops",
                        "",
                        "0.92",
                        1.0,
                        None,
                        "[]",
                        None,
                        None,
                        0,
                    ),
                    (
                        2,
                        r"C:\LIB\Drums\GDYN_Punching_Perc_RAW_BD - 6.wav",
                        "GDYN_Punching_Perc_RAW_BD - 6.wav",
                        "Drum Pack",
                        "Kicks",
                        "",
                        "Oneshots",
                        "",
                        "0.93",
                        0.5,
                        None,
                        "[]",
                        None,
                        None,
                        0,
                    ),
                ]
                db.add_staging_records_bulk(session_id, records)
                se = self._search_engine(db, session_id)

                self.assertEqual(
                    set(db.search_staging(session_id, 'sample_name:15_DefocusedJungle.wav')),
                    {1},
                )
                self._assert_result_set(se.execute_search('GDYN_Punching_Perc_RAW_BD - 6.wav'), {2})
            finally:
                db.close()

    def test_reserved_words_and_special_characters_do_not_break_fts_queries(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = UnshuffleDB(Path(tmp) / "query_reserved_terms.db")
            session_id = "q_reserved"
            try:
                db.register_session(session_id, Path("Source"), Path("Target"), "copy")
                records = [
                    (
                        1,
                        r"C:\LIB\FX\AND_gate.wav",
                        "AND_gate.wav",
                        "Logic Pack",
                        "FX",
                        "",
                        "Oneshots",
                        "gate",
                        "0.90",
                        0.4,
                        None,
                        "[]",
                        None,
                        None,
                        0,
                    ),
                    (
                        2,
                        r"C:\LIB\FX\NEAR-hit.wav",
                        "NEAR-hit.wav",
                        "Logic Pack",
                        "FX",
                        "",
                        "Oneshots",
                        "impact",
                        "0.89",
                        0.4,
                        None,
                        "[]",
                        None,
                        None,
                        0,
                    ),
                ]
                db.add_staging_records_bulk(session_id, records)
                se = self._search_engine(db, session_id)

                self._assert_result_set(se.execute_search("AND_gate.wav"), {1})
                self._assert_result_set(se.execute_search("NEAR-hit.wav"), {2})
                self.assertEqual(set(db.search_staging(session_id, 'sample_name:AND_gate.wav')), {1})
            finally:
                db.close()

    def test_search_db_failure_raises_instead_of_returning_empty_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = UnshuffleDB(Path(tmp) / "query_failure.db")
            session_id = "q_failure"
            try:
                self._seed_staging_fixture(db, session_id)
                db.conn.execute("DROP TABLE staging_fts")

                with self.assertRaises(SearchExecutionError):
                    db.search_staging(session_id, 'category:"Kicks"')
            finally:
                db.close()

    def test_search_engine_propagates_db_failures_to_worker_boundary(self):
        db = mock.Mock()
        db.search_staging.side_effect = RuntimeError("database unavailable")
        workflow = SimpleNamespace(db=db, session_id="session-1")

        with self.assertRaises(RuntimeError):
            SearchEngine.run_query(workflow, 'category:"Kicks"')

    def test_search_completion_schedules_tree_refresh_for_empty_results(self):
        app = SimpleNamespace(
            handle_search_results_applied=mock.Mock(),
            schedule_search_tree_refresh=mock.Mock(),
        )
        proxy = mock.Mock()
        proxy.rowCount.return_value = 0
        controller = SearchController(engine=mock.Mock(), model=mock.Mock(), proxy_model=proxy)
        controller.app = app

        controller.on_search_finished_logic({"query_text": ".midi", "matched_ids": []})

        app.handle_search_results_applied.assert_called_once()
        app.schedule_search_tree_refresh.assert_called_once_with(0)

    def test_staging_insert_auto_ids_and_invalid_vectors(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = UnshuffleDB(Path(tmp) / "staging_insert.db")
            session_id = "q2"
            try:
                db.register_session(session_id, Path("Source"), Path("Target"), "copy")
                records = [
                    (
                        10,
                        "Source/a.wav",
                        "a.wav",
                        "Pack A",
                        "Kicks",
                        "",
                        "Oneshots",
                        "",
                        "0.90",
                        0.2,
                        "hash-a",
                        "[]",
                        b"bad-shape",
                        None,
                        0,
                    ),
                    (
                        11,
                        "Source/b.wav",
                        "b.wav",
                        "Pack B",
                        "Snares",
                        "",
                        "Oneshots",
                        "",
                        "0.91",
                        0.3,
                        "hash-b",
                        "[]",
                        struct.pack("f" * FEATURE_VECTOR_SIZE, *([0.1] * FEATURE_VECTOR_SIZE)),
                        None,
                        0,
                    ),
                ]
                db.add_staging_records_bulk(session_id, records)
                rows = db.get_staging_records(session_id)
                self.assertEqual([row["row_id"] for row in rows], [10, 11])
                self.assertIsNone(rows[0]["feature_vector"])
                self.assertIsNotNone(rows[1]["feature_vector"])
                self.assertEqual(len(rows[1]["feature_vector"]), FEATURE_VECTOR_SIZE * 4)
            finally:
                db.close()

    def test_similar_query_prefilters_candidates_before_ranking(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = UnshuffleDB(Path(tmp) / "query_prefilter.db")
            session_id = "q_prefilter"
            try:
                self._seed_staging_fixture(db, session_id)
                captured: dict[str, object] = {}

                def fake_lookup(sid, target_id, limit=50, candidate_ids=None):
                    captured["session_id"] = sid
                    captured["target_id"] = target_id
                    captured["candidate_ids"] = set(candidate_ids) if candidate_ids is not None else None
                    return [1]

                with mock.patch.object(db, "search_similar_records", side_effect=fake_lookup):
                    ids = db.search_staging(session_id, 'similar:1, category:"Kicks", audio_type:"Loops"')

                self.assertEqual(ids, [1])
                self.assertEqual(captured["session_id"], session_id)
                self.assertEqual(captured["target_id"], 1)
                self.assertEqual(captured["candidate_ids"], {1})
            finally:
                db.close()

    def test_similar_query_without_other_filters_keeps_global_candidate_pool(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = UnshuffleDB(Path(tmp) / "query_no_prefilter.db")
            session_id = "q_no_prefilter"
            try:
                self._seed_staging_fixture(db, session_id)
                captured: dict[str, object] = {}

                def fake_lookup(sid, target_id, limit=50, candidate_ids=None):
                    captured["candidate_ids"] = candidate_ids
                    return [1, 2]

                with mock.patch.object(db, "search_similar_records", side_effect=fake_lookup):
                    ids = db.search_staging(session_id, "similar:1")

                self.assertEqual(ids, [1, 2])
                self.assertIsNone(captured["candidate_ids"])
            finally:
                db.close()
