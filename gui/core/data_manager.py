import logging
import csv
import json
import uuid
from pathlib import Path, PurePosixPath, PureWindowsPath

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QFileDialog, QInputDialog, QMessageBox

from unshuffle.bridge.persistence_bridge import PersistenceBridge
from unshuffle.core import PlanRecord, parse_tags, plan_records_from_staging_rows
from unshuffle.core.features import CURRENT_EXTRACTOR_VERSION, CURRENT_FEATURE_SCHEMA, CURRENT_FEATURE_SPACE_VERSION
from unshuffle.core.paths import DB_FILE_NAME, SYSTEM_FOLDER_NAME
from unshuffle.persistence.exports import export_staging_plan_csv

from ..utils.constants import StagingColumn, STAGING_HEADERS

SESSION_METADATA_SAVED_FILTERS_KEY = "saved_filters"
SESSION_METADATA_PORTABLE_MANIFEST_KEY = "portable_session_manifest"
PORTABLE_SESSION_FORMAT_VERSION = 2
PORTABLE_SESSION_TABLES = ("coherence_results", "refinement_candidates", "anchor_profiles")


def staging_row_tuple(row: dict) -> tuple:
    return (
        row.get("row_id"), row.get("source_path"), row.get("sample_name"), row.get("pack"),
        row.get("category"), row.get("subcategory"), row.get("audio_type"), row.get("tags"),
        row.get("confidence"), row.get("duration"), row.get("hash"), row.get("fast_hash"),
        row.get("pack_candidates"), row.get("evidence_json"),
        row.get("feature_vector", row.get("acoustic_vector")), row.get("feature_space_version"),
        row.get("feature_schema_json"), row.get("analysis_status"), row.get("analysis_tags_json"),
        row.get("preserved_root"), row.get("is_preserved"),
    )


def remap_imported_staging_row(row: dict, source_remaps: dict[str, Path]) -> dict:
    remapped = dict(row)
    remapped["source_path"] = str(remap_imported_source_path(row.get("source_path"), source_remaps))
    preserved_root = str(row.get("preserved_root") or "").strip()
    if preserved_root:
        remapped["preserved_root"] = str(remap_imported_source_path(preserved_root, source_remaps))
    evidence_text = row.get("evidence_json")
    try:
        evidence = json.loads(evidence_text) if isinstance(evidence_text, str) else dict(evidence_text or {})
    except (TypeError, ValueError, json.JSONDecodeError):
        evidence = None
    if isinstance(evidence, dict):
        shadow = evidence.get("duplicate_shadow")
        if isinstance(shadow, dict) and shadow.get("duplicate_of_path"):
            shadow["duplicate_of_path"] = str(remap_imported_source_path(shadow["duplicate_of_path"], source_remaps))
            remapped["evidence_json"] = json.dumps(evidence)
    return remapped


def copy_portable_session_table(source_conn, destination_conn, table: str, session_id: str, *, batch_size: int = 1000) -> int:
    if table not in PORTABLE_SESSION_TABLES:
        raise ValueError(f"Unsupported portable session table: {table}")
    columns = [
        str(row[1])
        for row in source_conn.execute(f"PRAGMA table_info({table})")
        if str(row[1]) != "id"
    ]
    if not columns:
        return 0
    column_sql = ", ".join(columns)
    placeholders = ", ".join("?" for _ in columns)
    cursor = source_conn.execute(
        f"SELECT {column_sql} FROM {table} WHERE session_id = ?",
        (session_id,),
    )
    copied = 0
    with destination_conn:
        destination_conn.execute(f"DELETE FROM {table} WHERE session_id = ?", (session_id,))
        while True:
            rows = cursor.fetchmany(max(1, int(batch_size)))
            if not rows:
                return copied
            destination_conn.executemany(
                f"INSERT INTO {table} ({column_sql}) VALUES ({placeholders})",
                [tuple(row) for row in rows],
            )
            copied += len(rows)

