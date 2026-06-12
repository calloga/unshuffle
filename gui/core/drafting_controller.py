import logging
import re
import uuid
from dataclasses import replace
from PySide6.QtGui import QUndoCommand
from PySide6.QtCore import QObject, Signal, QTimer
from PySide6.QtWidgets import QMessageBox, QWidget
from unshuffle.core import stable_record_identity
from gui.models.library_tree_resolution import SEMANTIC_OVERRIDE_ATTR, exact_destination_fields_from_filter
from unshuffle.logic.tree_organization import TreeOrganizationNode, TreeOrganizationProfile
from unshuffle.logic.tree_organization.models import utc_now_iso
from ..utils.state import finalize_model_mutation

class DraftingController(QObject):
    """
    Manages staging/drafting logic for reorganizations.
    """
    draftChanged = Signal()
    impactCalculated = Signal(dict)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        from . import ReorgManager
        self.reorg_manager = ReorgManager()
        self.app = parent 
        self._branch_paths = set()
        self._partial_refresh = False
        self._draft_profile_original: TreeOrganizationProfile | None = None
        self._draft_profile_active = False
        
        from PySide6.QtCore import QTimer
        self._draft_tree_refresh_timer = QTimer(self.app)
        self._draft_tree_refresh_timer.setSingleShot(True)
        self._draft_tree_refresh_timer.timeout.connect(lambda: self.app.view_controller.update_library_views(tree_delay_ms=0))
        
        self._reorg_impact_timer = QTimer(self.app)
        self._reorg_impact_timer.setSingleShot(True)
        self._reorg_impact_timer.timeout.connect(self.run_reorg_impact_analysis)
        self._impact_pending_notice_shown = False
        self._impact_request_id = 0
        self._impact_worker = None
        self._stale_impact_workers = set()
        self._saving_reorg_draft = False

    def _parent_widget(self) -> QWidget | None:
        parent = self.app
        return parent if isinstance(parent, QWidget) else None

    def schedule_reorg_impact_analysis(self):
        if not self._impact_pending_notice_shown:
            self.app.footer.log("<b>Draft impact pending...</b>")
            self._impact_pending_notice_shown = True
        self.app.footer.set_status("Draft impact pending...")
        self._reorg_impact_timer.start(900)

    def run_reorg_impact_analysis(self):
        if not self.reorg_manager.has_changes():
            return

        from .workers import DraftImpactWorker

        self._impact_request_id += 1
        request_id = self._impact_request_id
        originals_snapshot = [
            (rec_id, col_idx)
            for (rec_id, _col), (_rec, col_idx, _old_val) in self.reorg_manager.originals.items()
        ]
        worker = DraftImpactWorker(request_id, originals_snapshot, self.reorg_manager.conflicts)
        self._impact_worker = worker

        def _on_finished(payload):
            self._stale_impact_workers.discard(worker)
            if self._impact_worker is worker:
                self._impact_worker = None
            if payload.get("request_id") != self._impact_request_id:
                return
            summary_text = str(payload.get("summary", "")).strip()
            if summary_text:
                self.app.footer.log(f"<b>Draft impact:</b> {summary_text}")
            self.app.footer.set_status("Draft impact analysis updated.")
            self._impact_pending_notice_shown = False

        def _on_error(_message):
            self._stale_impact_workers.discard(worker)
            if self._impact_worker is worker:
                self._impact_worker = None
            self._impact_pending_notice_shown = False

        worker.finished.connect(_on_finished)
        worker.finished.connect(worker.deleteLater)
        worker.error.connect(_on_error)
        worker.error.connect(worker.deleteLater)
        worker.start()

    def save_reorg_draft(self):
        from ..utils import state as state_helpers

        if self._saving_reorg_draft or not self.app.model or not self.has_changes():
            return

        summary_text, detail_rows = self.build_save_summary(self.app.model)
        if not self._show_save_confirm_dialog(summary_text, detail_rows):
            return

        from ..utils import ui_helpers
        self._saving_reorg_draft = True
        learned_count = 0
        ui_helpers.set_ui_busy(self.app, True)
        try:
            self._invalidate_pending_impact_analysis()
            self.app.footer.log("<b>Saving draft reorganization...</b>")
            learned_count = self._learn_category_corrections_from_draft()
            state_helpers.rewrite_staging_from_model(self.app)
            self._save_draft_profile_if_needed()
            self.reorg_manager.clear()
            self._draft_profile_original = None
            self._draft_profile_active = False
            self._branch_paths.clear()
            self._partial_refresh = False
            self.draftChanged.emit()
            self.app.footer.set_reorg_draft_state("", False)
            self.app.footer.log("<b>Draft:</b> saved to canon.")
        finally:
            ui_helpers.set_ui_busy(self.app, False)
            self._saving_reorg_draft = False
        QTimer.singleShot(0, lambda: self._refresh_after_saved_draft(learned_count))

    def _invalidate_pending_impact_analysis(self) -> None:
        self._reorg_impact_timer.stop()
        self._impact_request_id += 1
        if self._impact_worker is not None:
            self._stale_impact_workers.add(self._impact_worker)
        self._impact_worker = None
        self._impact_pending_notice_shown = False

    def _refresh_after_saved_draft(self, learned_count: int = 0) -> None:
        if getattr(self.app, "search_controller", None):
            if getattr(self.app.search_controller, "current_query", ""):
                self.app.search_controller.execute_search()
            elif getattr(self.app, "view_controller", None):
                self.app.view_controller.update_library_views(tree_delay_ms=0)
        elif getattr(self.app, "view_controller", None):
            self.app.view_controller.update_library_views(tree_delay_ms=0)
        if learned_count and getattr(self.app, "system_controller", None):
            self.app.system_controller.refresh_corrections()

    def _learn_category_corrections_from_draft(self) -> int:
        from unshuffle.runtime.execution_learning import user_category_learning_events_for
        from ..utils.constants import StagingColumn

        if not self.app.model or not self.reorg_manager.has_changes():
            return 0
        events = set()
        for (_rec_id, _col), (rec, col_idx, old_val) in self.reorg_manager.originals.items():
            if col_idx != StagingColumn.CATEGORY:
                continue
            if hasattr(self.reorg_manager, "should_learn") and not self.reorg_manager.should_learn(rec, col_idx):
                continue
            old_category = str(old_val or "").strip()
            new_category = str(getattr(rec, "category", "") or "").strip()
            events.update(user_category_learning_events_for(rec, old_category, new_category))
        if not events:
            return 0
        bridge = getattr(getattr(self.app, "data_manager", None), "bridge", None)
        if bridge is None:
            return 0
        update_events = getattr(bridge, "update_token_adjustments_from_events", None)
        if callable(update_events):
            updated_count = update_events(list(events))
            if isinstance(updated_count, (str, int, float)):
                return int(updated_count)
        return 0

    def discard_reorg_draft(self, *, confirm: bool = True):
        from PySide6.QtWidgets import QMessageBox
        
        if not self.has_changes():
            return
            
        if confirm and QMessageBox.question(self._parent_widget(), "Discard Draft", "Discard all pending draft changes?") != QMessageBox.Yes:
            return
            
        if self.app.model:
            revert_updates = self.reorg_manager.get_revert_list()
            self.app.model._apply_bulk_values(revert_updates)
            for rec, _col, _old_val in revert_updates:
                if hasattr(rec, SEMANTIC_OVERRIDE_ATTR):
                    delattr(rec, SEMANTIC_OVERRIDE_ATTR)

        self._restore_draft_profile_if_needed()

        self.reorg_manager.clear()
        self._reorg_impact_timer.stop()
        self._impact_pending_notice_shown = False
        self._branch_paths.clear()
        self.draftChanged.emit()
        self._partial_refresh = False
        self.app.footer.set_reorg_draft_state("", False)
        self.app.view_controller.update_library_views(tree_delay_ms=0)
        self.app.footer.log("<b>Draft:</b> discarded.")
        self.app.footer.toggle_footer(False)

    def apply_bulk_tags(self, records, add_tags, remove_tags):
        from unshuffle.core import normalize_tags
        from ..utils.constants import StagingColumn
        
        add_tags = normalize_tags(add_tags or [])
        remove_set = {tag.lower() for tag in normalize_tags(remove_tags or [])}
        updates = []
        for rec in records:
            existing = normalize_tags(getattr(rec, "tags", []) or [])
            merged = [tag for tag in existing if tag.lower() not in remove_set]
            for tag in add_tags:
                if tag.lower() not in {item.lower() for item in merged}:
                    merged.append(tag)
            updates.append((rec, StagingColumn.TAGS, merged))
        
        return self._apply_draft_updates(updates)

    def build_save_summary(self, model, include_details=True):
        if not model or not self.reorg_manager.has_changes():
            if self._draft_profile_active:
                return "Custom tree placement change.", []
            return "No draft changes to save.", []

        from collections import Counter
        from ..utils.constants import StagingColumn

        rec_map = {}
        changed_keys_by_rec = {}
        field_counter = Counter()
        field_key_by_column = {
            StagingColumn.TYPE: "type",
            StagingColumn.CATEGORY: "category",
            StagingColumn.PACK: "pack",
            StagingColumn.SUBCATEGORY: "subcategory",
        }
        for (rec_id, col), (rec, col_idx, old_val) in self.reorg_manager.originals.items():
            rec_map[rec_id] = rec
            field_key = field_key_by_column.get(col_idx)
            if field_key:
                changed_keys_by_rec.setdefault(rec_id, set()).add(field_key)
            if col_idx == StagingColumn.TYPE:
                field_counter["Type"] += 1
            elif col_idx == StagingColumn.CATEGORY:
                field_counter["Category"] += 1
            elif col_idx == StagingColumn.PACK:
                field_counter["Pack"] += 1
            elif col_idx == StagingColumn.SUBCATEGORY:
                field_counter["Subcategory"] += 1

        changed_records = len(rec_map)
        n_fields = sum(field_counter.values())
        breakdown_parts = ", ".join(
            f"{k} ×{v}" for k, v in sorted(field_counter.items()) if v
        )
        summary_parts = [f"{changed_records} file{'s' if changed_records != 1 else ''} updated"]
        if breakdown_parts:
            summary_parts.append(breakdown_parts)
        if self.reorg_manager.conflicts:
            summary_parts.append(f"{self.reorg_manager.conflicts} collision{'s' if self.reorg_manager.conflicts != 1 else ''} flagged")
        if self._draft_profile_active:
            summary_parts.append("tree layout changed")
        summary = "  ·  ".join(summary_parts)

        detail_rows = []
        if include_details:
            for rec in sorted(rec_map.values(), key=lambda r: str(r.source_path).lower())[:250]:
                detail_rows.append({
                    "filename": rec.source_path.name,
                    "type": str(getattr(rec, "audio_type", "") or ""),
                    "category": str(getattr(rec, "category", "") or ""),
                    "subcategory": str(getattr(rec, "subcategory", "") or ""),
                    "pack": str(getattr(rec, "pack", "") or ""),
                    "_changed_keys": changed_keys_by_rec.get(stable_record_identity(rec), set()),
                })
            if len(rec_map) > 250:
                detail_rows.append({"file": f"…and {len(rec_map) - 250} more", "type": "", "category": "", "subcategory": "", "pack": ""})
        return summary, detail_rows

    def _show_save_confirm_dialog(self, summary_text: str, detail_rows: list) -> bool:
        """Show a styled save-confirmation dialog. Returns True if the user confirmed."""
        from PySide6.QtWidgets import (
            QDialog, QVBoxLayout, QHBoxLayout, QLabel,
            QPushButton, QTableWidget, QTableWidgetItem,
            QHeaderView, QAbstractItemView,
        )
        from ..utils.styles import (
            ColorPalette,
            apply_style,
            button_style,
            build_dialog_base_style,
            scaled_px,
            workspace_table_widget_style,
        )

        dialog = QDialog(self._parent_widget())
        dialog.setWindowTitle("Confirm Save Changes")
        dialog.setModal(True)
        dialog.setMinimumSize(scaled_px(720), scaled_px(420))
        themed_tables = []

        def refresh_theme() -> None:
            dialog.setStyleSheet(
                build_dialog_base_style()
                + (
                    f"QLabel#Title {{ color: {ColorPalette.TEXT_LIGHT}; "
                    f"font-size: {scaled_px(13)}px; font-weight: bold; }}"
                    f"QLabel#Summary {{ color: {ColorPalette.TEXT_MUTED}; "
                    f"font-size: {scaled_px(11)}px; }}"
                    f"{button_style('secondary', size='normal', min_width=84)}"
                    f"QPushButton[role=\"primary\"] {{ background: {ColorPalette.PRIMARY}; "
                    f"color: {ColorPalette.TEXT_INVERSE}; }}"
                    f"QPushButton[role=\"primary\"]:hover {{ background: {ColorPalette.PRIMARY_HOVER}; }}"
                )
            )
            for table in themed_tables:
                apply_style(table, workspace_table_widget_style())

        dialog.refresh_theme = refresh_theme
        refresh_theme()

        root = QVBoxLayout(dialog)
        root.setContentsMargins(14, 14, 14, 12)
        root.setSpacing(10)

        title = QLabel("Commit this draft reorganization to canon?")
        title.setObjectName("Title")
        root.addWidget(title)

        summary = QLabel(summary_text)
        summary.setObjectName("Summary")
        root.addWidget(summary)

        if detail_rows:
            visible_keys, visible_cols = self._save_detail_table_columns(detail_rows)

            table = QTableWidget(len(detail_rows), len(visible_cols))
            table.setHorizontalHeaderLabels(visible_cols)
            table.setEditTriggers(QAbstractItemView.NoEditTriggers)
            table.setSelectionMode(QAbstractItemView.NoSelection)
            table.verticalHeader().setVisible(False)
            table.setShowGrid(False)
            table.setAlternatingRowColors(False)
            table.horizontalHeader().setStretchLastSection(False)
            table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
            for col_i in range(1, len(visible_cols)):
                table.horizontalHeader().setSectionResizeMode(col_i, QHeaderView.Stretch)
            table.setWordWrap(False)
            table.setMaximumHeight(scaled_px(260))
            apply_style(table, workspace_table_widget_style())
            themed_tables.append(table)

            for row_i, row in enumerate(detail_rows):
                table.setRowHeight(row_i, 26)
                for col_i, key in enumerate(visible_keys):
                    value = row.get(key, "")
                    if key == "filename" and not value:
                        value = row.get("file", "")
                    item = QTableWidgetItem(value)
                    table.setItem(row_i, col_i, item)

            root.addWidget(table, 1)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        btn_no = QPushButton("Cancel")
        btn_no.clicked.connect(dialog.reject)
        btn_row.addWidget(btn_no)
        btn_yes = QPushButton("Save Changes")
        btn_yes.setProperty("role", "primary")
        btn_yes.setDefault(True)
        btn_yes.clicked.connect(dialog.accept)
        btn_row.addWidget(btn_yes)
        root.addLayout(btn_row)

        return dialog.exec() == QDialog.Accepted

    @staticmethod
    def _save_detail_table_columns(detail_rows: list) -> tuple[list[str], list[str]]:
        column_labels = {
            "filename": "Filename",
            "type": "Audio type",
            "category": "Category",
            "subcategory": "Subcategory",
            "pack": "Pack",
        }
        field_order = ["type", "category", "subcategory", "pack"]
        changed_keys = set()
        for row in detail_rows or []:
            changed_keys.update(row.get("_changed_keys") or set())
        visible_keys = ["filename"] + [key for key in field_order if key in changed_keys]
        return visible_keys, [column_labels[key] for key in visible_keys]

    def has_changes(self):
        return self.reorg_manager.has_changes() or self._draft_profile_active

    def confirm_clear_pending_draft(self, action_text: str = "continue") -> bool:
        if not self.has_changes():
            return True
        box = QMessageBox(self._parent_widget())
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("Pending Draft Changes")
        box.setText(f"Save or discard the current draft before you {action_text}.")
        save_button = box.addButton("Save Draft", QMessageBox.AcceptRole)
        discard_button = box.addButton("Discard Draft", QMessageBox.DestructiveRole)
        cancel_button = box.addButton(QMessageBox.Cancel)
        box.setDefaultButton(save_button)
        box.exec()
        clicked = box.clickedButton()
        if clicked is save_button:
            self.save_reorg_draft()
            return not self.has_changes()
        if clicked is discard_button:
            self.discard_reorg_draft(confirm=False)
            return not self.has_changes()
        return clicked is not cancel_button and not self.has_changes()

    def stage_tree_reorg_updates(
        self,
        updates,
        action_label="Tree Reorganize",
        collision_check=False,
        partial_tree_refresh=False,
        move_profile_node_id: str | None = None,
        target_fields: dict[str, str] | None = None,
        learn=True,
        mark_semantic_override=True,
    ):
        if not self.app.model:
            return False

        if not self.has_changes():
            self.reorg_manager.init_counters(self.app.model.records)
        self._partial_refresh = bool(partial_tree_refresh)

        normalized = []
        undo_updates = []
        touched_records = {}
        for rec, col, new_val in updates or []:
            old_val = self.app.model._get_record_value(rec, col)
            if old_val == new_val:
                continue
            
            normalized.append((rec, col, new_val))
            undo_updates.append((rec, col, old_val, new_val))
            touched_records[stable_record_identity(rec)] = rec

        from ..utils.tree_helpers import get_tree_branch_path_for_record
        before_branch_paths = {
            path for rec in touched_records.values() if (path := get_tree_branch_path_for_record(self.app, rec))
        }

        profile_changed = self._stage_profile_node_move(move_profile_node_id, target_fields or {})
        if profile_changed:
            self._partial_refresh = False

        if not normalized and not profile_changed:
            return False

        undo_stack = getattr(self.app, "undo_stack", None)
        if normalized and not profile_changed and not move_profile_node_id and undo_stack is not None:
            undo_stack.push(DraftEditCommand(
                self,
                undo_updates,
                action_label,
                learn=learn,
                mark_semantic_override=mark_semantic_override,
            ))
            return True

        if normalized:
            self.stage_updates(normalized, collision_check, learn=learn)
            self.app.model._apply_bulk_values(normalized)
            self._reconcile_draft_originals(normalized)
            self.draftChanged.emit()
            if profile_changed or move_profile_node_id:
                for rec, _col, _new_val in normalized:
                    if hasattr(rec, SEMANTIC_OVERRIDE_ATTR):
                        delattr(rec, SEMANTIC_OVERRIDE_ATTR)
            elif not mark_semantic_override:
                for rec, _col, _new_val in normalized:
                    if hasattr(rec, SEMANTIC_OVERRIDE_ATTR):
                        delattr(rec, SEMANTIC_OVERRIDE_ATTR)
            else:
                self._mark_semantic_overrides(normalized)
        elif profile_changed:
            self.draftChanged.emit()
        
      
        self._branch_paths.update(before_branch_paths)
        for rec in touched_records.values():
            path = get_tree_branch_path_for_record(self.app, rec)
            if path: self._branch_paths.add(path)
            
        if self.app.view_controller.is_tree_visible():
            self._draft_tree_refresh_timer.start(140)
            
        if normalized:
            self.schedule_reorg_impact_analysis()
        elif profile_changed:
            self.app.footer.log("<b>Draft custom tree:</b> placement staged.")
            self.app.footer.set_status("Custom tree placement staged.")
        return True

    def _mark_semantic_overrides(
        self,
        updates,
    ) -> None:
        from ..utils.constants import StagingColumn

        semantic_columns = self._semantic_reorg_columns()
        for rec, col, _new_val in updates:
            if col in semantic_columns:
                setattr(rec, SEMANTIC_OVERRIDE_ATTR, True)

    @staticmethod
    def _semantic_reorg_columns():
        from ..utils.constants import StagingColumn

        return {
            StagingColumn.TYPE,
            StagingColumn.CATEGORY,
            StagingColumn.SUBCATEGORY,
            StagingColumn.PACK,
        }

    def _stage_profile_node_move(self, node_id: str | None, target_fields: dict[str, str]) -> bool:
        node_id = (node_id or "").strip()
        if not node_id or not target_fields:
            return False
        controller = getattr(self.app, "tree_organization_controller", None)
        profile = getattr(controller, "active_profile", None)
        if profile is None:
            return False

        nodes = list(profile.nodes)
        by_id = {node.id: node for node in nodes}
        source = by_id.get(node_id)
        if (
            source is None
            or source.node_type not in {"custom", "system"}
            or source.id == profile.root_node_id
            or self._is_read_only_profile_node(source, profile)
        ):
            return False

        if self._draft_profile_original is None:
            self._draft_profile_original = profile

        parent_id, nodes = self._ensure_semantic_profile_path(profile, nodes, target_fields)
        if parent_id == source.parent_id:
            return False
        if self._is_profile_descendant(parent_id, source.id, nodes):
            return False

        existing_duplicate = None
        for node in nodes:
            if node.parent_id == parent_id and node.id != source.id:
                if node.node_type == "custom":
                    if (node.name.strip().lower() == source.name.strip().lower() or
                        (node.filter_query and source.filter_query and
                         node.filter_query.strip() == source.filter_query.strip())):
                        existing_duplicate = node
                        break

        old_parent_id = source.parent_id
        if existing_duplicate is not None:
            nodes = [
                replace(n, parent_id=existing_duplicate.id) if n.parent_id == source.id else n
                for n in nodes
                if n.id != source.id
            ]
        else:
            sibling_order = self._next_profile_sort_order(nodes, parent_id, excluding={source.id})
            moved = replace(source, parent_id=parent_id, sort_order=sibling_order)
            nodes = [moved if node.id == source.id else node for node in nodes]
            nodes = self._renumber_profile_siblings(nodes, parent_id)

        nodes = self._renumber_profile_siblings(nodes, old_parent_id)

        updated = replace(profile, nodes=nodes, updated_at=utc_now_iso())
        controller.active_profile = updated
        if hasattr(controller, "_sync_active_profile"):
            controller._sync_active_profile(refresh=False)
        self._draft_profile_active = True
        return True

    def _ensure_semantic_profile_path(
        self,
        profile: TreeOrganizationProfile,
        nodes: list[TreeOrganizationNode],
        target_fields: dict[str, str],
    ) -> tuple[str, list[TreeOrganizationNode]]:
        parent_id = profile.root_node_id
        for field_name in ("audio_type", "category", "subcategory", "pack"):
            value = (target_fields.get(field_name) or "").strip()
            if not value:
                continue
            existing = self._find_semantic_child(profile, nodes, parent_id, field_name, value)
            if existing is None:
                node_id = self._unique_profile_node_id(
                    self._profile_node_id((*self._profile_path_for_parent(nodes, parent_id), value)),
                    nodes,
                )
                existing = TreeOrganizationNode(
                    id=node_id,
                    parent_id=parent_id,
                    name=value,
                    filter_query=self._profile_filter_query(field_name, value),
                    node_type="system",
                    sort_order=self._next_profile_sort_order(nodes, parent_id),
                    enabled=True,
                )
                nodes.append(existing)
            parent_id = existing.id
        return parent_id, nodes

    def _find_semantic_child(
        self,
        profile: TreeOrganizationProfile,
        nodes: list[TreeOrganizationNode],
        parent_id: str,
        field_name: str,
        value: str,
    ) -> TreeOrganizationNode | None:
        for node in nodes:
            if node.parent_id != parent_id or node.id == node.parent_id:
                continue
            if self._is_read_only_profile_node(node, profile):
                continue
            fields = exact_destination_fields_from_filter(node.filter_query)
            if fields.get(field_name) == value:
                return node
        return None

    def _profile_path_for_parent(self, nodes: list[TreeOrganizationNode], parent_id: str) -> tuple[str, ...]:
        by_id = {node.id: node for node in nodes}
        parts = []
        current = by_id.get(parent_id)
        while current is not None and current.parent_id is not None:
            parts.append(current.name)
            current = by_id.get(current.parent_id)
        return tuple(reversed(parts))

    def _is_profile_descendant(self, node_id: str, possible_parent_id: str, nodes: list[TreeOrganizationNode]) -> bool:
        by_id = {node.id: node for node in nodes}
        current = by_id.get(node_id)
        while current is not None and current.parent_id is not None:
            if current.parent_id == possible_parent_id:
                return True
            current = by_id.get(current.parent_id)
        return False

    def _next_profile_sort_order(
        self,
        nodes: list[TreeOrganizationNode],
        parent_id: str | None,
        *,
        excluding: set[str] | None = None,
    ) -> int:
        excluded = set(excluding or set())
        siblings = [node for node in nodes if node.parent_id == parent_id and node.id not in excluded]
        return max((node.sort_order for node in siblings), default=0) + 1

    def _renumber_profile_siblings(
        self,
        nodes: list[TreeOrganizationNode],
        parent_id: str | None,
    ) -> list[TreeOrganizationNode]:
        siblings = sorted(
            [node for node in nodes if node.parent_id == parent_id],
            key=lambda node: (node.sort_order, node.name.lower()),
        )
        order_by_id = {node.id: index + 1 for index, node in enumerate(siblings)}
        return [
            replace(node, sort_order=order_by_id[node.id]) if node.id in order_by_id else node
            for node in nodes
        ]

    def _save_draft_profile_if_needed(self) -> None:
        if not self._draft_profile_active:
            return
        controller = getattr(self.app, "tree_organization_controller", None)
        profile = getattr(controller, "active_profile", None)
        repository = getattr(controller, "repository", None)
        if profile is None or repository is None:
            return
        saved = repository.save_profile(profile)
        controller.active_profile = saved
        if hasattr(controller, "_persist_active_profile_id"):
            controller._persist_active_profile_id(saved.id)
        if hasattr(controller, "_sync_active_profile"):
            controller._sync_active_profile(refresh=False)

    def _restore_draft_profile_if_needed(self) -> None:
        if self._draft_profile_original is None:
            return
        controller = getattr(self.app, "tree_organization_controller", None)
        if controller is not None:
            controller.active_profile = self._draft_profile_original
            if hasattr(controller, "_sync_active_profile"):
                controller._sync_active_profile(refresh=False)
        self._draft_profile_original = None
        self._draft_profile_active = False

    @staticmethod
    def _profile_node_id(parts: tuple[str, ...]) -> str:
        slug = "_".join(re.sub(r"[^a-z0-9]+", "_", part.lower()).strip("_") for part in parts)
        return f"node_{slug[:80] or uuid.uuid4().hex[:8]}"

    @staticmethod
    def _unique_profile_node_id(base_id: str, nodes: list[TreeOrganizationNode]) -> str:
        used = {node.id for node in nodes}
        if base_id not in used:
            return base_id
        suffix = 2
        while f"{base_id}_{suffix}" in used:
            suffix += 1
        return f"{base_id}_{suffix}"

    @staticmethod
    def _profile_filter_query(field_name: str, value: str) -> str | None:
        prefixes = {
            "audio_type": "type",
            "category": "cat",
            "subcategory": "sub",
            "pack": "pack",
        }
        prefix = prefixes.get(field_name)
        if prefix is None:
            return None
        raw_value = "Non-Audio Assets" if field_name == "audio_type" and value == "Utility" else value
        escaped = raw_value.replace('"', '\\"')
        return f'{prefix}:"{escaped}"'

    @staticmethod
    def _is_read_only_profile_node(node: TreeOrganizationNode, profile: TreeOrganizationProfile) -> bool:
        return (
            node.parent_id == profile.root_node_id
            and node.node_type == "system"
            and node.name == "Utility"
            and node.filter_query == 'type:"Non-Audio Assets"'
        )

    def stage_updates(self, updates, collision_check=False, learn=True):
        """
        updates: list of (rec, col, val)
        """
        staged_originals = []
        if self.app.model:
            for rec, col, _new_val in updates:
                staged_originals.append((rec, col, self.app.model._get_record_value(rec, col)))
        else:
            staged_originals = list(updates)
        self.reorg_manager.stage_updates(staged_originals, collision_check=collision_check, learn=learn)
        self.draftChanged.emit()

    def _reconcile_draft_originals(self, updates) -> None:
        if not self.app.model:
            return
        for rec, col, _value in updates or []:
            key = (stable_record_identity(rec), col)
            original = self.reorg_manager.originals.get(key)
            if original is None:
                continue
            _orig_rec, _orig_col, original_value = original
            if self.app.model._get_record_value(rec, col) == original_value:
                self.reorg_manager.originals.pop(key, None)
                self.reorg_manager.non_learning_originals.discard(key)
        if not self.reorg_manager.has_changes():
            self.reorg_manager.clear()

    def handle_tree_reorganize(self, records, fields):
        from ..utils.constants import StagingColumn
        from gui.widgets.library_filters import category_is_valid_for_audio_type, fallback_category_for_audio_type
        if not self.app.model or not records or not fields:
            return

        resolved = []
        row_by_rec_id = {stable_record_identity(rec): row for row, rec in enumerate(self.app.model.records)}
        for item in records:
            if isinstance(item, int):
                resolved.append(item)
            else:
                row = row_by_rec_id.get(stable_record_identity(item))
                if row is not None: resolved.append(row)

        if not resolved:
            return

        labels = []
        if fields.get("audio_type"): labels.append(f"Type: {fields['audio_type']}")
        if fields.get("category"): labels.append(f"Category: {fields['category']}")
        if fields.get("subcategory"): labels.append(f"Subcategory: {fields['subcategory']}")
        if fields.get("pack"): labels.append(f"Pack: {fields['pack']}")

        updates = []
        for row in resolved:
            rec = self.app.model.records[row]
            row_updates = []
            if fields.get("audio_type"):
                row_updates.append((rec, StagingColumn.TYPE, fields["audio_type"]))
            if fields.get("category"):
                row_updates.append((rec, StagingColumn.CATEGORY, fields["category"]))
            if fields.get("subcategory"):
                row_updates.append((rec, StagingColumn.SUBCATEGORY, fields["subcategory"]))
            if fields.get("pack"):
                row_updates.append((rec, StagingColumn.PACK, fields["pack"]))
            target_audio_type = str(fields.get("audio_type") or getattr(rec, "audio_type", "") or "").strip()
            target_category = str(fields.get("category") or getattr(rec, "category", "") or "").strip()
            if target_audio_type and target_category and not category_is_valid_for_audio_type(target_category, target_audio_type):
                fallback = fallback_category_for_audio_type(target_audio_type)
                row_updates = [
                    update
                    for update in row_updates
                    if update[1] not in {StagingColumn.CATEGORY, StagingColumn.SUBCATEGORY}
                ]
                row_updates.append((rec, StagingColumn.CATEGORY, (fallback, "")))
            updates.extend(row_updates)

        changed = self.stage_tree_reorg_updates(
            updates,
            action_label="Tree Reorganize",
            move_profile_node_id=fields.get("__move_profile_node_id"),
            target_fields=fields.get("__target_fields") or fields,
            mark_semantic_override=False,
        )
        if changed:
            self.app.footer.log(f"<b>Draft reorganized {len(resolved)} items:</b> {', '.join(labels)}")

    def clear(self):
        self.reorg_manager.clear()
        self._draft_profile_original = None
        self._draft_profile_active = False
        self.draftChanged.emit()

    def handle_tree_category_change(self, rec, category):
        self.apply_bulk_category(category, [rec])

    def _normalize_records(self, records):
        model = getattr(self.app, "model", None)
        normalized = []
        for item in records or []:
            if isinstance(item, int) and model is not None and hasattr(model, "record"):
                try:
                    normalized.append(model.record(item))
                except (IndexError, AttributeError, TypeError):
                    continue
                continue
            if hasattr(item, "source_path") or hasattr(item, "audio_type"):
                normalized.append(item)
        return normalized

    def _apply_draft_updates(
        self,
        updates,
        *,
        tree_delay_ms=0,
        persist=False,
        undo_text="Draft Edit",
        push_undo=True,
        learn=True,
        mark_semantic_override=True,
    ):
        if not self.app.model or not updates:
            return []

        normalized = []
        for rec, col, new_val in updates:
            old_val = self.app.model._get_record_value(rec, col)
            if old_val == new_val:
                continue
            normalized.append((rec, col, old_val, new_val))

        if not normalized:
            return []

        undo_stack = getattr(self.app, "undo_stack", None)
        if push_undo and undo_stack is not None:
            undo_stack.push(DraftEditCommand(
                self,
                normalized,
                undo_text,
                learn=learn,
                mark_semantic_override=mark_semantic_override,
            ))
            return [(rec, col, new_val) for rec, col, _old_val, new_val in normalized]

        apply_updates = [(rec, col, new_val) for rec, col, _old_val, new_val in normalized]

        if not self.has_changes():
            self.reorg_manager.init_counters(self.app.model.records)
        self.stage_updates(apply_updates, learn=learn)
        self.app.model._apply_bulk_values(apply_updates)
        self._reconcile_draft_originals(apply_updates)
        self.draftChanged.emit()
        if mark_semantic_override:
            self._mark_semantic_overrides(apply_updates)
        else:
            for rec, _col, _new_val in apply_updates:
                if hasattr(rec, SEMANTIC_OVERRIDE_ATTR):
                    delattr(rec, SEMANTIC_OVERRIDE_ATTR)
        if self.has_changes():
            self.schedule_reorg_impact_analysis()
        else:
            self._reorg_impact_timer.stop()
            self._impact_pending_notice_shown = False
            if getattr(self.app, "footer", None):
                self.app.footer.set_reorg_draft_state("", False)
                self.app.footer.set_status("Ready")
        if persist:
            finalize_model_mutation(self.app, resort=True, refresh_search=False, tree_delay_ms=tree_delay_ms)
        else:
            self.app.view_controller.update_library_views(tree_delay_ms=tree_delay_ms)
        return apply_updates

    def apply_table_edit(self, rec, col, value) -> bool:
        return bool(self._apply_draft_updates([(rec, col, value)], persist=False))

    def apply_table_bulk_updates(self, updates, text: str = "Table Edit") -> bool:
        return bool(self._apply_draft_updates(list(updates or []), persist=False, undo_text=text or "Table Edit"))

    def apply_non_learning_bulk_updates(self, updates, text: str = "Table Edit") -> bool:
        return bool(self._apply_draft_updates(
            list(updates or []),
            persist=False,
            undo_text=text or "Table Edit",
            learn=False,
        ))

    def apply_bulk_category(self, category, records):
        from ..utils.constants import StagingColumn
        updates = []
        for rec in self._normalize_records(records):
            old_val = getattr(rec, "category", "")
            if old_val != category:
                updates.append((rec, StagingColumn.CATEGORY, category))
        return self._apply_draft_updates(updates, undo_text=f"Set Category: {category}")

    def apply_bulk_subcategory(self, category, subcategory, records):
        from ..utils.constants import StagingColumn
        updates = []
        for rec in self._normalize_records(records):
            old_cat = getattr(rec, "category", "")
            old_sub = getattr(rec, "subcategory", "")
            if old_cat != category or old_sub != subcategory:
                updates.append((rec, StagingColumn.CATEGORY, category))
                updates.append((rec, StagingColumn.SUBCATEGORY, subcategory))
        return self._apply_draft_updates(updates, undo_text=f"Set Subcategory: {subcategory}")

    def apply_bulk_type(self, type_label, records):
        from ..utils.constants import StagingColumn
        updates = []
        for rec in self._normalize_records(records):
            old_val = getattr(rec, "audio_type", "")
            if old_val != type_label:
                updates.append((rec, StagingColumn.TYPE, type_label))
        return self._apply_draft_updates(updates, undo_text=f"Set Type: {type_label}")

    def apply_bulk_pack(self, pack_name, records):
        from ..utils.constants import StagingColumn
        updates = []
        for rec in self._normalize_records(records):
            old_val = getattr(rec, "pack", "")
            if old_val != pack_name:
                updates.append((rec, StagingColumn.PACK, pack_name))
        return self._apply_draft_updates(updates, undo_text=f"Set Pack: {pack_name}")

    def apply_preserve_pack(self, records, path):
        from pathlib import Path
        from ..utils.constants import DRAFT_IS_PRESERVED_FIELD, DRAFT_PRESERVED_ROOT_FIELD

        preserved_root = Path(path) if path else None
        updates = []
        for rec in records or []:
            if not bool(getattr(rec, "is_preserved", False)):
                updates.append((rec, DRAFT_IS_PRESERVED_FIELD, True))
            if getattr(rec, "preserved_root", None) != preserved_root:
                updates.append((rec, DRAFT_PRESERVED_ROOT_FIELD, preserved_root))
        changed = self._apply_draft_updates(updates)
        if changed:
            self.app.footer.log(f"<b>Draft preserve:</b> staged {len(records or [])} item(s).")
        return changed

    def apply_unpreserve_pack(self, records, _path=None):
        from ..utils.constants import DRAFT_IS_PRESERVED_FIELD, DRAFT_PRESERVED_ROOT_FIELD

        updates = []
        for rec in records or []:
            if bool(getattr(rec, "is_preserved", False)):
                updates.append((rec, DRAFT_IS_PRESERVED_FIELD, False))
            if getattr(rec, "preserved_root", None) is not None:
                updates.append((rec, DRAFT_PRESERVED_ROOT_FIELD, None))
        changed = self._apply_draft_updates(updates)
        if changed:
            self.app.footer.log(f"<b>Draft un-preserve:</b> staged {len(records or [])} item(s).")
        return changed


class DraftEditCommand(QUndoCommand):
    def __init__(self, controller: DraftingController, updates, text: str, *, learn=True, mark_semantic_override=True):
        super().__init__(text or "Draft Edit")
        self.controller = controller
        self.updates = list(updates or [])
        self.learn = learn
        self.mark_semantic_override = mark_semantic_override

    def undo(self) -> None:
        self.controller._apply_draft_updates(
            [(rec, col, old_val) for rec, col, old_val, _new_val in self.updates],
            undo_text=self.text(),
            push_undo=False,
            learn=self.learn,
            mark_semantic_override=self.mark_semantic_override,
        )

    def redo(self) -> None:
        self.controller._apply_draft_updates(
            [(rec, col, new_val) for rec, col, _old_val, new_val in self.updates],
            undo_text=self.text(),
            push_undo=False,
            learn=self.learn,
            mark_semantic_override=self.mark_semantic_override,
        )
