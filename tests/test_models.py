import unittest
import importlib.util
import json
from pathlib import Path
from typing import cast
from unshuffle.core import LibNode, NodeType, PlanRecord, parse_tags, plan_record_from_staging_row


def _load_build_staging_rows():
    state_path = Path(__file__).parents[1] / "gui" / "utils" / "state.py"
    spec = importlib.util.spec_from_file_location("gui_utils_state_for_test", state_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load gui.utils.state")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.build_staging_rows

class TestModels(unittest.TestCase):
    def test_libnode_defaults(self):
        node = LibNode(path=Path("/test"), name="test", node_type=NodeType.ROOT)
        self.assertEqual(node.name, "test")
        self.assertEqual(node.children, [])
        self.assertEqual(node.pack_candidate_weight, 0.0)
        self.assertFalse(node.is_preserved)

    def test_libnode_nesting(self):
        root = LibNode(Path("/"), "root", NodeType.ROOT)
        child = LibNode(Path("/child"), "child", NodeType.FILE, parent=root)
        root.children.append(child)
        
        self.assertEqual(root.children[0], child)
        self.assertEqual(child.parent, root)

    def test_planrecord_coercion(self):
        """
        Verify that PlanRecord.__post_init__ correctly coerces inputs to strings.
        This is a regression test for a bug where tuples were leaking into metadata.
        """
        # Confidence as a float, category as something else
        rec = PlanRecord(
            source_path=Path("file.wav"),
            pack=cast(str, 123), # Int instead of str
            category="Drums",
            audio_type="Oneshot",
            confidence=cast(str, 0.95), # Float instead of str
            tags=cast(list[str], ["tag1", 456]) # List with mixed types
        )
        
        self.assertIsInstance(rec.pack, str)
        self.assertEqual(rec.pack, "123")
        
        self.assertIsInstance(rec.confidence, str)
        self.assertEqual(rec.confidence, "0.95")
        
        self.assertIsInstance(rec.tags[1], str)
        self.assertEqual(rec.tags[1], "456")

    def test_planrecord_defaults(self):
        rec = PlanRecord(
            source_path=Path("file.wav"),
            pack="Pack",
            category="Cat",
            audio_type="Type",
            confidence="0.5"
        )
        self.assertEqual(rec.duration, 0.0)
        self.assertFalse(rec.is_manual)
        self.assertEqual(rec.evidence, {})

    def test_build_staging_rows_includes_fast_hash_after_hash(self):
        build_staging_rows = _load_build_staging_rows()
        rec = PlanRecord(
            source_path=Path("Source/kick.wav"),
            pack="Pack",
            category="Kicks",
            audio_type="Oneshots",
            confidence="0.9",
            hash="fast-a",
            fast_hash="fast-a",
            pack_candidates=[("Pack", 1.0)],
            evidence={"source": "test"},
        )

        row = build_staging_rows([rec])[0]

        self.assertEqual(row[10], "fast-a")
        self.assertEqual(row[11], "fast-a")
        self.assertEqual(row[12], '[["Pack", 1.0]]')
        self.assertEqual(row[13], '{"source": "test"}')
        self.assertEqual(len(row), 21)

    def test_duplicate_shadow_metadata_round_trips_through_staging_evidence(self):
        build_staging_rows = _load_build_staging_rows()
        rec = PlanRecord(
            source_path=Path("Source/dupe.wav"),
            pack="Pack",
            category="Kicks",
            audio_type="Oneshots",
            confidence="0.9",
            hash="hash-a",
            fast_hash="fast-a",
            tags=["duplicate"],
            is_duplicate_shadow=True,
            duplicate_of_hash="hash-canonical",
            duplicate_of_path=Path("Source/canonical.wav"),
        )

        row = build_staging_rows([rec])[0]
        evidence = json.loads(row[13])
        shadow = evidence["duplicate_shadow"]

        self.assertTrue(shadow["is_shadow"])
        self.assertEqual(shadow["duplicate_of_hash"], "hash-canonical")
        self.assertEqual(shadow["duplicate_of_path"], "Source\\canonical.wav")

        loaded = plan_record_from_staging_row(
            {
                "row_id": 0,
                "source_path": row[1],
                "pack": row[3],
                "category": row[4],
                "subcategory": row[5],
                "audio_type": row[6],
                "tags": '["duplicate"]',
                "confidence": row[8],
                "duration": row[9],
                "hash": row[10],
                "fast_hash": row[11],
                "pack_candidates": row[12],
                "evidence_json": row[13],
            },
            parse_tags,
        )

        self.assertTrue(loaded.is_duplicate_shadow)
        self.assertEqual(loaded.duplicate_of_hash, "hash-canonical")
        self.assertEqual(loaded.duplicate_of_path, Path("Source/canonical.wav"))

if __name__ == "__main__":
    unittest.main()