class DataManager:
    """
    Handles data persistence, database synchronization, and CSV import/export.
    """
    def __init__(self, engine=None, app=None):
        self.engine = None
        self.bridge = None
        self.app = app
        if engine is not None:
            self.set_engine(engine)

    def set_engine(self, engine):
        self.engine = engine
        if engine is None:
            self.bridge = None
        elif isinstance(engine, PersistenceBridge):
            self.bridge = engine
        else:
            workflow = getattr(engine, "workflow", None)
            self.bridge = PersistenceBridge(workflow or engine)

    def set_bridge(self, bridge):
        self.bridge = bridge
        self.engine = bridge.workflow if bridge else None

    def _active_databases_for_target(self, target_path: Path):
        """Return engine-owned (local, global) databases for the active target."""
        runtime = getattr(self.bridge, "engine", None) if self.bridge is not None else None
        if runtime is None:
            runtime = getattr(self.engine, "engine", self.engine)
        runtime_target = getattr(runtime, "target_dir", None)
        try:
            if runtime_target is None or Path(runtime_target).resolve() != target_path.resolve():
                return None, None
        except (OSError, TypeError, ValueError):
            return None, None

        local_db_path = (target_path / SYSTEM_FOLDER_NAME / DB_FILE_NAME).resolve()

        def live_database(name, *, expect_local: bool):
            database = getattr(runtime, name, None)
            if database is None or bool(getattr(database, "_closed", False)):
                return None
            try:
                is_local = Path(database.db_path).resolve() == local_db_path
            except (AttributeError, OSError, TypeError, ValueError):
                is_local = False
            if is_local != expect_local:
                return None
            try:
                database.conn.execute("SELECT 1").fetchone()
            except Exception:
                return None
            return database

        local_database = live_database("local_db", expect_local=True) or live_database(
            "db", expect_local=True
        )
        global_database = live_database("db", expect_local=False)
        return local_database, global_database

    def sync_record_to_db(self, row_id, record):
        """Updates a single record in the staging database."""
        if not self.bridge or not self.bridge.has_session():
            return
        
        try:
            self.bridge.update_staging_record(row_id, record)
        except Exception as e:
            logging.error(f"Failed to sync record {row_id} to DB: {e}")

    def check_and_sync_local_db(self, target_path, parent_widget=None):
        """
        Synchronizes the local sidecar database with the global database.
        Returns True if a refresh is needed.
        """
        if not target_path:
            return False
        local_db = None
        global_db = None
        owned_databases = []
        try:
            from unshuffle.persistence import get_local_db, get_db
            target_path = Path(target_path)
            local_db, global_db = self._active_databases_for_target(target_path)
            if local_db is None:
                local_db = get_local_db(target_path)
                owned_databases.append(local_db)
            if global_db is None:
                global_db = get_db(target_path)
                owned_databases.append(global_db)
            
            local_sessions = local_db.get_recent_sessions(50)
            if not local_sessions:
                return False
            
            global_sessions = set(s['session_id'] for s in global_db.get_recent_sessions(200))
            missing = [s for s in local_sessions if s['session_id'] not in global_sessions]
            
            if missing:
                count = len(missing)
                if parent_widget:
                    reply = QMessageBox.question(parent_widget, "Sync History", 
                        f"Found {count} sessions on this drive. Sync history now?",
                        QMessageBox.Yes | QMessageBox.No)
                    if reply == QMessageBox.No:
                        return False
                    
                for s in missing:
                    sid = s['session_id']
                    source_path = s.get('source_path') or s.get('source_root')
                    target_root = s.get('target_root') or target_path
                    if not source_path:
                        continue
                    global_db.register_session(sid, Path(source_path), Path(target_root), s['mode'], s['is_flat'])
                    source_roots = local_db.get_session_sources(sid) or [source_path]
                    global_db.set_session_sources(sid, [Path(src) for src in source_roots if src])
                    records = local_db.get_session_records(sid)
                    global_db.add_records_bulk(sid, records)
                
                return True
        except Exception as e:
            logging.error(f"Data sync failed: {e}")
        finally:
            for db in owned_databases:
                if db is not None:
                    try:
                        db.close()
                    except Exception:
                        pass
        return False

    def export_to_csv(self, file_path, records):
        """Exports the current staging plan to a CSV file."""
        try:
            export_staging_plan_csv(Path(file_path), records)
            return True
        except Exception as e:
            logging.error(f"CSV Export failed: {e}")
            return False

    def import_from_csv(self, file_path):
        """Imports a staging plan from a CSV file."""
        imported = []
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    pack_name = row.get('pack', 'Unknown')
                    category = row.get('category', 'Utility')
                    sub = row.get('subcategory', '')
                    audio_type = row.get('audio_type', 'Oneshots')
                    
                    tags = parse_tags(row.get('tags', ''))
                    
                    rec = PlanRecord(
                        source_path=Path(row['source_directory']) / row.get('source_filename', row.get('sample_name', '')),
                        pack=pack_name,
                        category=category,
                        subcategory=sub,
                        audio_type=audio_type,
                        tags=tags,
                        hash="",
                        confidence="1.0"
                    )
                    imported.append(rec)
            return imported
        except Exception as e:
            logging.error(f"CSV Import failed: {e}")
            return None

    def reconstruct_plan_records(self, db_rows):
        """Converts raw database staging rows back into PlanRecord objects."""
        return plan_records_from_staging_rows(db_rows, parse_tags)

    def _show_session_export_success(self, local_db_path: Path, sources: list[str], parent_widget=None) -> None:
        parent = parent_widget or self.app
        message = QMessageBox(parent)
        message.setWindowTitle("Export Session")
        message.setIcon(QMessageBox.Information)
        message.setText("Staging session exported.")
        message.setInformativeText(
            "The session was saved to this folder's unshuffle sidecar.\n\n"
            "To restore it later, use Library > Import > From Staging Session and select this folder "
            "or the sidecar unshuffle.db file.\n\n"
            "If a source folder has moved or the drive letter changed, import will ask for its current location."
        )
        message.setDetailedText(
            f"Exported database:\n{local_db_path}\n\n"
            f"Linked source folders:\n" + "\n".join(str(source) for source in sources)
        )
        show_button = message.addButton("Show", QMessageBox.ActionRole)
        message.addButton(QMessageBox.Ok)
        message.exec()
        if message.clickedButton() is show_button:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(local_db_path.parent.absolute())))

    @staticmethod
    def _session_choice_label(session: dict) -> str:
        session_id = str(session.get("session_id") or "")
        timestamp = str(session.get("timestamp") or "").strip()
        source = str(session.get("source_path") or "").strip()
        parts = [session_id]
        if timestamp:
            parts.append(timestamp)
        if source:
            parts.append(source)
        return " | ".join(part for part in parts if part)

    def _choose_import_session(self, sessions: list[dict], parent_widget=None) -> dict | None:
        if not sessions:
            return None
        if len(sessions) == 1:
            return sessions[0]
        labels = [self._session_choice_label(session) for session in sessions]
        selected, ok = QInputDialog.getItem(
            parent_widget or self.app,
            "Import Session",
            "Select the staging session to import:",
            labels,
            0,
            False,
        )
        if not ok:
            return None
        try:
            return sessions[labels.index(selected)]
        except ValueError:
            return None

    def _prompt_source_remaps(self, sources: list[str], parent_widget=None) -> dict[str, Path] | None:
        remaps: dict[str, Path] = {}
        parent = parent_widget or self.app
        for source in sources:
            source_text = str(source or "").strip()
            if not source_text:
                continue
            source_path = Path(source_text)
            if source_path.exists():
                remaps[source_text] = source_path
                continue

            QMessageBox.information(
                parent,
                "Source Folder Moved",
                (
                    "Unshuffle could not find one of this session's source folders.\n\n"
                    f"{source_text}\n\n"
                    "Select the folder's current location to continue the import."
                ),
            )
            replacement = QFileDialog.getExistingDirectory(
                parent,
                "Select Current Source Folder",
                str(Path.home()),
            )
            if not replacement:
                return None
            remaps[source_text] = Path(replacement)
        return remaps

    def export_session_to_folder(self, folder_path, parent_widget=None) -> bool:
        """Exports the active staging session and metadata to a target folder."""
        if not self.bridge or not self.bridge.has_session():
            QMessageBox.warning(parent_widget or self.app, "Export Session", "No active staging session is loaded.")
            return False

        drafting = getattr(self.app, "drafting_controller", None)
        if drafting is not None and drafting.has_changes():
            if not drafting.confirm_clear_pending_draft("export this session"):
                return False

        global_db = self.bridge._get_db()
        session_id = str(self.bridge.session_id or "")
        if not session_id:
            QMessageBox.warning(parent_widget or self.app, "Export Session", "No active staging session is loaded.")
            return False
        sources = global_db.get_session_sources(session_id)

        # Pop warning dialog about source folder dependencies
        sources_list = "\n".join(f"- {s}" for s in sources)
        msg = (
            f"Exporting staging session '{session_id}' to target folder.\n\n"
            f"The following directory paths are linked to this session:\n{sources_list}\n\n"
            "These directories will be needed to fully restore the session later. "
            "If a source folder has moved or the drive letter changed, import will ask for its current location.\n\n"
            "Proceed with export?"
        )
        reply = QMessageBox.question(
            parent_widget or self.app,
            "Confirm Staging Session Export",
            msg,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if reply != QMessageBox.Yes:
            return False

        local_db = None
        try:
            from unshuffle.persistence import UnshuffleDB
            from unshuffle.core.paths import get_local_system_dir

            saved_filters = []
            settings_controller = getattr(self.app, "settings_controller", None)
            if settings_controller is not None and hasattr(settings_controller, "get_saved_filters"):
                saved_filters = settings_controller.get_saved_filters()

            export_path = Path(folder_path)
            if export_path.suffix == ".unshuffle" or export_path.is_file():
                local_db_path = export_path
                local_db_path.parent.mkdir(parents=True, exist_ok=True)
            else:
                local_system_dir = get_local_system_dir(export_path)
                local_system_dir.mkdir(parents=True, exist_ok=True)
                local_db_path = local_system_dir / "unshuffle.db"
            
            local_db = UnshuffleDB(local_db_path)
            
            # 1. Clear any pre-existing session export payload without deleting
            # the session parent row; sidecar DBs may have child history rows.
            local_db.clear_staging(session_id)
            with local_db.write_transaction():
                local_db.conn.execute("DELETE FROM records WHERE session_id = ?", (session_id,))

            # 2. Copy Session details
            sess = global_db.get_session(session_id)
            if sess:
                local_db.register_session(
                    session_id=session_id,
                    source=Path(sess.get("source_path") or "."),
                    target=Path(sess.get("target_root") or "."),
                    mode=sess.get("mode") or "pending",
                    is_flat=bool(sess.get("is_flat")),
                )
            if hasattr(local_db, "set_session_metadata"):
                local_db.set_session_metadata(
                    session_id,
                    SESSION_METADATA_SAVED_FILTERS_KEY,
                    json.dumps(saved_filters),
                )
                active_profile = getattr(getattr(self.app, "tree_organization_controller", None), "active_profile", None)
                profile_payload = None
                to_dict = getattr(active_profile, "to_dict", None)
                if callable(to_dict):
                    candidate = to_dict()
                    if isinstance(candidate, dict):
                        profile_payload = candidate
                local_db.set_session_metadata(
                    session_id,
                    SESSION_METADATA_PORTABLE_MANIFEST_KEY,
                    json.dumps({
                        "format_version": PORTABLE_SESSION_FORMAT_VERSION,
                        "active_tree_profile": profile_payload,
                    }),
                )

            # 3. Copy Session Sources
            local_db.set_session_sources(session_id, [Path(s) for s in sources if s])

            # 4. Copy Staging Records
            local_db.add_staging_records_iter(
                session_id,
                (
                    staging_row_tuple(row)
                    for batch in global_db.iter_staging_records(session_id, batch_size=1000)
                    for row in batch
                ),
                batch_size=1000,
            )

            # 5-7. Copy coherence metadata without materializing complete result sets.
            try:
                for table in PORTABLE_SESSION_TABLES:
                    copy_portable_session_table(global_db.conn, local_db.conn, table, session_id)
            except Exception:
                logging.exception("Failed to export coherence session metadata.")

            # 8. Copy review decisions scoped to this session without loading all paths.
            try:
                for batch in global_db.iter_staging_records(session_id, batch_size=800):
                    decisions = global_db.list_coherence_review_decisions(
                        [str(row.get("source_path") or "") for row in batch],
                        [str(row.get("hash") or "") for row in batch],
                    )
                    if decisions:
                        local_db.upsert_coherence_review_decisions(session_id, decisions)
            except Exception:
                logging.exception("Failed to export coherence review decisions.")

            self._show_session_export_success(local_db_path, sources, parent_widget=parent_widget)
            return True
        except Exception as e:
            logging.exception("Failed to export staging session")
            QMessageBox.critical(parent_widget or self.app, "Export Error", f"Failed to export staging session:\n{e}")
            return False
        finally:
            if local_db is not None:
                local_db.close()

    def import_session_from_folder(self, folder_path, parent_widget=None) -> bool:
        """Imports a staging session and staging records from a folder's local database sidecar."""
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication
        from unshuffle.persistence import UnshuffleDB
        from unshuffle.core.paths import get_local_system_dir

        import_path = Path(folder_path)
        if import_path.is_file():
            local_db_path = import_path
        else:
            local_system_dir = get_local_system_dir(import_path)
            local_db_path = local_system_dir / "unshuffle.db"
            
        if not local_db_path.exists():
            QMessageBox.warning(parent_widget or self.app, "Import Session", f"No staging session database found at:\n{local_db_path}")
            return False

        cursor_set = False
        local_db = None
        try:
            local_db = UnshuffleDB(local_db_path)
            recent = local_db.get_recent_sessions(100000)
            if not recent:
                QMessageBox.warning(parent_widget or self.app, "Import Session", "No staging sessions found in the target database.")
                return False

            sess = self._choose_import_session(recent, parent_widget=parent_widget)
            if sess is None:
                return False
            session_id = str(sess.get("session_id") or "")
            if not session_id:
                QMessageBox.warning(parent_widget or self.app, "Import Session", "Invalid or empty session ID in sidecar database.")
                return False

            QApplication.setOverrideCursor(Qt.WaitCursor)
            cursor_set = True
            if self.app and getattr(self.app, "footer", None):
                self.app.footer.set_status("Importing session records...")
                self.app.footer.log("<b>Staging Session:</b> reading and copying sidecar database...")
            
            total_staging = int(local_db.conn.execute(
                "SELECT COUNT(*) FROM staging_records WHERE session_id = ?",
                (session_id,),
            ).fetchone()[0])
            if total_staging <= 0:
                QMessageBox.warning(parent_widget or self.app, "Import Session", "No staging records found in the sidecar database.")
                return False

            sources = local_db.get_session_sources(session_id)
            source_remaps = self._prompt_source_remaps(sources, parent_widget=parent_widget)
            if source_remaps is None:
                return False
            remapped_sources = [
                source_remaps[str(source)].resolve() if str(source) in source_remaps else Path(source)
                for source in sources
                if str(source or "").strip()
            ]

            # Validate physical presence without materializing the session.
            importable_count = 0
            skipped_count = 0
            for batch in local_db.iter_staging_records(session_id, batch_size=1000):
                for row in batch:
                    source_path = str(row.get("source_path") or "").strip()
                    normalized = source_path.replace("\\", "/").lower()
                    if not source_path or "/.unshuffle/" in normalized or "/do_not_delete_unshuffle/" in normalized:
                        skipped_count += 1
                        continue
                    path = remap_imported_source_path(source_path, source_remaps)
                    if path.exists():
                        importable_count += 1
                    else:
                        logging.warning("Session import skipped missing file: %s -> %s", source_path, path)
                        skipped_count += 1

            if importable_count <= 0:
                QMessageBox.warning(
                    parent_widget or self.app,
                    "Import Session",
                    f"All {total_staging} files in this session are unmounted or missing on this system. Cannot import."
                )
                return False

            if skipped_count:
                QMessageBox.information(
                    parent_widget or self.app,
                    "Import Session",
                    f"Importing session: {skipped_count} out of {total_staging} files are missing on this system and were skipped."
                )

            # Wires/restores session into this computer's global database.
            import_target_root = import_session_target_root(import_path, local_db_path, sess.get("target_root"))
            global_db = self.bridge._get_db() if self.bridge else None
            if not global_db:
                global_db = self.engine.db if self.engine else None
            
            if not global_db:
                try:
                    from unshuffle.bridge.workflow_bridge import create_workflow_bridge
                    engine = create_workflow_bridge(import_target_root, session_id=session_id)
                    if self.app and hasattr(self.app, "set_runtime_context"):
                        self.app.set_runtime_context(engine=engine)
                    self.set_engine(engine)
                    global_db = self.bridge._get_db() if self.bridge else None
                except Exception as e:
                    logging.exception("Failed to connect engine during session import")

            if not global_db:
                raise RuntimeError("No active database available to register import.")

            # Clear old records in global db
            global_db.clear_staging(session_id)
            global_db.delete_session(session_id)

            # 1. Register Session details
            global_db.register_session(
                session_id=session_id,
                source=remap_imported_source_path(sess.get("source_path") or (sources[0] if sources else "."), source_remaps),
                target=import_target_root,
                mode=sess.get("mode") or "pending",
                is_flat=bool(sess.get("is_flat")),
            )

            # 2. Register Session Sources
            global_db.set_session_sources(session_id, remapped_sources)

            # 3. Stream staging records into the destination database.
            def imported_rows():
                for batch in local_db.iter_staging_records(session_id, batch_size=1000):
                    for row in batch:
                        source_path = str(row.get("source_path") or "").strip()
                        normalized = source_path.replace("\\", "/").lower()
                        if not source_path or "/.unshuffle/" in normalized or "/do_not_delete_unshuffle/" in normalized:
                            continue
                        remapped = remap_imported_staging_row(row, source_remaps)
                        if Path(remapped["source_path"]).exists():
                            yield staging_row_tuple(remapped)

            global_db.add_staging_records_iter(session_id, imported_rows(), batch_size=1000)
            # Seed the scoped analysis cache from trusted exported staging metadata.
            cache_rows = []
            for batch in global_db.iter_staging_records(session_id, batch_size=500):
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
                        global_db.update_cache_bulk(cache_rows)
                        cache_rows.clear()
            if cache_rows:
                global_db.update_cache_bulk(cache_rows)

            # 4-6. Restore coherence metadata in bounded batches.
            try:
                for table in PORTABLE_SESSION_TABLES:
                    copy_portable_session_table(local_db.conn, global_db.conn, table, session_id)
                with global_db.write_transaction():
                    for table in ("coherence_results", "refinement_candidates"):
                        global_db.conn.execute(
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
                logging.exception("Failed to import coherence session metadata.")

            # 7. Restore review decisions and remap their source paths.
            try:
                for batch in local_db.iter_staging_records(session_id, batch_size=800):
                    decisions = local_db.list_coherence_review_decisions(
                        [str(row.get("source_path") or "") for row in batch],
                        [str(row.get("hash") or "") for row in batch],
                    )
                    for decision in decisions:
                        decision["source_path"] = str(
                            remap_imported_source_path(decision.get("source_path"), source_remaps)
                        )
                    if decisions:
                        global_db.upsert_coherence_review_decisions(session_id, decisions)
            except Exception:
                logging.exception("Failed to import coherence review decisions.")

            # Update engine session ID
            if self.engine:
                if hasattr(self.engine, "update_state"):
                    self.engine.update_state(session_id=session_id)
                else:
                    self.engine.session_id = session_id
                self.engine.session_source_roots = [source for source in remapped_sources if source.exists()]
                if remapped_sources:
                    self.engine.session_source_root = remapped_sources[0]

            if hasattr(local_db, "get_session_metadata"):
                saved_filters_json = local_db.get_session_metadata(session_id, SESSION_METADATA_SAVED_FILTERS_KEY)
                if saved_filters_json:
                    try:
                        saved_filters = json.loads(saved_filters_json)
                    except (TypeError, json.JSONDecodeError):
                        saved_filters = []
                    settings_controller = getattr(self.app, "settings_controller", None)
                    if settings_controller is not None and hasattr(settings_controller, "save_saved_filters"):
                        restored_filters = saved_filters if isinstance(saved_filters, list) else []
                        settings_controller.save_saved_filters(restored_filters)
                        if getattr(self.app, "library_tab", None) is not None:
                            self.app.library_tab.set_saved_filters(restored_filters)
                        filter_controller = getattr(self.app, "filter_controller", None)
                        if filter_controller is not None and hasattr(filter_controller, "refresh_dock_filters"):
                            filter_controller.refresh_dock_filters()

                portable_json = local_db.get_session_metadata(session_id, SESSION_METADATA_PORTABLE_MANIFEST_KEY)
                if portable_json:
                    try:
                        portable = json.loads(portable_json)
                        profile_payload = portable.get("active_tree_profile") if isinstance(portable, dict) else None
                        if isinstance(profile_payload, dict):
                            from unshuffle.logic.tree_organization.models import TreeOrganizationProfile

                            tree_controller = getattr(self.app, "tree_organization_controller", None)
                            repository = getattr(tree_controller, "repository", None)
                            if tree_controller is not None and repository is not None:
                                imported_profile = TreeOrganizationProfile.from_dict(profile_payload)
                                existing = repository.get_profile(imported_profile.id)
                                if existing is not None and existing.to_dict() != imported_profile.to_dict():
                                    profile_payload = dict(profile_payload)
                                    profile_payload["id"] = f"profile_{uuid.uuid4().hex[:12]}"
                                    profile_payload["name"] = f"{imported_profile.name} (Imported)"
                                    imported_profile = TreeOrganizationProfile.from_dict(profile_payload)
                                saved_profile = existing or repository.save_profile(imported_profile)
                                tree_controller.active_profile = saved_profile
                                tree_controller._persist_active_profile_id(saved_profile.id)
                                tree_controller._sync_active_profile(refresh=False)
                    except (TypeError, ValueError, json.JSONDecodeError):
                        logging.exception("Failed to restore portable library structure metadata.")

            # Clear active drafts
            drafting = getattr(self.app, "drafting_controller", None)
            if drafting is not None:
                drafting.clear()

            # Attach the DB-backed session model directly; do not hydrate every record.
            stats = {
                "total_scanned": importable_count,
                "added_count": importable_count,
                "lib_dupe_count": 0,
                "session_dupe_count": 0,
                "total_dupe_count": 0,
            }
            self.app.workflow_controller.finalize_scan_data(
                [],
                False,
                stats,
                show_summary=False,
                persist_staging=False,
            )
            self.app.footer.log(f"<b>Staging Session:</b> imported {importable_count} records successfully.")
            return True
        except Exception as e:
            logging.exception("Failed to import staging session")
            QMessageBox.critical(parent_widget or self.app, "Import Error", f"Failed to import staging session:\n{e}")
            return False
        finally:
            if cursor_set:
                QApplication.restoreOverrideCursor()
            if local_db is not None:
                local_db.close()


def _pure_path_for_remap(path: object):
    text = str(path or "")
    if "\\" in text or (len(text) >= 2 and text[1] == ":"):
        return PureWindowsPath(text)
    return PurePosixPath(text)


def remap_imported_source_path(source_path: object, source_remaps: dict[str, Path]) -> Path:
    source_text = str(source_path or "")
    source_pure = _pure_path_for_remap(source_text)
    for original, replacement in sorted(source_remaps.items(), key=lambda item: len(str(item[0])), reverse=True):
        try:
            relative = source_pure.relative_to(_pure_path_for_remap(original))
        except ValueError:
            continue
        return Path(replacement).joinpath(*relative.parts)
    return Path(source_text)


def import_session_target_root(import_path: Path, local_db_path: Path, session_target_root: object) -> Path:
    from unshuffle.core.paths import DB_FILE_NAME, SYSTEM_FOLDER_NAME

    target_text = str(session_target_root or "").strip()
    if target_text:
        target_path = Path(target_text)
        try:
            if target_path.exists():
                return target_path
        except OSError:
            pass

    if (
        local_db_path.name.lower() == DB_FILE_NAME.lower()
        and local_db_path.parent.name.lower() == SYSTEM_FOLDER_NAME.lower()
    ):
        return local_db_path.parent.parent

    if import_path.is_dir():
        return import_path

    return local_db_path.parent


def imported_staging_record_ids(records_to_load: list[dict]) -> set[str]:
    return {
        str(row.get("row_id") if row.get("row_id") is not None else row.get("id"))
        for row in records_to_load
        if (row.get("row_id") is not None or row.get("id") is not None)
    }


def filter_imported_metadata_rows(rows: list[dict], imported_record_ids: set[str]) -> list[dict]:
    return [
        row for row in rows
        if str(row.get("record_id") or "") in imported_record_ids
    ]
