from __future__ import annotations

import csv
import json
import logging
import os
import threading
import uuid
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from unshuffle.bridge.workflow_bridge import create_workflow_bridge
from unshuffle.core import parse_tags, tags_to_search_text
from unshuffle.core.features import CURRENT_EXTRACTOR_VERSION, CURRENT_FEATURE_SCHEMA, CURRENT_FEATURE_SPACE_VERSION
from unshuffle.persistence import UnshuffleDB


class _ImportCancelled(RuntimeError):
    pass


def _extend_common_source_roots(roots_by_drive: dict[str, Path], directory: Path) -> None:
    """Update one common source root per drive using bounded memory."""
    key = directory.drive.casefold() or "relative"
    current = roots_by_drive.get(key)
    if current is None:
        roots_by_drive[key] = directory
        return
    try:
        roots_by_drive[key] = Path(os.path.commonpath((str(current), str(directory))))
    except (OSError, ValueError):
        # Different path dialects should already have different drive keys. Keep
        # the first useful root if a malformed legacy CSV defeats commonpath().
        return


class CsvImportWorker(QThread):
    """Stream a CSV staging plan into a new database-backed session."""

    progress = Signal(dict)
    completed = Signal(dict)
    error = Signal(str)

    def __init__(self, file_path: Path, target_path: Path, *, existing_engine=None, parent=None):
        super().__init__(parent)
        self.file_path = Path(file_path)
        self.target_path = Path(target_path)
        self.existing_engine = existing_engine
        self._cancel_requested = threading.Event()

    def request_cancel(self) -> None:
        self._cancel_requested.set()

    def _check_cancelled(self) -> None:
        if self._cancel_requested.is_set():
            raise _ImportCancelled("CSV import canceled.")

    def _count_rows(self) -> int:
        with self.file_path.open("r", encoding="utf-8", newline="") as file_handle:
            reader = csv.DictReader(file_handle)
            self._validate_headers(reader.fieldnames)
            return sum(1 for _row in reader)

    @staticmethod
    def _validate_headers(fieldnames) -> None:
        headers = set(fieldnames or ())
        required = {"source_directory"}
        if not required.issubset(headers) or not ({"source_filename", "sample_name"} & headers):
            raise ValueError(
                "CSV must contain source_directory and either source_filename or sample_name columns."
            )

    def run(self) -> None:
        engine = None
        session_id = ""
        owns_engine = False
        try:
            self.progress.emit({
                "phase": "Reading CSV",
                "message": "Checking CSV records...",
                "current": 0,
                "total": 0,
            })
            total = self._count_rows()
            if total <= 0:
                raise ValueError("The CSV contains no records.")
            self._check_cancelled()

            self.progress.emit({
                "phase": "Preparing Destination",
                "message": "Preparing an imported session...",
                "current": 0,
                "total": total,
            })
            engine = self.existing_engine
            if engine is None:
                engine = create_workflow_bridge(self.target_path)
                session_id = str(engine.session_id or "")
                owns_engine = True
            else:
                session_id = f"csv_{uuid.uuid4().hex}"
            if not session_id:
                raise RuntimeError("Could not create an import session.")

            db = engine.db
            source_roots_by_drive: dict[str, Path] = {}
            inserted = 0

            def staging_rows():
                nonlocal inserted
                with self.file_path.open("r", encoding="utf-8", newline="") as file_handle:
                    reader = csv.DictReader(file_handle)
                    self._validate_headers(reader.fieldnames)
                    for row_number, row in enumerate(reader, start=2):
                        self._check_cancelled()
                        source_directory = str(row.get("source_directory") or "").strip()
                        source_filename = str(
                            row.get("source_filename") or row.get("sample_name") or ""
                        ).strip()
                        if not source_directory or not source_filename:
                            raise ValueError(
                                f"CSV row {row_number} is missing its source directory or filename."
                            )
                        source_dir = Path(source_directory)
                        _extend_common_source_roots(source_roots_by_drive, source_dir)
                        source_path = source_dir / source_filename
                        row_id = inserted
                        inserted += 1
                        yield (
                            row_id,
                            str(source_path),
                            source_path.name,
                            str(row.get("pack") or "Unknown"),
                            str(row.get("category") or "Utility"),
                            str(row.get("subcategory") or ""),
                            str(row.get("audio_type") or "Oneshots"),
                            tags_to_search_text(parse_tags(row.get("tags") or "")),
                            "1.0",
                            0.0,
                            "",
                            None,
                            "[]",
                            "{}",
                            None,
                            None,
                            None,
                            None,
                            "[]",
                            None,
                            False,
                        )
                        if inserted % 250 == 0:
                            self.progress.emit({
                                "phase": "Importing CSV",
                                "message": "Importing CSV records...",
                                "current": inserted,
                                "total": total,
                            })

            # The session ID is new, so partial data cannot replace the visible session.
            db.register_session(
                session_id,
                source=self.target_path,
                target=self.target_path,
                mode="pending",
            )
            db.add_staging_records_iter(session_id, staging_rows(), batch_size=1000)
            self._check_cancelled()
            source_roots = list(source_roots_by_drive.values())
            db.register_session(
                session_id,
                source=source_roots[0] if source_roots else self.target_path,
                target=self.target_path,
                mode="pending",
            )
            db.set_session_sources(session_id, source_roots)
            self.progress.emit({
                "phase": "Opening Imported Session",
                "message": "Preparing the imported library...",
                "current": inserted,
                "total": total,
            })
            self.completed.emit({
                "engine": engine,
                "session_id": session_id,
                "record_count": inserted,
                "source_roots": source_roots,
                "cancelled": False,
            })
            if owns_engine:
                engine = None
        except _ImportCancelled:
            if engine is not None and session_id:
                try:
                    engine.db.delete_session(session_id)
                except Exception:
                    pass
            self.completed.emit({"cancelled": True, "record_count": 0})
        except Exception as exc:
            if engine is not None and session_id:
                try:
                    engine.db.delete_session(session_id)
                except Exception:
                    pass
            self.error.emit(str(exc))
        finally:
            if owns_engine and engine is not None:
                try:
                    engine.close()
                except Exception:
                    pass


