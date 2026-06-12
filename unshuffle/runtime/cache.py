import csv
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import logging
import os
from pathlib import Path

from typing import Any, Callable, Dict, List, Optional, Tuple

from ..core.constants import AUDIO_EXTS
from ..core.hashing import get_file_hash
from ..core.path_safety import is_symlink_or_reparse
from ..persistence import UnshuffleDB


class CacheMixin:
    db: Optional[UnshuffleDB] = None
    local_db: Optional[UnshuffleDB] = None
    progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None
    interrupted: bool = False
    target_dir: Path

    def _hash_audio_file(self, path: Path):
        return get_file_hash(path, interrupted_check=lambda: self.interrupted)

    def _find_audio_files(self, target_dir: Path):
        system_folders = {".git", "__macosx", ".trash", ".ds_store", "do_not_delete_unshuffle"}
        for root, dirs, files in os.walk(target_dir):
            if any(dir_name.lower() in system_folders for dir_name in Path(root).parts):
                continue
            root_path = Path(root)
            dirs[:] = [directory for directory in dirs if not is_symlink_or_reparse(root_path / directory)]
            for file_name in files:
                path = root_path / file_name
                if path.suffix.lower() in AUDIO_EXTS and not file_name.startswith("._") and not is_symlink_or_reparse(path):
                    yield path

    def _collect_audio_files(self, target_dir: Path):
        collected = []
        for path in self._find_audio_files(target_dir):
            try:
                stat_result = path.stat()
            except OSError:
                continue
            collected.append((path, stat_result.st_size, stat_result.st_mtime))
        return collected

    def _rebuild_index(self):
        self.log("Deep-scanning library for existing audio files...")

        if not self.target_dir.exists():
            self.log("Maintenance Complete: 0 unique hashes added to index.")
            return

        audio_files = self._collect_audio_files(self.target_dir)
        total = len(audio_files)
        self.log(f"Found {total} audio files to index. Rebuilding hashes...")

        if total <= 1:
            iterable = enumerate(audio_files, 1)
            for index, (path, size, mtime) in iterable:
                if self.interrupted:
                    self.log("Rebuild interrupted by user.")
                    break
                if self.progress_callback:
                    self.progress_callback({"current": index, "total": total, "message": f"Hashing: {path.name}"})
                file_hash = self._hash_audio_file(path)
                if file_hash:
                    try:
                        self.seen_hashes[file_hash] = path.relative_to(self.target_dir).as_posix()
                    except ValueError:
                        self.seen_hashes[file_hash] = path.as_posix()
                    self.seen_hash_metadata[file_hash] = (size, mtime)
            self.log(f"Maintenance Complete: {len(self.seen_hashes)} unique hashes added to index.")
            return

        max_workers = min(max(os.cpu_count() or 1, 1), 8, total)
        pending_metadata = {}
        completed = 0
        with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="unshuffle-cache") as executor:
            futures = {}
            for path, size, mtime in audio_files:
                future = executor.submit(self._hash_audio_file, path)
                futures[future] = path
                pending_metadata[path] = (size, mtime)

            while futures:
                if self.interrupted:
                    executor.shutdown(wait=False, cancel_futures=True)
                    self.log("Rebuild interrupted by user.")
                    break

                done, _pending = wait(tuple(futures), return_when=FIRST_COMPLETED)
                for future in done:
                    path = futures.pop(future)
                    completed += 1
                    if self.progress_callback:
                        self.progress_callback({"current": completed, "total": total, "message": f"Hashing: {path.name}"})
                    file_hash = future.result()
                    if file_hash:
                        try:
                            self.seen_hashes[file_hash] = path.relative_to(self.target_dir).as_posix()
                        except ValueError:
                            self.seen_hashes[file_hash] = path.as_posix()
                        self.seen_hash_metadata[file_hash] = pending_metadata[path]

        self.log(f"Maintenance Complete: {len(self.seen_hashes)} unique hashes added to index.")

    def _initialize_cache_state(self):
        from ..core.constants import refresh_alias_structures
        from ..core.config import get_config
        from ..logic.classification import get_scoring_engine, reset_scoring_engine
        from ..persistence import get_db, sync_alias_library, sync_full_config

        if hasattr(self, "db") and self.db:
            self.db.close()
        self.db = get_db(self.target_dir)

        config = get_config()
        sync_alias_library(self.db, config.get("ALIAS_TABLE", {}))
        sync_full_config(self.db, config)

        refresh_alias_structures(db=self.db)
        reset_scoring_engine()
        get_scoring_engine()

    def refresh_seen_hashes(self, rebuild=False):
        self.seen_hashes = {}
        self.seen_hash_metadata = {}

        if rebuild:
            self._rebuild_index()
            return True

        if hasattr(self.db, "prefetch_bloom_filter"):
            self.db.prefetch_bloom_filter()

        self.log("Loaded memory-resident hash filter from database.")
        return True

    def load_cache(self, force_reset=False, rebuild=False):
        self._initialize_cache_state()
        database = self._primary_database() if hasattr(self, "_primary_database") else self.db
        if force_reset and database is not None and hasattr(database, "clear_cache"):
            database.clear_cache()
            self.seen_hashes = {}
            self.seen_hash_metadata = {}
            if not rebuild:
                self.log("Cache reset complete.")
                return True
        refreshed = self.refresh_seen_hashes(rebuild=rebuild)
        if refreshed and rebuild:
            return self.save_cache()
        return refreshed

    def save_cache(self):
        if not self.seen_hashes:
            return True

        database = self._primary_database() if hasattr(self, "_primary_database") else self.db
        if database is None:
            return False

        data = []
        for file_hash, path in self.seen_hashes.items():
            size, mtime = self.seen_hash_metadata.get(file_hash, (0, 0.0))
            data.append((file_hash, _cache_path_text(path), size, mtime))

        database.update_cache_bulk(data)
        return True

    def save_legend(self):
        if not self.prefix_map:
            return True
        legend_path = self.target_dir / "prefix_legend.csv"
        try:
            with open(legend_path, "w", newline="", encoding="utf-8") as file_handle:
                writer = csv.writer(file_handle)
                writer.writerow(["Prefix", "Full Pack Path"])
                for prefix, full in sorted(self.prefix_map.items()):
                    writer.writerow([prefix, full])
            return True
        except OSError as exc:
            self.log(f"Failed to save prefix legend: {exc}", level=logging.ERROR)
            return False


def _cache_path_text(value) -> str:
    if isinstance(value, Path):
        return value.as_posix()
    return str(value).replace("\\", "/")
