import logging
import csv
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QInputDialog, QMessageBox

from unshuffle.bridge.persistence_bridge import PersistenceBridge
from unshuffle.core import PlanRecord, parse_tags, plan_records_from_staging_rows
from unshuffle.persistence.exports import export_staging_plan_csv

from ..utils.constants import StagingColumn, STAGING_HEADERS

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
        try:
            from unshuffle.persistence import get_local_db, get_db
            target_path = Path(target_path)
            local_db = get_local_db(target_path)
            global_db = get_db(target_path)
            
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
            for db in (local_db, global_db):
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
            "or the .unshuffle database file.\n\n"
            "The original source folders still need to be available when importing."
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
        file_count = session.get("file_count")
        parts = [session_id]
        if timestamp:
            parts.append(timestamp)
        if file_count is not None:
            parts.append(f"{file_count} files")
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

    def export_session_to_folder(self, folder_path, parent_widget=None) -> bool:
        """Exports the active staging session and metadata to a target folder."""
        if not self.bridge or not self.bridge.has_session():
            QMessageBox.warning(parent_widget or self.app, "Export Session", "No active staging session is loaded.")
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
            "If they are not mounted or present on the restoring system, files from them will be skipped.\n\n"
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

        try:
            from unshuffle.persistence import UnshuffleDB
            from unshuffle.core.paths import get_local_system_dir

            export_path = Path(folder_path)
            if export_path.suffix == ".unshuffle" or export_path.is_file():
                local_db_path = export_path
                local_db_path.parent.mkdir(parents=True, exist_ok=True)
            else:
                local_system_dir = get_local_system_dir(export_path)
                local_system_dir.mkdir(parents=True, exist_ok=True)
                local_db_path = local_system_dir / "unshuffle.db"
            
            local_db = UnshuffleDB(local_db_path)
            
            # 1. Clear any pre-existing staging in local DB for this session
            local_db.clear_staging(session_id)
            local_db.delete_session(session_id)

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

            # 3. Copy Session Sources
            local_db.set_session_sources(session_id, [Path(s) for s in sources if s])

            # 4. Copy Staging Records
            staging = global_db.get_staging_records(session_id)
            records_to_insert = []
            for r in staging:
                records_to_insert.append((
                    r.get("row_id"),
                    r.get("source_path"),
                    r.get("sample_name"),
                    r.get("pack"),
                    r.get("category"),
                    r.get("subcategory"),
                    r.get("audio_type"),
                    r.get("tags"),
                    r.get("confidence"),
                    r.get("duration"),
                    r.get("hash"),
                    r.get("pack_candidates"),
                    r.get("feature_vector", r.get("acoustic_vector")),
                    r.get("feature_space_version"),
                    r.get("feature_schema_json"),
                    r.get("analysis_status"),
                    r.get("analysis_tags_json"),
                    r.get("preserved_root"),
                    r.get("is_preserved"),
                ))
            if records_to_insert:
                local_db.add_staging_records_bulk(session_id, records_to_insert)

            # 5. Copy Coherence Results
            try:
                results = global_db.list_coherence_results(session_id)
                if results:
                    local_db.upsert_coherence_results(session_id, results)
            except Exception:
                pass

            # 6. Copy Refinement Candidates
            try:
                refinements = global_db.list_refinement_candidates(session_id)
                if refinements:
                    local_db.upsert_refinement_candidates(session_id, refinements)
            except Exception:
                pass

            # 7. Copy Anchor Candidates
            try:
                anchors = global_db.list_anchor_candidates(session_id)
                if anchors:
                    if hasattr(local_db, "upsert_anchor_profile_rows"):
                        local_db.upsert_anchor_profile_rows(session_id, anchors)
                    else:
                        local_db.upsert_anchor_candidates(session_id, anchors)
            except Exception:
                pass

            self._show_session_export_success(local_db_path, sources, parent_widget=parent_widget)
            return True
        except Exception as e:
            logging.exception("Failed to export staging session")
            QMessageBox.critical(parent_widget or self.app, "Export Error", f"Failed to export staging session:\n{e}")
            return False

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
            
            # Load staging records
            staging_records = local_db.get_staging_records(session_id)
            if not staging_records:
                QMessageBox.warning(parent_widget or self.app, "Import Session", "No staging records found in the sidecar database.")
                return False

            # Filter staging records by physical presence of their source files
            records_to_load = []
            skipped_count = 0
            for r in staging_records:
                source_path = r.get("source_path")
                if not source_path or not str(source_path).strip():
                    continue 
                
                # Filter out any hidden system lock/db files inside system metadata directories
                normalized_path_str = str(source_path).replace("\\", "/")
                if "/.unshuffle/" in normalized_path_str or "/do_not_delete_unshuffle/" in normalized_path_str.lower():
                    continue
                
                path = Path(source_path)
                if path.exists():
                    records_to_load.append(r)
                else:
                    logging.warning(f"Session import skipped missing file: {source_path}")
                    skipped_count += 1

            if not records_to_load:
                QMessageBox.warning(
                    parent_widget or self.app,
                    "Import Session",
                    f"All {len(staging_records)} files in this session are unmounted or missing on this system. Cannot import."
                )
                return False

            if skipped_count:
                QMessageBox.information(
                    parent_widget or self.app,
                    "Import Session",
                    f"Importing session: {skipped_count} out of {len(staging_records)} files are missing on this system and were skipped."
                )

            # Wires/restores session into computer's global database
            global_db = self.bridge._get_db() if self.bridge else None
            if not global_db:
                global_db = self.engine.db if self.engine else None
            
            target_root = sess.get("target_root")
            if not global_db and target_root:
                try:
                    from unshuffle.bridge.workflow_bridge import create_workflow_bridge
                    engine = create_workflow_bridge(Path(str(target_root)), session_id=session_id)
                    if self.app:
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
                source=Path(sess.get("source_path") or "."),
                target=Path(sess.get("target_root") or "."),
                mode=sess.get("mode") or "pending",
                is_flat=bool(sess.get("is_flat")),
            )

            # 2. Register Session Sources
            sources = local_db.get_session_sources(session_id)
            global_db.set_session_sources(session_id, [Path(s) for s in sources if s])

            # 3. Add Staging Records
            records_to_insert = []
            for r in records_to_load:
                records_to_insert.append((
                    r.get("row_id"),
                    r.get("source_path"),
                    r.get("sample_name"),
                    r.get("pack"),
                    r.get("category"),
                    r.get("subcategory"),
                    r.get("audio_type"),
                    r.get("tags"),
                    r.get("confidence"),
                    r.get("duration"),
                    r.get("hash"),
                    r.get("pack_candidates"),
                    r.get("feature_vector", r.get("acoustic_vector")),
                    r.get("feature_space_version"),
                    r.get("feature_schema_json"),
                    r.get("analysis_status"),
                    r.get("analysis_tags_json"),
                    r.get("preserved_root"),
                    r.get("is_preserved"),
                ))
            global_db.add_staging_records_bulk(session_id, records_to_insert)
            imported_record_ids = imported_staging_record_ids(records_to_load)

            # 4. Copy Coherence Results
            try:
                results = local_db.list_coherence_results(session_id)
                results_filtered = filter_imported_metadata_rows(results, imported_record_ids)
                if results_filtered:
                    global_db.upsert_coherence_results(session_id, results_filtered)
            except Exception:
                logging.exception("Failed to import coherence results metadata.")

            # 5. Copy Refinement Candidates
            try:
                refinements = local_db.list_refinement_candidates(session_id)
                refinements_filtered = filter_imported_metadata_rows(refinements, imported_record_ids)
                if refinements_filtered:
                    global_db.upsert_refinement_candidates(session_id, refinements_filtered)
            except Exception:
                logging.exception("Failed to import refinement candidate metadata.")

            # 6. Copy Anchor Candidates
            try:
                anchors = local_db.list_anchor_candidates(session_id)
                if anchors:
                    if hasattr(global_db, "upsert_anchor_profile_rows"):
                        global_db.upsert_anchor_profile_rows(session_id, anchors)
                    else:
                        global_db.upsert_anchor_candidates(session_id, anchors)
            except Exception:
                pass

            # Update engine session ID
            if self.engine:
                if hasattr(self.engine, "update_state"):
                    self.engine.update_state(session_id=session_id)
                else:
                    self.engine.session_id = session_id
                self.engine.session_source_roots = [Path(s) for s in sources if Path(s).exists()]
                if sources:
                    self.engine.session_source_root = Path(sources[0])

            # Clear active drafts
            drafting = getattr(self.app, "drafting_controller", None)
            if drafting is not None:
                drafting.clear()

            # Reconstruct PlanRecord elements and feed to the workbench
            plan = self.reconstruct_plan_records(records_to_load)
            self.app.workflow_controller.handle_scan_finished(plan, False, None)
            self.app.footer.log(f"<b>Staging Session:</b> imported {len(plan)} records successfully.")
            return True
        except Exception as e:
            logging.exception("Failed to import staging session")
            QMessageBox.critical(parent_widget or self.app, "Import Error", f"Failed to import staging session:\n{e}")
            return False
        finally:
            if cursor_set:
                QApplication.restoreOverrideCursor()


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
