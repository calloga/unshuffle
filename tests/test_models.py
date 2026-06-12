import unittest
from pathlib import Path
from typing import cast
from unshuffle.core import LibNode, NodeType, PlanRecord

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

if __name__ == "__main__":
    unittest.main()
