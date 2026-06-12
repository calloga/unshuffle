import os
import struct
import unittest
from pathlib import Path
from unittest import mock

from unshuffle.audio import SimilarityEngine
from unshuffle.core.features import FEATURE_VECTOR_SIZE, calculate_similarity_distance, normalize_distance_vector
from unshuffle.core.vector_math import cosine_distance
from unshuffle.core import LibNode, NodeType
from unshuffle.logic.classification import classify_node, detect_audio_type, reset_scoring_engine
from gui.widgets.coherence_analyzer import _distance_between_payloads, _distance_payload_for_vector


class AudioTypeTests(unittest.TestCase):
    def test_camel_case_loop_detection_still_works(self):
        node = LibNode(
            path=Path(r"C:\audio\KickLoop01.wav"),
            name="KickLoop01.wav",
            node_type=NodeType.FILE,
            extension=".wav",
        )
        self.assertEqual(detect_audio_type(node), "Loops")

    def test_substring_hit_no_longer_overrides_bpm_loop(self):
        node = LibNode(
            path=Path(r"C:\audio\white_noise_140bpm.wav"),
            name="white_noise_140bpm.wav",
            node_type=NodeType.FILE,
            extension=".wav",
        )
        self.assertEqual(detect_audio_type(node), "Loops")

    def test_loopmasters_parent_no_longer_forces_loop(self):
        node = LibNode(
            path=Path(r"C:\audio\Loopmasters\mystery.wav"),
            name="mystery.wav",
            node_type=NodeType.FILE,
            extension=".wav",
        )
        self.assertEqual(detect_audio_type(node), "Oneshots")

    def test_parent_loop_folder_without_oneshot_hint_classifies_as_loop(self):
        node = LibNode(
            path=Path(r"C:\audio\Loops\mystery.wav"),
            name="mystery.wav",
            node_type=NodeType.FILE,
            extension=".wav",
        )
        self.assertEqual(detect_audio_type(node), "Loops")

    def test_parent_loop_folder_with_token_oneshot_hint_stays_loop_after_soft_malus(self):
        node = LibNode(
            path=Path(r"C:\audio\Loops\kick.wav"),
            name="kick.wav",
            node_type=NodeType.FILE,
            extension=".wav",
        )
        self.assertEqual(detect_audio_type(node), "Loops")

    def test_parent_loop_folder_with_substring_oneshot_hint_is_not_malus_candidate(self):
        node = LibNode(
            path=Path(r"C:\audio\Loops\sidekick.wav"),
            name="sidekick.wav",
            node_type=NodeType.FILE,
            extension=".wav",
        )
        self.assertEqual(detect_audio_type(node), "Loops")

    def test_primary_classification_applies_suppression_rules_after_adjustments(self):
        runtime = {
            "alias_table": {"kick": "Kicks", "bass": "Bass"},
            "noise_words": set(),
            "loop_indicators": [],
            "weak_loop_indicators": [],
            "oneshot_indicators": [],
            "oneshot_hint_tokens": [],
            "category_suppression_rules": {"Kicks": ["Bass"]},
            "percussive_categories": [],
            "sub_taxonomy_map": {},
            "default_sub_map": {},
            "model_numbers": set(),
        }
        node = LibNode(
            path=Path(r"C:\audio\kick_bass.wav"),
            name="kick_bass.wav",
            node_type=NodeType.FILE,
            extension=".wav",
        )

        reset_scoring_engine()
        try:
            category, confidence, evidence = classify_node(
                node,
                token_adjustments={"bass": {"Bass": 0.4}},
                runtime=runtime,
            )
        finally:
            reset_scoring_engine()

        self.assertEqual(category, "Kicks")
        self.assertGreater(confidence, 0.0)
        self.assertEqual(evidence["trace"]["components"]["filename"]["suppressed_scores"]["Bass"], 0.0)


