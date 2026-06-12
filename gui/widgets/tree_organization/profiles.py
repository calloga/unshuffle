from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import replace

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu, QWidget

from unshuffle.logic.tree_organization import TreeOrganizationProfile, TreeOrganizationResolver, make_empty_profile
from unshuffle.logic.tree_organization.models import utc_now_iso


class TreeOrganizationProfileMixin:
    def _load_profiles(self) -> None:
        self.profile_combo.blockSignals(True)
        self.profile_combo.clear()
        self.profile_combo.addItem("Default", "")
        known_ids = {profile.id for profile in self._profiles}
        if self._profile.id not in known_ids:
            self.profile_combo.addItem(f"{self._profile.name} (unsaved)", self._profile.id)
        for profile in self._profiles:
            self.profile_combo.addItem(profile.name, profile.id)
        self.profile_combo.blockSignals(False)

    def reload(
        self,
        profiles: list[TreeOrganizationProfile],
        active_profile: TreeOrganizationProfile | None,
        records: list,
    ) -> None:
        self._profiles = list(profiles)
        self._records = list(records)
        self._active_profile_id = active_profile.id if active_profile is not None else ""
        known_ids = {profile.id for profile in self._profiles}
        if self._selected_profile_id not in known_ids and self._selected_profile_id != "":
            self._selected_profile_id = self._active_profile_id
        self._pending_profile = active_profile
        if getattr(self, "_editor_built", False):
            self._filter_suggestions = self._build_filter_suggestions()
            if hasattr(self, "node_filter"):
                self.node_filter.set_suggestions(self._filter_suggestions, self._saved_filter_suggestions)
            profile = active_profile or make_empty_profile(f"profile_{uuid.uuid4().hex[:12]}", "Custom Tree")
            self._load_profiles()
            self._load_profile(profile)
        if hasattr(self, "_show_profile_list"):
            self._show_profile_list()

    def _load_profile(self, profile: TreeOrganizationProfile) -> None:
        self._profile = profile
        self._nodes = list(profile.nodes)
        self._undo_states.clear()
        self._redo_states.clear()
        self._rebuild_node_indexes()
        self._selected_id = profile.root_node_id
        self.profile_name.setText(profile.name)
        self._match_count_cache.clear()
        self._render_tree()
        self._select_node(self._selected_id)
        self._validate()
        self._refresh_undo_buttons()
        self._schedule_count_refresh(delay_ms=300)

    def _profile_from_ui(self) -> TreeOrganizationProfile:
        now = utc_now_iso()
        nodes = list(self._nodes)
        selected = self._selected_node() if hasattr(self, "_selected_node") else None
        if selected is not None and not self._is_read_only_node(selected):
            updated = replace(
                selected,
                name=self.node_name.text().strip() or "Folder",
                filter_query=self.node_filter.text().strip() or None,
                node_type=self.node_type.currentText(),
                enabled=True,
                hide_subbranches=bool(
                    getattr(self, "node_hide_subbranches", None)
                    and self.node_hide_subbranches.isChecked()
                ),
            )
            nodes = [updated if node.id == selected.id else node for node in nodes]
        return TreeOrganizationProfile(
            id=self._profile.id,
            name=self.profile_name.text().strip() or self._profile.name or "Custom Tree",
            root_node_id=self._profile.root_node_id,
            nodes=nodes,
            created_at=self._profile.created_at,
            updated_at=now,
        )

    def _validate(self, *, full: bool = False) -> bool:
        profile = self._profile_from_ui()
        name_conflict = self._profile_name_conflict(profile.name, exclude_profile_id=profile.id)
        if name_conflict:
            self.validation_label.setText(f'Another custom tree is already named "{profile.name}".')
            return False
        records = self._records if full else []
        result = TreeOrganizationResolver().validate_profile(profile, records)
        self.validation_label.setText("\n".join(result.blocking_messages[:6]) if result.blocking_messages else "Profile looks valid.")
        return result.valid

    def _save(self) -> None:
        if not self._validate(full=True):
            return
        profile = self._profile_from_ui()
        self._profile = profile
        self.profileSaved.emit(profile)
        self._selected_profile_id = profile.id
        if self._embedded:
            self._show_profile_list()

    def _apply(self) -> None:
        if not self._validate(full=True):
            return
        profile = self._profile_from_ui()
        self._profile = profile
        self.profileSaved.emit(profile)
        self.profileApplied.emit(profile)
        if self._embedded:
            self._active_profile_id = profile.id
            self._selected_profile_id = profile.id
            self._show_profile_list()
        if not self._embedded:
            self.accept()

    def _on_profile_selected(self, index: int) -> None:
        profile_id = self.profile_combo.itemData(index)
        if not profile_id:
            self._disable_custom_tree()
            return
        if profile_id == self._profile.id:
            self._load_profile(self._profile)
            return
        for profile in self._profiles:
            if profile.id == profile_id:
                self._load_profile(profile)
                return

    def _show_options_menu(self) -> None:
        menu = QMenu(self if isinstance(self, QWidget) else None)
        default_action = QAction("Default", menu)
        default_action.triggered.connect(self._disable_custom_tree)
        menu.addAction(default_action)
        reset_action = QAction("Reset Custom Tree", menu)
        reset_action.triggered.connect(self._reset_custom_tree)
        menu.addAction(reset_action)
        menu.addSeparator()

        new_action = QAction("New Custom Tree", menu)
        new_action.triggered.connect(self._new_profile)
        menu.addAction(new_action)

        menu.exec(self.btn_options.mapToGlobal(self.btn_options.rect().bottomLeft()))