class PortableSessionImportWorker(QThread):
    """Copy a selected portable session without performing filesystem/SQLite work on the GUI thread."""

    progress = Signal(dict)
    completed = Signal(dict)
    error = Signal(str)

    def __init__(
        self,
        local_db_path: Path,
        global_db,
        session: dict,
        source_remaps: dict[str, Path],
        remapped_sources: list[Path],
        import_target_root: Path,
        parent=None,
    ):
        super().__init__(parent)
        self.local_db_path = Path(local_db_path)
        self.global_db = global_db
        self.session = dict(session)
        self.source_remaps = dict(source_remaps)
        self.remapped_sources = list(remapped_sources)
        self.import_target_root = Path(import_target_root)

    def run(self) -> None:
        from .data_manager import (
            PORTABLE_SESSION_TABLES,
            SESSION_METADATA_PORTABLE_MANIFEST_KEY,
            SESSION_METADATA_SAVED_FILTERS_KEY,
            copy_portable_session_table,
            remap_imported_source_path,
            remap_imported_staging_row,
            staging_row_tuple,
        )

        local_db = None
        try:
            local_db = UnshuffleDB(self.local_db_path)
            session_id = str(self.session.get("session_id") or "")
            total_staging = int(local_db.conn.execute(
                "SELECT COUNT(*) FROM staging_records WHERE session_id = ?",
                (session_id,),
            ).fetchone()[0])

            self.progress.emit({
                "phase": "Checking Imported Files",
                "message": "Checking file availability...",
                "current": 0,
                "total": total_staging,
            })
            importable_count = 0
            skipped_count = 0
            checked_count = 0
            for batch in local_db.iter_staging_records(session_id, batch_size=1000):
                for row in batch:
                    source_path = str(row.get("source_path") or "").strip()
                    normalized = source_path.replace("\\", "/").lower()
                    if not source_path or "/.unshuffle/" in normalized or "/do_not_delete_unshuffle/" in normalized:
                        skipped_count += 1
                        continue
                    if remap_imported_source_path(source_path, self.source_remaps).exists():
                        importable_count += 1
                    else:
                        skipped_count += 1
                checked_count += len(batch)
                self.progress.emit({
                    "phase": "Checking Imported Files",
                    "message": "Checking file availability...",
                    "current": checked_count,
                    "total": total_staging,
                })
            if importable_count <= 0:
                raise ValueError(
                    f"All {total_staging} files in this session are unmounted or missing on this system."
                )

            self.progress.emit({
                "phase": "Preparing Destination",
                "message": "Preparing the imported session...",
                "current": 0,
                "total": importable_count,
            })
            self.global_db.clear_staging(session_id)
            self.global_db.delete_session(session_id)
            self.global_db.register_session(
                session_id=session_id,
                source=remap_imported_source_path(
                    self.session.get("source_path")
                    or (self.remapped_sources[0] if self.remapped_sources else "."),
                    self.source_remaps,
                ),
                target=self.import_target_root,
                mode=self.session.get("mode") or "pending",
                is_flat=bool(self.session.get("is_flat")),
            )
            self.global_db.set_session_sources(session_id, self.remapped_sources)

            copied_count = 0

            def imported_rows():
                nonlocal copied_count
                for batch in local_db.iter_staging_records(session_id, batch_size=1000):
                    for row in batch:
                        source_path = str(row.get("source_path") or "").strip()
                        normalized = source_path.replace("\\", "/").lower()
                        if not source_path or "/.unshuffle/" in normalized or "/do_not_delete_unshuffle/" in normalized:
                            continue
                        remapped = remap_imported_staging_row(row, self.source_remaps)
                        if Path(remapped["source_path"]).exists():
                            yield staging_row_tuple(remapped)
                            copied_count += 1
                    self.progress.emit({
                        "phase": "Copying Session Records",
                        "message": "Copying staging records...",
                        "current": copied_count,
                        "total": importable_count,
                    })

            self.global_db.add_staging_records_iter(session_id, imported_rows(), batch_size=1000)

            cache_rows = []
            cache_checked = 0
            for batch in self.global_db.iter_staging_records(session_id, batch_size=500):
                for row in batch:
                    file_hash = str(row.get("hash") or "").strip()
                    source_path = Path(str(row.get("source_path") or ""))
                    if not file_hash or not source_path.exists():
                        continue
                    try:
                        stat = source_path.stat()
                    except OSError:
                        continue
                    cache_rows.append((
                        file_hash,
                        source_path,
                        int(stat.st_size),
                        float(stat.st_mtime),
                        row.get("feature_vector", row.get("acoustic_vector")),
                        row.get("feature_space_version") or CURRENT_FEATURE_SPACE_VERSION,
                        CURRENT_EXTRACTOR_VERSION,
                        row.get("feature_schema_json") or json.dumps(list(CURRENT_FEATURE_SCHEMA)),
                        row.get("analysis_status") or "ok",
                        row.get("analysis_tags_json") or "[]",
                        row.get("fast_hash"),
                    ))
                    if len(cache_rows) >= 256:
                        self.global_db.update_cache_bulk(cache_rows)
                        cache_rows.clear()
                cache_checked += len(batch)
                self.progress.emit({
                    "phase": "Restoring Audio Cache",
                    "message": "Restoring cached analysis...",
                    "current": cache_checked,
                    "total": importable_count,
                })
            if cache_rows:
                self.global_db.update_cache_bulk(cache_rows)

            try:
                for table_index, table in enumerate(PORTABLE_SESSION_TABLES, start=1):
                    table_total = int(local_db.conn.execute(
                        f"SELECT COUNT(*) FROM {table} WHERE session_id = ?",
                        (session_id,),
                    ).fetchone()[0])
                    phase = f"Restoring Session Metadata ({table_index}/{len(PORTABLE_SESSION_TABLES)})"
                    copy_portable_session_table(
                        local_db.conn,
                        self.global_db.conn,
                        table,
                        session_id,
                        progress_callback=lambda copied, phase=phase, total=table_total, name=table: self.progress.emit({
                            "phase": phase,
                            "message": f"Restoring {name.replace('_', ' ')}...",
                            "current": copied,
                            "total": total,
                        }),
                    )
                with self.global_db.write_transaction():
                    for table in ("coherence_results", "refinement_candidates"):
                        self.global_db.conn.execute(
                            f"""
                            DELETE FROM {table}
                            WHERE session_id = ?
                              AND NOT EXISTS (
                                  SELECT 1 FROM staging_records AS staging
                                  WHERE staging.session_id = {table}.session_id
                                    AND CAST(staging.row_id AS TEXT) = CAST({table}.record_id AS TEXT)
                              )
                            """,
                            (session_id,),
                        )
            except Exception:
                logging.exception("Failed to import portable coherence metadata.")

            try:
                reviewed_count = 0
                for batch in local_db.iter_staging_records(session_id, batch_size=800):
                    decisions = local_db.list_coherence_review_decisions(
                        [str(row.get("source_path") or "") for row in batch],
                        [str(row.get("hash") or "") for row in batch],
                    )
                    for decision in decisions:
                        decision["source_path"] = str(
                            remap_imported_source_path(decision.get("source_path"), self.source_remaps)
                        )
                    if decisions:
                        self.global_db.upsert_coherence_review_decisions(session_id, decisions)
                    reviewed_count += len(batch)
                    self.progress.emit({
                        "phase": "Restoring Review Decisions",
                        "message": "Restoring coherence review decisions...",
                        "current": reviewed_count,
                        "total": importable_count,
                    })
            except Exception:
                logging.exception("Failed to import portable coherence review decisions.")

            self.completed.emit({
                "session_id": session_id,
                "record_count": importable_count,
                "skipped_count": skipped_count,
                "saved_filters_json": local_db.get_session_metadata(
                    session_id, SESSION_METADATA_SAVED_FILTERS_KEY
                ),
                "portable_manifest_json": local_db.get_session_metadata(
                    session_id, SESSION_METADATA_PORTABLE_MANIFEST_KEY
                ),
            })
        except Exception as exc:
            self.error.emit(str(exc))
        finally:
            if local_db is not None:
                local_db.close()
