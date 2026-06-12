from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QFileDialog, QMessageBox

from gui.core.system_io import anchor_profile_from_payload, read_additions_csv, write_additions_csv
from gui.core import (
    system_additions,
    system_anchor_actions,
    system_anchor_drafts,
    system_anchor_io,
    system_anchor_rows,
    system_taxonomy,
)
from gui.main.actions.history import reset_learning
from gui.main.actions.library import handle_refresh_all
from unshuffle.bridge import DiscoveryBridge
from unshuffle.logic.coherence.models import ANCHOR_VERIFIED, AnchorProfile


class SystemController:
    """Orchestrates the System workspace without changing classifier behavior."""

    def __init__(self, app, page):
        self.app = app
        self.page = page
        self.discovery_bridge = DiscoveryBridge()
        self._anchor_draft_actions: list[tuple[str, object]] = []
        self._anchor_draft_counts: Counter[str] = Counter()
        self._anchor_action_drafts: dict[str, str] = {}
        self._anchor_update_drafts: dict[str, AnchorProfile] = {}
        self._connect_page_signals()

    def _connect_page_signals(self) -> None:
        self.page.lookupRequested.connect(self.lookup_alias)
        self.page.addAliasesRequested.connect(self.add_aliases)
        self.page.refreshDiscoveryRequested.connect(self.refresh_discovery)
        self.page.gapAddRequested.connect(self.begin_gap_add)
        self.page.refreshAdditionsRequested.connect(self.refresh_additions)
        self.page.removeAdditionsRequested.connect(self.remove_additions)
        self.page.importAdditionsRequested.connect(self.import_additions_csv)
        self.page.exportAdditionsRequested.connect(self.export_additions_csv)
        self.page.refreshCorrectionsRequested.connect(self.refresh_corrections)
        self.page.removeCorrectionsRequested.connect(self.remove_corrections)
        self.page.resetCorrectionsRequested.connect(self.reset_corrections)
        self.page.removeVerifiedAnchorsRequested.connect(self.remove_verified_anchors)
        self.page.importAnchorsRequested.connect(self.import_anchors)
        self.page.promoteAnchorsRequested.connect(self.promote_anchors)
        self.page.ignoreAnchorsRequested.connect(self.ignore_anchors)
        self.page.anchorCandidateActionChanged.connect(self.update_anchor_candidate_action)
        self.page.saveAnchorCandidateDraftRequested.connect(self.save_anchor_candidate_draft)
        self.page.discardAnchorCandidateDraftRequested.connect(self.discard_anchor_candidate_draft)
        self.page.exportAnchorsRequested.connect(self.export_anchors)
        self.page.previewAnchorRequested.connect(self.preview_anchor)
        self.page.anchorSoundGroupChanged.connect(self.update_anchor_sound_group)

    def _bridge(self):
        return getattr(self.app.data_manager, "bridge", None)

    def _engine(self):
        return getattr(self.app, "engine", None)

    def _target_dir(self) -> Path | None:
        engine = self._engine()
        target = getattr(engine, "target_dir", None)
        if target:
            return Path(target)
        raw = str(self.app.settings.value("last_target", "") or "").strip()
        return Path(raw) if raw else None

    def _session_write_enabled(self) -> bool:
        bridge = self._bridge()
        return bool(bridge and bridge.has_session())

    def refresh_capabilities(self) -> None:
        can_write = self._session_write_enabled()
        self.page.set_mode(can_write)
        self.page.set_anchor_draft_state(len(self._anchor_draft_actions))
        self.refresh_additions()
        self.refresh_corrections()
        self.refresh_discovery()
        self.refresh_anchors()

    def open_workspace(self) -> None:
        self.refresh_navigation_state()
        self.app.stack.setCurrentWidget(self.page)
        QTimer.singleShot(0, self.refresh_current_section)

    def refresh_navigation_state(self) -> None:
        can_write = self._session_write_enabled()
        self.page.set_mode(can_write)
        self.page.set_anchor_draft_state(len(self._anchor_draft_actions))

    def refresh_current_section(self) -> None:
        stack = getattr(self.page, "stack", None)
        current = stack.currentWidget() if stack is not None else None
        if current is getattr(self.page, "discovery_panel", None):
            self.refresh_discovery()
        elif current is getattr(self.page, "additions_panel", None):
            self.refresh_additions()
        elif current is getattr(self.page, "corrections_panel", None):
            self.refresh_corrections()
        elif current is getattr(self.page, "anchors_panel", None) or current is getattr(self.page, "my_anchors_panel", None):
            self.refresh_anchors()

    def lookup_alias(self, alias: str, category: str) -> None:
        result = system_taxonomy.alias_lookup(self, alias, category)
        self.page.set_alias_lookup(result.status, result.rows, result.cooccurrences, result.allows_add)
        self.app.set_search_status(result.search_status)

    def add_aliases(self, aliases: list[str], category: str) -> None:
        if not self._session_write_enabled():
            self.app.set_search_status("Taxonomy: Add Alias requires an active session.")
            return
        if not category or category.lower() == "all":
            self.app.set_search_status("Taxonomy: choose a concrete category.")
            return

        normalized = system_additions.normalize_aliases(aliases)
        if not normalized:
            return

        message = (
            f"Add {len(normalized)} alias(es) to {category}?\n\n"
            + "\n".join(f"- {alias}" for alias in normalized[:20])
            + ("\n..." if len(normalized) > 20 else "")
            + "\n\nOnly proceed if these are true taxonomy vocabulary for this category."
        )
        if QMessageBox.warning(self.app, "Confirm Taxonomy Addition", message, QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return

        bridge = self._bridge()
        if bridge is None:
            self.app.set_search_status("Taxonomy: no database available.")
            return
        added = bridge.add_aliases_bulk(normalized, category, source="user")
        self.app.set_search_status(f"Taxonomy: added {added} user alias(es) to {category}.")
        self.refresh_additions()
        self._prompt_rescan("Taxonomy additions were saved.")

    def refresh_discovery(self) -> None:
        model = getattr(self.app, "model", None)
        records = list(getattr(model, "records", []) or [])
        uncategorized = [
            rec for rec in records
            if str(getattr(rec, "category", "") or "").strip() == "Uncategorized"
        ]
        rows = system_taxonomy.discovery_rows(uncategorized)
        gaps = self._probable_gaps(uncategorized)
        self.page.set_discovery_rows(rows, gaps)

    def begin_gap_add(self, token: str) -> None:
        self.page.open_add_alias(token)
        self.app.set_search_status("Taxonomy: choose a category to add this discovered token.")

    def refresh_additions(self) -> None:
        bridge = self._bridge()
        rows = bridge.get_user_additions() if bridge and bridge.has_session() else []
        self.page.set_additions(rows)

    def remove_additions(self, aliases: list[str]) -> None:
        if not aliases:
            return
        if not self._session_write_enabled():
            self.app.set_search_status("Taxonomy: removal requires an active session.")
            return
        message = f"Remove {len(aliases)} user addition(s)?"
        if QMessageBox.warning(self.app, "Remove Additions", message, QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        bridge = self._bridge()
        removed = bridge.remove_aliases_if_source_allowed(aliases, allowed_sources=("user",)) if bridge else 0
        self.app.set_search_status(f"Taxonomy: removed {removed} user addition(s).")
        self.refresh_additions()
        if removed:
            self._prompt_rescan("Taxonomy additions were removed.")

    def import_additions_csv(self) -> None:
        if not self._session_write_enabled():
            self.app.set_search_status("Taxonomy: import requires an active session.")
            return
        path, _ = QFileDialog.getOpenFileName(self.app, "Import Additions CSV", "", "CSV Files (*.csv)")
        if not path:
            return
        rows = self._read_additions_csv(Path(path))
        if not rows:
            QMessageBox.information(self.app, "Import Additions", "No valid alias/category rows found.")
            return

        plan = system_additions.plan_import(self, rows)
        if plan.invalid:
            preview = "\n".join(f"- {alias}: {category}" for alias, category in plan.invalid[:12])
            QMessageBox.warning(self.app, "Import Additions", f"Unknown categories found:\n\n{preview}")
            return

        count = plan.count
        if count <= 0:
            QMessageBox.information(self.app, "Import Additions", "All imported aliases are already covered.")
            return
        summary = "\n".join(f"- {category}: {len(items)} alias(es)" for category, items in sorted(plan.filtered.items()))
        if plan.skipped:
            summary += f"\n\nSkipped as already covered: {len(plan.skipped)}"
        if plan.conflicts:
            preview = "\n".join(
                f"- {alias} resembles {hit_alias} ({hit_category})"
                for alias, hit_alias, hit_category in plan.conflicts[:8]
            )
            summary += f"\n\nPossible meaning conflicts:\n{preview}"
        if QMessageBox.warning(
            self.app,
            "Import Additions",
            f"Import {count} addition(s) as user aliases?\n\n{summary}",
            QMessageBox.Yes | QMessageBox.No,
        ) != QMessageBox.Yes:
            return

        bridge = self._bridge()
        added = 0
        if bridge:
            for category, aliases in plan.filtered.items():
                added += bridge.add_aliases_bulk(aliases, category, source="user")
        self.app.set_search_status(f"Taxonomy: imported {added} user addition(s).")
        self.refresh_additions()
        if added:
            self._prompt_rescan("Taxonomy additions were imported.")

    def export_additions_csv(self) -> None:
        target = self._target_dir()
        default_path = str((target or Path.cwd()) / "unshuffle_user_additions.csv")
        out_path, _ = QFileDialog.getSaveFileName(self.app, "Export My Additions", default_path, "CSV Files (*.csv)")
        if not out_path:
            return
        bridge = self._bridge()
        rows = bridge.get_user_additions() if bridge and bridge.has_session() else []
        write_additions_csv(Path(out_path), rows)
        self.app.set_search_status(f"Taxonomy: exported {len(rows)} user addition(s).")

    def refresh_corrections(self) -> None:
        bridge = self._bridge()
        rows = bridge.list_token_adjustments() if bridge and bridge.has_session() else []
        self.page.set_corrections(system_additions.corrections_for_display(self, rows))

    def refresh_anchor_candidates(self) -> None:
        self.refresh_anchors()

    def refresh_anchors(self) -> None:
        candidate_rows, verified_rows = system_anchor_rows.rows_for_controller(self)
        self.page.set_anchor_candidates(candidate_rows)
        self.page.set_anchor_candidate_actions(self._anchor_action_drafts)
        self.page.set_my_anchors(verified_rows)

    def _add_anchor_consistency(self, rows: list[dict]) -> list[dict]:
        return system_anchor_rows.add_anchor_consistency(rows)

    @staticmethod
    def _anchor_neighbors(row: dict) -> int:
        return system_anchor_rows.anchor_neighbors(row)

    def _enrich_anchor_candidate_rows(self, engine, rows: list[dict]) -> list[dict]:
        return system_anchor_rows.enrich_anchor_candidate_rows(engine, rows)

    def preview_anchor(self, source_path: str) -> None:
        audio_controller = getattr(self.app, "audio_controller", None)
        if not source_path or audio_controller is None:
            return
        audio_controller.play_path(Path(source_path))

    def update_anchor_sound_group(self, anchor_id: str, audio_type: str, category: str, subcategory: str) -> None:
        engine = self._engine()
        if not engine or not getattr(engine, "db", None):
            return
        anchor_id = (anchor_id or "")
        category = (category or "").strip()
        if not anchor_id or not category:
            return
        rows = engine.db.list_anchor_candidates(engine.session_id)
        row = next((candidate for candidate in rows if str(candidate.get("anchor_id") or "") == anchor_id), None)
        if row is None:
            return
        draft = system_anchor_actions.build_sound_group_draft(
            row,
            audio_type,
            category,
            subcategory,
            self._anchor_profile_from_payload,
        )
        if draft.status or draft.anchor is None:
            self.app.set_search_status(draft.status or "Taxonomy: anchor payload is invalid.")
            self.refresh_anchors()
            return
        self._anchor_update_drafts[anchor_id] = draft.anchor
        self._anchor_action_drafts[anchor_id] = "update"
        self.page.set_anchor_candidate_actions(self._anchor_action_drafts)
        self.page.set_anchor_draft_state(self._anchor_draft_count())
        self.page.set_anchor_status(self._anchor_draft_status_message(), "success")
        self.app.set_search_status(f"Taxonomy: staged anchor sound group as {audio_type}/{category}/{subcategory}. Save to keep it, or Save and Apply to use it now.")

    def _queue_anchor_draft_action(self, label: str, callback, *, kind: str = "action", count: int = 1) -> None:
        self._anchor_draft_actions.append(((label or "Anchor action"), callback))
        self._anchor_draft_counts[(kind or "action")] += max(1, (count or 1))
        self.page.set_anchor_draft_state(self._anchor_draft_count())
        self.page.set_anchor_status(self._anchor_draft_status_message(), "info")

    def _anchor_draft_count(self) -> int:
        return system_anchor_drafts.draft_count(self)

    def _anchor_draft_status_message(self) -> str:
        return system_anchor_drafts.draft_status_message(self)

    def save_anchor_candidate_draft(self, apply_now: bool = False) -> None:
        if not self._anchor_draft_count():
            self.page.set_anchor_status("No anchor candidate draft changes to save.", "info")
            return
        count = self._anchor_draft_count()
        action = "Save and apply" if apply_now else "Save"
        if QMessageBox.question(
            self.app,
            "Save Anchor Candidate Draft",
            f"{action} {count} anchor candidate draft action{'s' if count != 1 else ''}?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        draft_snapshot = system_anchor_drafts.snapshot(self)
        actions = draft_snapshot.actions
        anchor_actions = draft_snapshot.action_drafts
        anchor_updates = draft_snapshot.update_drafts
        try:
            for _label, callback in actions:
                if callable(callback):
                    callback()
            for anchor_id, action in anchor_actions.items():
                if action == "update" and anchor_id in anchor_updates:
                    self._upsert_anchor_profile(anchor_updates[anchor_id])
            promote_ids = [anchor_id for anchor_id, action in anchor_actions.items() if action == "promotion"]
            ignore_ids = [anchor_id for anchor_id, action in anchor_actions.items() if action == "ignore"]
            if promote_ids:
                engine = self._engine()
                if engine and getattr(engine, "db", None) and hasattr(engine.db, "repair_anchor_profile_json"):
                    from unshuffle.logic.coherence.anchor_profiles import build_anchor_payload
                    failed = engine.db.repair_anchor_profile_json(engine.session_id, promote_ids, build_anchor_payload)
                    if failed:
                        QMessageBox.warning(
                            self.app,
                            "Anchor Promotion Warning",
                            f"{len(failed)} anchor(s) could not be promoted because their profile data is incomplete "
                            f"and could not be reconstructed:\n\n"
                            + "\n".join(f"- {aid}" for aid in failed[:10])
                            + ("\n..." if len(failed) > 10 else ""),
                        )
                        promote_ids = [aid for aid in promote_ids if aid not in set(failed)]
                self._set_anchor_state(promote_ids, "verified")
            if ignore_ids:
                self._set_anchor_state(ignore_ids, "ignored")
        except Exception as exc:
            system_anchor_drafts.restore(self, draft_snapshot)
            self.page.set_anchor_candidate_actions(self._anchor_action_drafts)
            self.page.set_anchor_draft_state(self._anchor_draft_count())
            self.page.set_anchor_status(f"Could not save anchor candidate draft: {exc}", "error")
            self.app.set_search_status("Taxonomy: anchor candidate draft was not saved.")
            return
        system_anchor_drafts.clear_drafts(self)
        self.page.set_anchor_candidate_actions({})
        self.page.set_anchor_draft_state(0)
        self.refresh_anchors()
        self.page.set_anchor_status(f"Saved {count} anchor candidate draft action{'s' if count != 1 else ''}.", "success")
        self.app.set_search_status(f"Taxonomy: saved {count} anchor candidate draft action{'s' if count != 1 else ''}.")
        if apply_now:
            self.prompt_anchor_apply_rescan()

    def discard_anchor_candidate_draft(self) -> None:
        if not self._anchor_draft_count():
            self.page.set_anchor_status("No anchor candidate draft changes to discard.", "info")
            return
        count = self._anchor_draft_count()
        if QMessageBox.question(
            self.app,
            "Discard Anchor Candidate Draft",
            f"Discard {count} pending anchor candidate draft action{'s' if count != 1 else ''}?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        system_anchor_drafts.clear_drafts(self)
        self.page.set_anchor_candidate_actions({})
        self.page.set_anchor_draft_state(0)
        self.refresh_anchors()
        self.page.set_anchor_status("Anchor candidate draft discarded.", "info")
        self.app.set_search_status("Taxonomy: discarded anchor candidate draft.")

    def _upsert_anchor_profile(self, anchor: AnchorProfile) -> None:
        engine = self._engine()
        if not engine or not getattr(engine, "db", None):
            return
        if hasattr(engine.db, "upsert_anchor_profiles"):
            engine.db.upsert_anchor_profiles(engine.session_id, [anchor])
        else:
            engine.db.upsert_anchor_candidates(engine.session_id, [anchor])
        self.refresh_anchors()

    def update_anchor_candidate_action(self, anchor_id: str, action: str) -> None:
        anchor_id = (anchor_id or "")
        result = system_anchor_actions.update_candidate_action_draft(
            self._anchor_action_drafts,
            self._anchor_update_drafts,
            anchor_id,
            action,
        )
        if not result.handled:
            return
        if result.needs_sound_group:
            self.page.set_anchor_candidate_actions(self._anchor_action_drafts)
            self.page.set_anchor_status("Change the Sound group first to stage an update.", "info")
            return
        if result.had_update and (action or "") != "update":
            self.refresh_anchors()
        self.page.set_anchor_candidate_actions(self._anchor_action_drafts)
        self.page.set_anchor_draft_state(self._anchor_draft_count())
        self.page.set_anchor_status(self._anchor_draft_status_message(), "success" if self._anchor_draft_count() else "info")
        self.app.set_search_status(f"Taxonomy: {self._anchor_draft_status_message()}.")

    def promote_anchors(self, anchor_ids: list[str]) -> None:
        if not anchor_ids:
            self.page.set_anchor_status("Select one or more anchor candidates to promote.", "warn")
            self.app.set_search_status("Taxonomy: select anchor candidates before promoting.")
            return
        engine = self._engine()
        if not engine or not getattr(engine, "db", None):
            self.page.set_anchor_status("Promote requires an active library session.", "warn")
            return
        count = len(anchor_ids)
        prompt = system_anchor_actions.candidate_action_prompt("promotion", count)
        if QMessageBox.question(
            self.app,
            prompt.title,
            prompt.message,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        ) != QMessageBox.Yes:
            self.page.set_anchor_status(prompt.cancel_status, "info")
            return
        system_anchor_actions.stage_candidate_actions(self._anchor_action_drafts, anchor_ids, "promotion")
        self.page.set_anchor_candidate_actions(self._anchor_action_drafts)
        self.page.set_anchor_draft_state(self._anchor_draft_count())
        status = self._anchor_draft_status_message()
        self.page.set_anchor_status(status, "success")
        self.app.set_search_status(f"Taxonomy: {status}")

    def remove_verified_anchors(self, anchor_ids: list[str]) -> None:
        if not anchor_ids:
            self.app.set_search_status("Taxonomy: select verified anchors before removing.")
            return
        engine = self._engine()
        if not engine or not getattr(engine, "db", None):
            return
        count = len(anchor_ids)
        _item_word, prompt, status = system_anchor_actions.verified_removal_message(count)
        if QMessageBox.question(
            self.app,
            "Remove Verified Anchors",
            prompt,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        if hasattr(engine.db, "remove_verified_anchor_profiles"):
            engine.db.remove_verified_anchor_profiles(engine.session_id, anchor_ids)
        else:
            engine.db.set_anchor_candidate_state(engine.session_id, anchor_ids, "ignored")
        self.refresh_anchors()
        self.app.set_search_status(status)

    def ignore_anchors(self, anchor_ids: list[str]) -> None:
        if not anchor_ids:
            self.page.set_anchor_status("Select one or more anchor candidates to ignore.", "warn")
            self.app.set_search_status("Taxonomy: select anchor candidates before ignoring.")
            return
        engine = self._engine()
        if not engine or not getattr(engine, "db", None):
            self.page.set_anchor_status("Ignore requires an active library session.", "warn")
            return
        count = len(anchor_ids)
        prompt = system_anchor_actions.candidate_action_prompt("ignore", count)
        if QMessageBox.question(
            self.app,
            prompt.title,
            prompt.message,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        ) != QMessageBox.Yes:
            self.page.set_anchor_status(prompt.cancel_status, "info")
            return
        system_anchor_actions.stage_candidate_actions(self._anchor_action_drafts, anchor_ids, "ignore")
        self.page.set_anchor_candidate_actions(self._anchor_action_drafts)
        self.page.set_anchor_draft_state(self._anchor_draft_count())
        status = self._anchor_draft_status_message()
        self.page.set_anchor_status(status, "success")
        self.app.set_search_status(f"Taxonomy: {status}")

    def _set_anchor_state(self, anchor_ids: list[str], state: str) -> None:
        engine = self._engine()
        if not engine or not getattr(engine, "db", None):
            return
        engine.db.set_anchor_candidate_state(engine.session_id, anchor_ids, state)
        self.refresh_anchors()

    def export_anchors(self, anchor_ids: list[str]) -> None:
        engine = self._engine()
        if not engine or not getattr(engine, "db", None):
            return
        all_rows = engine.db.list_anchor_candidates(engine.session_id, state=ANCHOR_VERIFIED)
        selected = system_anchor_actions.selected_anchor_rows(all_rows, anchor_ids)
        if not selected:
            self.app.set_search_status("Taxonomy: no verified anchors to export.")
            return
        default_path = str((self._target_dir() or Path.cwd()) / "unshuffle_verified_anchors.json")
        out_path, _ = QFileDialog.getSaveFileName(self.app, "Export Verified Anchors", default_path, "JSON Files (*.json)")
        if not out_path:
            return

        payloads = system_anchor_io.exportable_anchor_payloads(selected)
        Path(out_path).write_text(json.dumps(payloads, indent=2), encoding="utf-8")
        self.app.set_search_status(f"Taxonomy: exported {len(payloads)} verified anchor profile(s).")

    def import_anchors(self) -> None:
        engine = self._engine()
        if not engine or not getattr(engine, "db", None):
            self.app.set_search_status("Taxonomy: import anchors requires an active library session.")
            return
        path, _ = QFileDialog.getOpenFileName(self.app, "Import Verified Anchors", "", "JSON Files (*.json)")
        if not path:
            return
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            QMessageBox.warning(self.app, "Import Verified Anchors", f"Could not read anchor JSON:\n\n{exc}")
            return

        anchors, rejected, parsed = system_anchor_io.imported_anchor_profiles(raw)
        if not parsed:
            QMessageBox.warning(self.app, "Import Verified Anchors", "Anchor import expects a JSON list of anchor profiles.")
            return

        if not anchors:
            preview = "\n".join(f"- #{idx}: {reason}" for idx, reason in rejected[:8])
            QMessageBox.warning(self.app, "Import Verified Anchors", f"No valid anchor profiles found.\n\n{preview}")
            return

        summary = f"Import {len(anchors)} verified anchor profile(s)?"
        if rejected:
            summary += f"\n\nSkipped invalid profile(s): {len(rejected)}"
            summary += "\n" + "\n".join(f"- #{idx}: {reason}" for idx, reason in rejected[:8])
        if QMessageBox.warning(
            self.app,
            "Import Verified Anchors",
            summary,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        ) != QMessageBox.Yes:
            return

        if hasattr(engine.db, "upsert_anchor_profiles"):
            engine.db.upsert_anchor_profiles(engine.session_id, anchors)
        else:
            engine.db.upsert_anchor_candidates(engine.session_id, anchors)
        self.refresh_anchors()
        self.app.set_search_status(f"Taxonomy: imported {len(anchors)} verified anchor profile(s). Run coherence to use them.")

    @staticmethod
    def _anchor_profile_from_payload(payload: dict, state: str = ANCHOR_VERIFIED) -> AnchorProfile | None:
        return anchor_profile_from_payload(payload, state)

    def remove_corrections(self, keys: list[tuple[str, str]]) -> None:
        if not keys:
            return
        if not self._session_write_enabled():
            self.app.set_search_status("Taxonomy: correction removal requires an active session.")
            return
        message = f"Remove {len(keys)} learned correction row(s)?"
        if QMessageBox.warning(self.app, "Remove Learned Corrections", message, QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        bridge = self._bridge()
        removed = bridge.remove_token_adjustments(keys) if bridge else 0
        self.app.set_search_status(f"Taxonomy: removed {removed} learned correction row(s).")
        self.refresh_corrections()
        if removed:
            self._prompt_rescan("Learned corrections were removed.")

    def reset_corrections(self) -> None:
        reset_learning(self.app)
        self.refresh_corrections()

    def run_rescan(self) -> None:
        handle_refresh_all(self.app)

    def prompt_anchor_apply_rescan(self) -> None:
        self._prompt_rescan("Anchor candidate changes were applied.")

    def reset_weights(self) -> None:
        self.reset_corrections()

    def refresh_conflicts(self) -> None:
        self.app.set_search_status("Taxonomy: conflicts are shown during Add Alias lookup.")

    def sync_apply(self) -> None:
        self.app.set_search_status("Taxonomy: sync/apply is internal for this workflow.")

    def run_dry_run(self) -> None:
        self.app.set_search_status("Taxonomy: discovery uses the current staged table.")

    def _candidate_token(self, alias: str) -> str:
        return system_taxonomy.candidate_token(alias)

    def _alias_map(self) -> dict[str, tuple[str, float, str]]:
        return system_taxonomy.alias_map(self)

    def _aliases_containing_token(
        self,
        alias_map: dict[str, tuple[str, float, str]],
        token: str,
        category: str | None,
    ) -> list[tuple[str, str, str]]:
        return system_taxonomy.aliases_containing_token(alias_map, token, category)

    def _cooccurrences_for_token(
        self,
        token: str,
        alias_map: dict[str, tuple[str, float, str]],
    ) -> list[tuple[str, int]]:
        return system_taxonomy.cooccurrences_for_token(self, token, alias_map)

    def _probable_gaps(self, records) -> list[tuple[str, int, str]]:
        return system_taxonomy.probable_gaps(self, records)

    def _read_additions_csv(self, path: Path) -> list[tuple[str, str]]:
        return read_additions_csv(path)

    def _prompt_rescan(self, reason: str) -> None:
        reply = QMessageBox.question(
            self.app,
            "Rescan Recommended",
            f"{reason}\n\nRescan active sources now so staged classifications reflect this change?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self.run_rescan()
