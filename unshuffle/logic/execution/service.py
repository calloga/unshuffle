import logging
import shutil
from pathlib import Path
from typing import Literal, Optional

from ...core.hashing import get_file_hash
from ...core.path_safety import (
    is_path_within_directory,
)
from .destination import DestinationContainmentError, DestinationResolver
from .duplicates import handle_duplicate_record
from .placement import place_record_file
from .preserved import path_under_session_source_root, process_preserved_record, resolve_preserved_destination_root
from .reporting import csv_record_row
from .transfer import execute_file_transfer, execute_folder_transfer, validate_folder_transfer_tree


class ExecutionMixin:
    def _path_under_session_source_root(self, path: Path) -> bool:
        return path_under_session_source_root(self, path)

    def _resolve_preserved_destination_root(self, preserved_root: Path) -> Path:
        """Place HANDSOFF folders under a matching target ancestor when possible."""
        return resolve_preserved_destination_root(self, preserved_root)

    def _execute_file_transfer(
        self,
        source_path: Path,
        dest_path: Path,
        dest_folder: Path,
        move: bool,
        source_hash: Optional[str] = None,
    ) -> Path | Literal["stale"] | None:
        return execute_file_transfer(self, source_path, dest_path, dest_folder, move, source_hash)

    def _execute_folder_transfer(self, source_dir: Path, dest_dir: Path, move: bool):
        return execute_folder_transfer(self, source_dir, dest_dir, move)

    def _validate_folder_transfer_tree(self, source_dir: Path) -> bool:
        return validate_folder_transfer_tree(self, source_dir)

    def _process_single_record(self, record, move, dry_run, flat, no_prefix, csv_writer):
        self._last_duplicate_trash_path = None
        self._last_effective_action = "move" if move else "copy"
        self._last_record_hash = None
        if record.is_preserved:
            result, dest_path, dest_folder = process_preserved_record(self, record, move=move, dry_run=dry_run)

        else:
            file_hash = record.hash or get_file_hash(record.source_path, interrupted_check=lambda: self.interrupted)
            if not file_hash:
                if self.interrupted:
                    return "interrupted", None
                return "stale", record.source_path
            self._last_record_hash = file_hash

            resolver = getattr(self, "destination_resolver", None) or DestinationResolver()
            self.destination_resolver = resolver
            try:
                resolution = resolver.resolve(
                    record,
                    self.target_dir,
                    flat,
                    no_prefix,
                    self.prefix_map,
                    active_tree_profile=getattr(self, "active_tree_profile", None),
                    records=getattr(self, "_execution_records", None) or [record],
                )
            except DestinationContainmentError as exc:
                self.log(f"  ! {exc}", level=logging.ERROR)
                return "error", record.source_path
            dest_path = Path(resolution.dest_path)
            dest_folder = Path(resolution.dest_folder)
            if not is_path_within_directory(dest_folder, self.target_dir) or not is_path_within_directory(dest_path, self.target_dir):
                self.log(f"  ! Refusing destination outside target: {dest_path}", level=logging.ERROR)
                return "error", dest_path

            result = handle_duplicate_record(self, record, file_hash, move=move, dry_run=dry_run, move_file=shutil.move) or "copied"

            if result == "copied":
                result, dest_path = place_record_file(self, record, dest_path, dest_folder, file_hash, move=move, dry_run=dry_run)
                if result == "stale":
                    result = "error"

        if result != "error" and csv_writer:
            csv_writer.writerow(csv_record_row(record, dest_path, dest_folder))

        return result, dest_path
