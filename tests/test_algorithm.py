#!/usr/bin/env python3
import unittest
import tempfile
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, List, Dict, Tuple

from unshuffle.logic.planning import run_plan
from unshuffle.runtime.engine import RuntimeUnshuffler as Unshuffler

@dataclass
class MockFile:
    name: str

@dataclass
class MockDir:
    name: str
    files: List[MockFile] = field(default_factory=list)
    subdirs: List["MockDir"] = field(default_factory=list)

def build_real_tree(node, current_path: Path):
    dir_path = current_path / node.name
    dir_path.mkdir(parents=True, exist_ok=True)
    for f in node.files:
        p = dir_path / f.name
        p.touch()
    for d in node.subdirs:
        build_real_tree(d, dir_path)

@dataclass
class Expected:
    source_path: str
    pack:        str
    category:    str
    audio_type:  str
    subcategory: Optional[str] = None
    is_preserved: bool = False
    note:        str = ""

class AlgorithmTestBase(unittest.TestCase):
    def run_case(self, name: str, tree: MockDir, expected_list: List[Expected]):
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp_dir = Path(tmp_str)
            build_real_tree(tree, tmp_dir)
            source_root = tmp_dir / tree.name
            target_dir = tmp_dir / "OrganizedTarget"
            
            try:
                records = run_plan(source_root=source_root, target_dir=target_dir)
                rec_map = {r.source_path.relative_to(tmp_dir).as_posix(): r for r in records}
                print(f"DEBUG {name}: Keys in rec_map:", list(rec_map.keys()))

                for exp in expected_list:
                    with self.subTest(msg=f"{name} -> {exp.source_path}"):
                        rec = rec_map.get(exp.source_path)
                        self.assertIsNotNone(rec, f"Missing record for {exp.source_path}")
                        
                        self.assertEqual(rec.pack, exp.pack, f"Pack mismatch for {exp.source_path}")
                        self.assertEqual(rec.category, exp.category, f"Category mismatch for {exp.source_path}")
                        self.assertEqual(rec.audio_type, exp.audio_type, f"Audio type mismatch for {exp.source_path}")
                        self.assertEqual(rec.subcategory, exp.subcategory, f"Subcategory mismatch for {exp.source_path}")
                        self.assertEqual(rec.is_preserved, exp.is_preserved, f"Preserved mismatch for {exp.source_path}")
            finally:
                logging.shutdown()