class SimilarityVectorTests(unittest.TestCase):
    def test_pre_v1_17_float_vectors_are_rejected(self):
        blob = struct.pack("f" * 17, *([0.25] * 17))
        vec = SimilarityEngine.vector_from_blob(blob)

        self.assertIsNone(vec)
        self.assertEqual(calculate_similarity_distance([0.0] * 17, [0.0] * FEATURE_VECTOR_SIZE), float("inf"))

    def test_active_duration_from_vector_overrides_raw_duration_penalty(self):
        engine = SimilarityEngine()
        base = [0.5, 0.2, 0.5, 0.1, 0.8] + [1.0] * 12
        same_active = _v(base + [0.5])
        long_padding = _v(base + [0.5])
        longer_active = _v(base + [2.0])

        self.assertEqual(
            engine.calculate_distance(same_active, long_padding, d1=0.5, d2=5.0),
            0.0,
        )
        self.assertGreater(
            engine.calculate_distance(same_active, longer_active),
            0.0,
        )

    def test_distance_normalizes_raw_feature_scales(self):
        normalized = _v([0.5, 0.25, 0.5, 0.1, 0.5] + [1.0] * 12 + [0.7])
        raw_equivalent = _v([5000.0, 0.25, 8.0, 0.1, -5.0] + [10.0] * 12 + [0.7])

        self.assertEqual(normalize_distance_vector(raw_equivalent), normalized)
        self.assertAlmostEqual(calculate_similarity_distance(normalized, raw_equivalent), 0.0, places=6)

    def test_invalid_vector_blob_is_rejected(self):
        self.assertIsNone(SimilarityEngine.vector_from_blob(b"bad-shape"))

    def test_cosine_distance_rejects_mismatched_vector_lengths(self):
        self.assertEqual(cosine_distance([1.0, 0.0], [1.0]), float("inf"))

    def test_analyzer_cached_distance_matches_similarity_distance(self):
        examples = [
            (
                _v([0.5, 0.2, 0.5, 0.1, 0.8] + [1.0] * 12 + [0.5]),
                _v([0.8, 0.6, 0.4, 0.2, 0.3] + [0.2, 0.9, 0.1, 0.4, 0.0, 0.3, 0.7, 0.8, 0.2, 0.1, 0.6, 0.5] + [2.0]),
            ),
            (
                [0.0] * FEATURE_VECTOR_SIZE,
                _v([0.5, 0.2, 0.5, 0.1, 0.8] + [1.0] * 12 + [0.5]),
            ),
            (
                _v([5000.0, 0.25, 8.0, 0.1, -5.0] + [10.0] * 12 + [0.7]),
                _v([0.5, 0.25, 0.5, 0.1, 0.5] + [1.0] * 12 + [0.7]),
            ),
        ]

        for left, right in examples:
            with self.subTest(left=left[:5], right=right[:5]):
                self.assertAlmostEqual(
                    _distance_between_payloads(_distance_payload_for_vector(left), _distance_payload_for_vector(right)),
                    calculate_similarity_distance(left, right),
                    places=9,
                )

    def test_default_extractor_candidates_include_platform_names(self):
        root = Path("C:/repo") if os.name == "nt" else Path("/repo")
        candidates = SimilarityEngine.default_extractor_candidates(root)

        self.assertTrue(candidates)
        if os.name == "nt":
            self.assertTrue(any(str(path).endswith("unshuffle_extractor.exe") for path in candidates))
        else:
            self.assertTrue(any(str(path).endswith("unshuffle_extractor") for path in candidates))

    def test_default_extractor_candidates_cover_bundled_platform_dirs(self):
        root = Path("/repo")

        with mock.patch("unshuffle.audio.acoustic.os.name", "nt"), \
             mock.patch("unshuffle.audio.acoustic.sys.platform", "win32"):
            windows_candidates = [str(path).replace("\\", "/") for path in SimilarityEngine.default_extractor_candidates(root)]

        with mock.patch("unshuffle.audio.acoustic.os.name", "posix"), \
             mock.patch("unshuffle.audio.acoustic.sys.platform", "darwin"):
            mac_candidates = [str(path).replace("\\", "/") for path in SimilarityEngine.default_extractor_candidates(root)]

        with mock.patch("unshuffle.audio.acoustic.os.name", "posix"), \
             mock.patch("unshuffle.audio.acoustic.sys.platform", "linux"):
            linux_candidates = [str(path).replace("\\", "/") for path in SimilarityEngine.default_extractor_candidates(root)]

        self.assertIn("/repo/bin/windows/unshuffle_extractor.exe", windows_candidates)
        self.assertIn("/repo/bin/macos/unshuffle_extractor", mac_candidates)
        self.assertIn("/repo/bin/linux/unshuffle_extractor", linux_candidates)

    def test_default_extractor_search_candidates_include_installed_asset_root(self):
        repo_root = Path("C:/repo") if os.name == "nt" else Path("/repo")
        installed_root = Path("C:/Python/share/unshuffle") if os.name == "nt" else Path("/opt/share/unshuffle")

        with mock.patch("unshuffle.audio.acoustic.asset_roots", return_value=[repo_root, installed_root]):
            candidates = [
                str(path).replace("\\", "/")
                for path in SimilarityEngine.default_extractor_search_candidates()
            ]

        expected_name = "unshuffle_extractor.exe" if os.name == "nt" else "unshuffle_extractor"
        installed_root_text = str(installed_root).replace("\\", "/")
        self.assertIn(
            f"{installed_root_text}/bin/{SimilarityEngine.platform_bundle_dir_name()}/{expected_name}",
            candidates,
        )

    def test_extractor_path_env_override_is_honored(self):
        with mock.patch.dict(os.environ, {SimilarityEngine.EXTRACTOR_PATH_ENV: "/custom/unshuffle_extractor"}):
            engine = SimilarityEngine()

        self.assertEqual(engine.extractor_path, "/custom/unshuffle_extractor")


def _v(values):
    return list(values) + [0.0] * (FEATURE_VECTOR_SIZE - len(values))
