import json
import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from collections import OrderedDict
from pathlib import Path
from typing import Dict, List, Optional

from ..core.features import (
    CURRENT_EXTRACTOR_VERSION,
    CURRENT_FEATURE_SCHEMA,
    CURRENT_FEATURE_SPACE_VERSION,
    CURRENT_FEATURE_VECTOR_SIZE,
    DEFAULT_DISTANCE_WEIGHTS,
    FEATURE_VECTOR_SIZE,
    IDX_ACTIVE_DURATION,
    IDX_BRIGHTNESS,
    IDX_CHROMA_START,
    IDX_DECAY,
    IDX_FFT_REGISTER,
    IDX_PERCUSSIVITY,
    IDX_ZCR,
    calculate_similarity_distance,
    feature_blob_from_vector,
    sanitize_vector,
    vector_from_feature_values,
    vector_from_blob,
)
from ..core.assets import asset_roots
from ..core.constants import AUDIO_EXTS
from ..core.vector_math import calculate_tonalness


@dataclass(frozen=True)
class FeaturePayload:
    vector: List[float]
    feature_space_version: str = CURRENT_FEATURE_SPACE_VERSION
    extractor_version: str = ""
    feature_schema: tuple[str, ...] = CURRENT_FEATURE_SCHEMA
    analysis_status: str = "ok"

    @property
    def vector_schema(self) -> tuple[str, ...]:
        return self.feature_schema

    @property
    def duration(self) -> float:
        return self.vector[IDX_ACTIVE_DURATION] if len(self.vector) > IDX_ACTIVE_DURATION else 0.0

    @property
    def feature_vector_blob(self) -> bytes | None:
        return feature_blob_from_vector(self.vector)


