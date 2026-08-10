import json
import logging
import os
import queue
import subprocess
import sys
import tempfile
import threading
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections.abc import Callable, Iterator
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
    FALLBACK_EXTRACTOR_WORKERS = 4
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
        self.extraction_failure_messages: "OrderedDict[str, str]" = OrderedDict()
        self._process_slots = threading.BoundedSemaphore(1)
        self._is_interrupted: Callable[[], bool] | None = None
        self._completion_callback: Callable[[Path], None] | None = None
        self._reported_completions: set[Path] = set()
        self._completion_lock = threading.Lock()

    def configure_extraction_runtime(
        self,
        *,
        max_processes: int,
        is_interrupted: Callable[[], bool] | None = None,
        completion_callback: Callable[[Path], None] | None = None,
    ) -> None:
        """Set one process budget shared by batch extraction and retries."""
        self._process_slots = threading.BoundedSemaphore(max(1, max_processes))
        self._is_interrupted = is_interrupted
        self._completion_callback = completion_callback
        self._reported_completions.clear()

    def _report_extraction_complete(self, file_path: Path) -> None:
        callback = self._completion_callback
        if callback is None:
            return
        with self._completion_lock:
            if file_path in self._reported_completions:
                return
            self._reported_completions.add(file_path)
        callback(file_path)

    @contextmanager
    def _process_slot(self) -> Iterator[None]:
        while not self._process_slots.acquire(timeout=0.1):
            if self._is_interrupted and self._is_interrupted():
                raise InterruptedError("audio feature extraction cancelled")
        try:
            if self._is_interrupted and self._is_interrupted():
                raise InterruptedError("audio feature extraction cancelled")
            yield
        finally:
            self._process_slots.release()

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

    def extraction_failure_message(self, file_path: Path | str) -> str | None:
        return self.extraction_failure_messages.get(str(file_path))

    def _remember_extraction_failure_tag(self, file_path: Path, message: str) -> None:
        key = str(file_path)
        normalized_message = str(message or "").strip()[:2000]
        if normalized_message:
            self.extraction_failure_messages[key] = normalized_message
            self.extraction_failure_messages.move_to_end(key)
            while len(self.extraction_failure_messages) > self.max_cache_entries:
                self.extraction_failure_messages.popitem(last=False)
        tag = self.extraction_failure_tag_for_message(message)
        if not tag:
            return
        self.extraction_failure_tags[key] = tag
        self.extraction_failure_tags.move_to_end(key)
        while len(self.extraction_failure_tags) > self.max_cache_entries:
            self.extraction_failure_tags.popitem(last=False)

    def _cache_negative_and_return_none(self, file_path: Path, message: str = "") -> None:
        self._remember_extraction_failure_tag(file_path, message)
        self._negative_cache_set(file_path)
        return None

    def _subprocess_options(self) -> dict:
        options = {
            "capture_output": True,
            "text": True,
            # The extractor's JSON protocol is always UTF-8. Relying on the
            # process locale corrupts non-ASCII paths in frozen Windows apps.
            "encoding": "utf-8",
        }
        if os.name == "nt":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0
            options["creationflags"] = subprocess.CREATE_NO_WINDOW | 0x00000040
            options["startupinfo"] = startupinfo
        return options

    def _run_batch_extractor(self, manifest_path: Path) -> subprocess.CompletedProcess[str]:
        """Run a JSONL batch with a per-file inactivity watchdog."""
        options = self._subprocess_options()
        options.pop("capture_output", None)
        options["stdout"] = subprocess.PIPE
        options["stderr"] = subprocess.PIPE
        with self._process_slot():
            return self._run_batch_extractor_in_slot(manifest_path, options)

    def _run_batch_extractor_in_slot(self, manifest_path: Path, options: dict) -> subprocess.CompletedProcess[str]:
        process = subprocess.Popen([self.extractor_path, "--batch", str(manifest_path)], **options)
        output: queue.Queue[str | None] = queue.Queue()
        stderr_lines: list[str] = []

        def read_stdout() -> None:
            try:
                if process.stdout is not None:
                    for line in process.stdout:
                        output.put(line)
            finally:
                output.put(None)

        def read_stderr() -> None:
            if process.stderr is not None:
                for line in process.stderr:
                    stderr_lines.append(line)

        reader = threading.Thread(target=read_stdout, name="unshuffle-extractor-output", daemon=True)
        error_reader = threading.Thread(target=read_stderr, name="unshuffle-extractor-errors", daemon=True)
        reader.start()
        error_reader.start()
        stdout_lines: list[str] = []
        try:
            while True:
                try:
                    line = output.get(timeout=self.EXTRACT_TIMEOUT_SECONDS)
                except queue.Empty as exc:
                    raise subprocess.TimeoutExpired(
                        process.args,
                        self.EXTRACT_TIMEOUT_SECONDS,
                    ) from exc
                if line is None:
                    break
                stdout_lines.append(line)
            returncode = process.wait(timeout=self.EXTRACT_TIMEOUT_SECONDS)
            error_reader.join(timeout=1)
            return subprocess.CompletedProcess(
                process.args,
                returncode,
                "".join(stdout_lines),
                "".join(stderr_lines),
            )
        except Exception as exc:
            if process.poll() is None:
                process.kill()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                logging.warning("C++ batch extractor did not exit after termination.")
            if isinstance(exc, subprocess.TimeoutExpired):
                exc.output = "".join(stdout_lines)
                exc.stderr = "".join(stderr_lines)
            raise

    @staticmethod
    def _should_retry_extraction_error(message: str) -> bool:
        normalized = str(message or "").strip().lower()
        definitive = (
            "file is empty",
            "file is silent",
            "audio too short",
            "nan or infinite",
            "severe audio clipping",
            "invalid wav format",
        )
        return not any(marker in normalized for marker in definitive)

    def _fallback_individual_payloads(
        self,
        pending: List[Path],
        results: Dict[Path, Optional[FeaturePayload]],
    ) -> Dict[Path, Optional[FeaturePayload]]:
        """Retry a failed batch without multiplying timeout by every file serially."""
        if not pending:
            return results
        workers = max(1, min(self.FALLBACK_EXTRACTOR_WORKERS, len(pending)))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="unshuffle-extractor-retry") as executor:
            futures = {executor.submit(self.extract_feature_payload, path): path for path in pending}
            for future in as_completed(futures):
                if self._is_interrupted and self._is_interrupted():
                    for pending_future in futures:
                        pending_future.cancel()
                    break
                path = futures[future]
                try:
                    results[path] = future.result()
                except Exception as exc:
                    logging.error("C++ extractor retry failed for %s: %s", path.name, exc)
                    results[path] = self._cache_negative_and_return_none(path, str(exc))
                finally:
                    self._report_extraction_complete(path)
        return results

    def _payload_from_extractor_data(self, file_path: Path, data: dict) -> Optional[FeaturePayload]:
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
            with self._process_slot():
                result = subprocess.run(
                    [self.extractor_path, "--file", str(file_path)],
                    **self._subprocess_options(),
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

            return self._payload_from_extractor_data(file_path, json.loads(result.stdout))
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

    def extract_feature_payloads_bulk(self, file_paths: List[Path]) -> Dict[Path, Optional[FeaturePayload]]:
        results: Dict[Path, Optional[FeaturePayload]] = {}
        pending: List[Path] = []
        for file_path in file_paths:
            cached = self._cache_get(file_path)
            if cached is not None:
                results[file_path] = FeaturePayload(vector=cached)
                self._report_extraction_complete(file_path)
                continue
            if self._negative_cache_get(file_path):
                results[file_path] = None
                self._report_extraction_complete(file_path)
                continue
            ext = file_path.suffix.lower()
            if ext not in self.SUPPORTED_EXTS:
                if ext == ".m4a":
                    logging.info(
                        "Acoustic Indexing: Skipping .m4a (not supported by C++ engine) - %s",
                        file_path.name,
                    )
                results[file_path] = self._cache_negative_and_return_none(file_path)
                self._report_extraction_complete(file_path)
                continue
            pending.append(file_path)

        if not pending:
            return results

        if not Path(self.extractor_path).exists():
            logging.error("Similarity Engine: Extractor not found at %s", self.extractor_path)
            return self._fallback_individual_payloads(pending, results)

        manifest_path: Path | None = None
        batch_incomplete = False
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, suffix=".txt") as manifest:
                manifest_path = Path(manifest.name)
                for file_path in pending:
                    manifest.write(str(file_path))
                    manifest.write("\n")
            result = self._run_batch_extractor(manifest_path)
        except subprocess.TimeoutExpired as exc:
            logging.warning(
                "C++ batch extractor made no progress for %ss; retrying only unresolved files.",
                self.EXTRACT_TIMEOUT_SECONDS,
            )
            result = subprocess.CompletedProcess(
                getattr(exc, "cmd", [self.extractor_path, "--batch", str(manifest_path)]),
                -1,
                str(getattr(exc, "output", "") or ""),
                str(getattr(exc, "stderr", "") or ""),
            )
            batch_incomplete = True
        except Exception as exc:
            logging.info("C++ batch extractor unavailable; falling back to per-file extraction: %s", exc)
            return self._fallback_individual_payloads(pending, results)
        finally:
            if manifest_path is not None:
                try:
                    manifest_path.unlink()
                except Exception:
                    pass

        if result.returncode != 0 and not result.stdout.strip():
            logging.info(
                "C++ batch extractor failed with %s; falling back to per-file extraction: %s",
                result.returncode,
                (result.stderr or "").strip(),
            )
            return self._fallback_individual_payloads(pending, results)

        pending_by_path = {
            os.path.normcase(os.path.normpath(str(file_path))): file_path
            for file_path in pending
        }
        seen: set[Path] = set()
        retry_paths: set[Path] = set()
        try:
            for line in result.stdout.splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                path_text = row.get("path")
                if not isinstance(path_text, str):
                    raise ValueError("batch row missing path")
                file_path = pending_by_path.get(os.path.normcase(os.path.normpath(path_text)))
                if file_path is None:
                    raise ValueError(f"batch row path was not requested: {path_text!r}")
                seen.add(file_path)
                if row.get("ok"):
                    payload_data = row.get("payload")
                    if not isinstance(payload_data, dict):
                        raise ValueError("batch success row missing payload")
                    results[file_path] = self._payload_from_extractor_data(file_path, payload_data)
                    self._report_extraction_complete(file_path)
                else:
                    error_text = str(row.get("error") or "")
                    if self._should_retry_extraction_error(error_text):
                        retry_paths.add(file_path)
                        logging.warning(
                            "C++ batch extractor could not decode %s; retrying once individually: %s",
                            file_path.name,
                            error_text.strip(),
                        )
                    else:
                        logging.error("C++ Extractor Error for %s: %s", file_path.name, error_text.strip())
                        results[file_path] = self._cache_negative_and_return_none(file_path, error_text)
                        self._report_extraction_complete(file_path)
        except Exception as exc:
            logging.info("C++ batch extractor emitted invalid output; retrying unresolved files: %s", exc)
            batch_incomplete = True

        unresolved = [
            file_path
            for file_path in pending
            if file_path in retry_paths or file_path not in seen
        ]
        if batch_incomplete and not unresolved:
            logging.debug("Incomplete batch output contained a result for every requested file.")
        return self._fallback_individual_payloads(unresolved, results)

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