class TestAlgorithmExhaustive(AlgorithmTestBase):
    def test_T01_flat_kit(self):
        tree = MockDir("Source", files=[MockFile("kick.wav"), MockFile("loop_120bpm.wav"), MockFile("sample_01.wav")])
        self.run_case("T01", tree, [
            Expected("Source/kick.wav", "Source", "Kicks", "Oneshots"),
            Expected("Source/loop_120bpm.wav", "Source", "Uncategorized", "Loops"),
            Expected("Source/sample_01.wav", "Source", "Uncategorized", "Oneshots"),
        ])

    def test_T02_classic_leaf(self):
        tree = MockDir("Source", subdirs=[
            MockDir("Kicks", files=[MockFile("kick1.wav")]),
            MockDir("Snares", files=[MockFile("snare1.wav")]),
            MockDir("hh", files=[MockFile("closed.wav")]),
        ])
        self.run_case("T02", tree, [
            Expected("Source/Kicks/kick1.wav", "Source", "Kicks", "Oneshots"),
            Expected("Source/Snares/snare1.wav", "Source", "Snares", "Oneshots"),
            Expected("Source/hh/closed.wav", "Source", "Hats & Cymbals", "Oneshots", "Hats"),
        ])

    def test_T03_named_packs(self):
        tree = MockDir("Source", subdirs=[
            MockDir("Kit A", subdirs=[MockDir("Kicks", files=[MockFile("kick.wav")])]),
            MockDir("Kit B", subdirs=[MockDir("808", files=[MockFile("bass.wav")])]),
        ])
        self.run_case("T03", tree, [
            Expected("Source/Kit A/Kicks/kick.wav", "Kit A", "Kicks", "Oneshots"),
            Expected("Source/Kit B/808/bass.wav", "Kit B", "Bass", "Oneshots"),
        ])

    def test_T04_multi_segment_pack(self):
        tree = MockDir("Source", subdirs=[MockDir("Percussion", subdirs=[MockDir("Organic", subdirs=[MockDir("Africa", files=[MockFile("HatLoop122.wav")])])])])
        self.run_case("T04", tree, [Expected("Source/Percussion/Organic/Africa/HatLoop122.wav", "Organic", "Hats & Cymbals", "Loops", "Hats")])

    def test_T05_strong_filename_overrides_leaf(self):
        tree = MockDir("Source", subdirs=[MockDir("Vocals", files=[MockFile("kick_vox.wav")])])
        self.run_case("T05", tree, [Expected("Source/Vocals/kick_vox.wav", "Source", "Kicks", "Oneshots")])

    def test_T09_tie_breaking(self):
        tree = MockDir("Source", files=[MockFile("808_snare.wav")])
        self.run_case("T09", tree, [Expected("Source/808_snare.wav", "Source", "Snares", "Oneshots")])

    def test_T10_depth_cap(self):
        tree = MockDir("Source", subdirs=[MockDir("V", subdirs=[MockDir("C", subdirs=[MockDir("S", subdirs=[MockDir("P", subdirs=[MockDir("Kicks", files=[MockFile("kick.wav")])])])])])])
        self.run_case("T10", tree, [Expected("Source/V/C/S/P/Kicks/kick.wav", "P", "Kicks", "Oneshots")])

    def test_T13_loop_detection(self):
        tree = MockDir("Source", subdirs=[MockDir("Loops", subdirs=[MockDir("Kicks", files=[MockFile("01.wav")])])])
        self.run_case("T13", tree, [Expected("Source/Loops/Kicks/01.wav", "Loops", "Kicks", "Oneshots")])

    def test_T17_camelcase(self):
        tree = MockDir("Source", files=[MockFile("KickDrum01.wav")])
        self.run_case("T17", tree, [Expected("Source/KickDrum01.wav", "Source", "Kicks", "Oneshots")])

    def test_T20_chord_vs_ch(self):
        tree = MockDir("Source", files=[MockFile("chord_loop.wav")])
        self.run_case("T20", tree, [Expected("Source/chord_loop.wav", "Source", "Melodics", "Loops")])

    def test_T28_suppression(self):
        tree = MockDir("Source", files=[MockFile("808_Conga_Loop.wav")])
        self.run_case("T28", tree, [Expected("Source/808_Conga_Loop.wav", "Source", "Percussion", "Loops", "Membranophones")])

    def test_T34_nightmare_pack(self):
        tree = MockDir("Source", subdirs=[
            MockDir("My_Samples", files=[MockFile(".unshuffle_preserved")], subdirs=[MockDir("Nested", files=[MockFile("dont_touch.wav")])]),
            MockDir("Kit_A", subdirs=[MockDir("Kicks", files=[MockFile("kick.wav")])]),
        ])
        self.run_case("T34", tree, [
            Expected("Source/My_Samples", "My_Samples", "Preserved", "Utility", subcategory="", is_preserved=True),
            Expected("Source/Kit_A/Kicks/kick.wav", "Kit_A", "Kicks", "Oneshots"),
        ])

    def test_T30_long_path_execution(self):
        long_name = "A" * 120
        tree = MockDir("Source", subdirs=[MockDir(long_name, files=[MockFile("kick.wav")])])
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp_dir = Path(tmp_str)
            target_root = tmp_dir / "OrganizedTarget"
            target_root.mkdir()
            build_real_tree(tree, tmp_dir)
            source_root = tmp_dir / tree.name
            from unshuffle.persistence import UnshuffleDB
            class DummyBootstrapper:
                def setup_logging_fn(self, *args, **kwargs):
                    pass
                def get_local_db_fn(self, root):
                    db_path = root / ".unshuffle" / "staging.db"
                    db_path.parent.mkdir(parents=True, exist_ok=True)
                    return UnshuffleDB(db_path)
                def run_plan_fn(self, *args, **kwargs):
                    from unshuffle.logic.planning.service import run_plan
                    return run_plan(*args, **kwargs)
            engine = Unshuffler(target_root, bootstrapper=DummyBootstrapper())
            engine.db = engine.local_db
            try:
                plan = engine.prepare_plan([source_root])
                result = engine.execute_plan(plan, flat=True)
                self.assertEqual(result.get("copied"), 1)
            finally:
                engine.close()
                logging.shutdown()

if __name__ == "__main__":
    unittest.main()