class SimilarityEngine:
    """
    Bridges Python classification with a C++ feature extractor.
    Calculates weighted distance between feature vectors for perceptual similarity.
    """

    IDX_BRIGHTNESS = IDX_BRIGHTNESS
    IDX_PERCUSSIVITY = IDX_PERCUSSIVITY
    IDX_FFT_REGISTER = IDX_FFT_REGISTER
    IDX_ZCR = IDX_ZCR
    IDX_DECAY = IDX_DECAY
    IDX_CHROMA_START = IDX_CHROMA_START
    IDX_ACTIVE_DURATION = 17

    SILENCE_THRESHOLD = 0.001
    PERCUSSIVE_TONAL_SPLIT = 0.4

    DEFAULT_WEIGHTS = DEFAULT_DISTANCE_WEIGHTS.copy()
    FEATURE_VECTOR_SIZE = FEATURE_VECTOR_SIZE
    EXTRACT_TIMEOUT_SECONDS = 15
    EXTRACTOR_PATH_ENV = "UNSHUFFLE_EXTRACTOR_PATH"
    SUPPORTED_EXTS = AUDIO_EXTS - {".mid", ".midi", ".aas"}
    EXTRACTION_TAG_SILENT = "Silent"
    EXTRACTION_TAG_EMPTY = "Empty"
    EXTRACTION_TAG_CORRUPTED = "Corrupted"

    @staticmethod
    def platform_extractor_name() -> str:
        return "unshuffle_extractor.exe" if os.name == "nt" else "unshuffle_extractor"

    @staticmethod
    def platform_bundle_dir_name() -> str:
        if os.name == "nt":
            return "windows"
        if sys.platform == "darwin":
            return "macos"
        return "linux"

    @classmethod
    def default_extractor_candidates(cls, root: Path) -> List[Path]:
        name = cls.platform_extractor_name()
        platform_dir = cls.platform_bundle_dir_name()

        candidates = []
        candidates.extend(
            [
                root / "bin" / platform_dir / name,
                root / "bin" / name,
                root / name,
                root / "unshuffle_extractor" / "build" / platform_dir / name,
                root / "unshuffle_extractor" / "build" / "Release" / name,
                root / "unshuffle_extractor" / "build" / "Debug" / name,
                root / "unshuffle_extractor" / "build" / name,
                Path(name),
            ]
        )
        return candidates

    @classmethod
    def default_extractor_search_candidates(cls) -> List[Path]:
        candidates: List[Path] = []
        seen: set[str] = set()
        for root in asset_roots():
            for candidate in cls.default_extractor_candidates(root):
                key = str(candidate)
                if key not in seen:
                    seen.add(key)
                    candidates.append(candidate)
        return candidates

    def __init__(
        self,
        extractor_path: Optional[str] = None,
        weights: Optional[Dict[str, float]] = None,
        max_cache_entries: int = 1024,
    ):
        self.weights = weights or self.DEFAULT_WEIGHTS.copy()
        self.max_cache_entries = max(1, max_cache_entries)
        if extractor_path:
            self.extractor_path = extractor_path
        elif env_path := os.environ.get(self.EXTRACTOR_PATH_ENV):
            self.extractor_path = env_path
        else:
            options = self.default_extractor_search_candidates()
            self.extractor_path = options[-1].name
            for option in options:
                if option.exists():
                    self.extractor_path = str(option)
                    break

        self.feature_cache: "OrderedDict[str, List[float]]" = OrderedDict()
        self.negative_feature_cache: "OrderedDict[str, tuple[int, int, str, str, str]]" = OrderedDict()
        self.extraction_failure_tags: "OrderedDict[str, str]" = OrderedDict()

    def _cache_get(self, file_path: Path) -> Optional[List[float]]:
        key = str(file_path)
        cached = self.feature_cache.get(key)
        if cached is not None:
            self.feature_cache.move_to_end(key)
        return cached

    def _cache_set(self, file_path: Path, vector: List[float]) -> None:
        key = str(file_path)
        self.feature_cache[key] = vector
        self.feature_cache.move_to_end(key)
        while len(self.feature_cache) > self.max_cache_entries:
            self.feature_cache.popitem(last=False)

    def _negative_cache_signature(self, file_path: Path) -> tuple[int, int, str, str, str] | None:
        try:
            stat = file_path.stat()
        except OSError:
            return None
        return (
            stat.st_mtime_ns,
            stat.st_size,
            self.extractor_path,
            CURRENT_FEATURE_SPACE_VERSION,
            CURRENT_EXTRACTOR_VERSION,
        )

    def _negative_cache_get(self, file_path: Path) -> bool:
        key = str(file_path)
        signature = self._negative_cache_signature(file_path)
        if signature is None:
            return False
        cached = self.negative_feature_cache.get(key)
        if cached == signature:
            self.negative_feature_cache.move_to_end(key)
            return True
        if cached is not None:
            self.negative_feature_cache.pop(key, None)
        return False

    def _negative_cache_set(self, file_path: Path) -> None:
        signature = self._negative_cache_signature(file_path)
        if signature is None:
            return
        key = str(file_path)
        self.negative_feature_cache[key] = signature
        self.negative_feature_cache.move_to_end(key)
        while len(self.negative_feature_cache) > self.max_cache_entries:
            self.negative_feature_cache.popitem(last=False)

    @classmethod
    def extraction_failure_tag_for_message(cls, message: str) -> str | None:
        text = (message or "").strip().lower()
        if not text:
            return None
        if "silent" in text:
            return cls.EXTRACTION_TAG_SILENT
        if "empty" in text or "too short" in text:
            return cls.EXTRACTION_TAG_EMPTY
        if (
            "failed to open" in text
            or "couldn't open" in text
            or "could not open" in text
            or "invalid" in text
            or "exception" in text
        ):
            return cls.EXTRACTION_TAG_CORRUPTED
        return None

    def extraction_failure_tag(self, file_path: Path | str) -> str | None:
        return self.extraction_failure_tags.get(str(file_path))

    def _remember_extraction_failure_tag(self, file_path: Path, message: str) -> None:
        tag = self.extraction_failure_tag_for_message(message)
        if not tag:
            return
        key = str(file_path)
        self.extraction_failure_tags[key] = tag
        self.extraction_failure_tags.move_to_end(key)
        while len(self.extraction_failure_tags) > self.max_cache_entries:
            self.extraction_failure_tags.popitem(last=False)

    def _cache_negative_and_return_none(self, file_path: Path, message: str = "") -> None:
        self._remember_extraction_failure_tag(file_path, message)
        self._negative_cache_set(file_path)
        return None

    def extract_feature_payload(self, file_path: Path) -> Optional[FeaturePayload]:
        cached = self._cache_get(file_path)
        if cached is not None:
            return FeaturePayload(vector=cached)
        if self._negative_cache_get(file_path):
            return None

        if not Path(self.extractor_path).exists():
            logging.error("Similarity Engine: Extractor not found at %s", self.extractor_path)
            return None

        ext = file_path.suffix.lower()
        if ext not in self.SUPPORTED_EXTS:
            if ext == ".m4a":
                logging.info(
                    "Acoustic Indexing: Skipping .m4a (not supported by C++ engine) - %s",
                    file_path.name,
                )
            return self._cache_negative_and_return_none(file_path)

        try:
            creationflags = 0
            startupinfo = None
            if os.name == "nt":
                creationflags = subprocess.CREATE_NO_WINDOW | 0x00000040
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = 0
            result = subprocess.run(
                [self.extractor_path, "--file", str(file_path)],
                capture_output=True,
                text=True,
                creationflags=creationflags,
                startupinfo=startupinfo,
                timeout=self.EXTRACT_TIMEOUT_SECONDS,
            )
            if result.returncode != 0:
                error_text = result.stderr.strip()
                logging.error(
                    "C++ Extractor Error (%s) for %s: %s",
                    result.returncode,
                    file_path.name,
                    error_text,
                )
                return self._cache_negative_and_return_none(file_path, error_text)

            data = json.loads(result.stdout)
            vector = data.get("vector")
            if not vector and isinstance(data.get("features"), dict):
                vector = vector_from_feature_values(data["features"])
            if vector:
                sanitized = self._sanitize_vector(vector)
                if sanitized and len(sanitized) == CURRENT_FEATURE_VECTOR_SIZE:
                    feature_space_version = str(data.get("feature_space_version") or CURRENT_FEATURE_SPACE_VERSION)
                    feature_schema = tuple(data.get("feature_schema") or data.get("vector_schema") or CURRENT_FEATURE_SCHEMA)
                    if feature_space_version != CURRENT_FEATURE_SPACE_VERSION:
                        logging.error(
                            "C++ Extractor returned unsupported feature space %s for %s",
                            feature_space_version,
                            file_path.name,
                        )
                        return self._cache_negative_and_return_none(file_path)
                    if feature_schema != CURRENT_FEATURE_SCHEMA:
                        logging.error(
                            "C++ Extractor returned unsupported feature schema for %s",
                            file_path.name,
                        )
                        return self._cache_negative_and_return_none(file_path)
                    self._cache_set(file_path, sanitized)
                    return FeaturePayload(
                        vector=sanitized,
                        feature_space_version=feature_space_version,
                        extractor_version=str(data.get("extractor_version") or ""),
                        feature_schema=feature_schema,
                        analysis_status=str(data.get("analysis_status") or "ok"),
                    )
                message = "C++ Extractor returned an invalid vector"
                logging.error("%s for %s", message, file_path.name)
                return self._cache_negative_and_return_none(file_path, message)
        except subprocess.TimeoutExpired:
            message = (
                f"C++ Extractor timed out after {self.EXTRACT_TIMEOUT_SECONDS}s"
            )
            logging.error(
                "C++ Extractor timed out after %ss for %s",
                self.EXTRACT_TIMEOUT_SECONDS,
                file_path.name,
            )
            return self._cache_negative_and_return_none(file_path, message)
        except Exception as exc:
            message = f"C++ Bridge Exception: {exc}"
            logging.error("C++ Bridge Exception: %s", exc)
            return self._cache_negative_and_return_none(file_path, message)
        return self._cache_negative_and_return_none(file_path)

    def extract_features(self, file_path: Path) -> Optional[List[float]]:
        payload = self.extract_feature_payload(file_path)
        return payload.vector if payload else None

    @classmethod
    def vector_from_blob(cls, value) -> Optional[List[float]]:
        return vector_from_blob(value)

    def _sanitize_vector(self, vec: List[float]) -> Optional[List[float]]:
        return sanitize_vector(vec)

    def _calculate_tonalness(self, chroma: List[float]) -> float:
        return calculate_tonalness(chroma)

    def calculate_distance(self, v1: List[float], v2: List[float], d1: float = 0.0, d2: float = 0.0) -> float:
        return calculate_similarity_distance(v1, v2, weights=self.weights, d1=d1, d2=d2)

    def find_similar(self, target_record, candidates: List, limit=10):
        target_vec = self.extract_features(target_record.source_path)
        if not target_vec:
            return []

        results = []
        for record in candidates:
            if record == target_record:
                continue
            candidate_vec = self.extract_features(record.source_path)
            if candidate_vec:
                dist = self.calculate_distance(
                    target_vec,
                    candidate_vec,
                    d1=target_record.duration,
                    d2=record.duration,
                )
                results.append((record, dist))

        results.sort(key=lambda item: item[1])
        return results[:limit]
