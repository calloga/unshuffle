from __future__ import annotations

import uuid

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QMessageBox

from gui.core.tree_organization_defaults import (
    append_default_profile_nodes,
    build_default_tree_nodes,
    default_filter_query,
    default_node_id,
)
from unshuffle.logic.tree_organization import (
    TreeOrganizationNode,
    TreeOrganizationProfile,
    TreeOrganizationProfileStoreError,
    TreeOrganizationRepository,
)
from unshuffle.logic.tree_organization.models import utc_now_iso

ACTIVE_PROFILE_ID_KEY = "tree_organization_active_profile_id"


class TreeOrganizationController(QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.app = parent
        self.repository = TreeOrganizationRepository()
        self.active_profile: TreeOrganizationProfile | None = self._load_persisted_active_profile()
        self.editor_widget = None

    def open_editor(self) -> None:
        from gui.widgets.tree_organization import TreeOrganizationEditor

        if self.editor_widget is not None:
            self.show_profile_list()
            if getattr(self.app, "system_page", None):
                self.app.system_page.set_tree_organization_panel(self.editor_widget)
            if getattr(self.app, "open_system_workspace", None):
                self.app.open_system_workspace("tree_organization")
            return

        records = list(getattr(getattr(self.app, "model", None), "records", []) or [])
        dialog_profile = self.active_profile or self._editable_profile_from_default(records)
        try:
            profiles = self.repository.list_profiles()
        except TreeOrganizationProfileStoreError as exc:
            QMessageBox.warning(self.app, "Tree Profiles Unavailable", str(exc))
            profiles = []
        editor = TreeOrganizationEditor(profiles, dialog_profile, records, self.app, embedded=True)
        editor.profileSaved.connect(self.save_profile)
        editor.profileApplied.connect(self.apply_profile)
        editor.profileDeleted.connect(self.delete_profile)
        editor.profileDisabled.connect(self.disable_profile)
        self.editor_widget = editor
        if getattr(self.app, "system_page", None):
            self.app.system_page.set_tree_organization_panel(editor)
        if getattr(self.app, "open_system_workspace", None):
            self.app.open_system_workspace("tree_organization")

    def show_profile_list(self) -> None:
        editor = self.editor_widget
        if editor is None:
            return
        show_profile_list = getattr(editor, "show_profile_list", None)
        if callable(show_profile_list):
            show_profile_list()

    def save_profile(self, profile: TreeOrganizationProfile) -> None:
        if not profile.nodes:
            self.repository.delete_profile(profile.id)
            if self.active_profile and self.active_profile.id == profile.id:
                self.disable_profile()
            return
        try:
            saved = self.repository.save_profile(profile)
        except TreeOrganizationProfileStoreError as exc:
            QMessageBox.warning(self.app, "Tree Profiles Unavailable", str(exc))
            return
        if self.active_profile and self.active_profile.id == saved.id:
            self.active_profile = saved
            self._persist_active_profile_id(saved.id)
            self._sync_active_profile()
            self._refresh_editor()
        elif self.editor_widget is not None:
            try:
                self.editor_widget._profiles = self.repository.list_profiles()
                self.editor_widget._selected_profile_id = saved.id
                self.editor_widget._load_profiles()
            except TreeOrganizationProfileStoreError as exc:
                QMessageBox.warning(self.app, "Tree Profiles Unavailable", str(exc))
            except RuntimeError:
                self.editor_widget = None

    def apply_profile(self, profile: TreeOrganizationProfile) -> None:
        records = list(getattr(getattr(self.app, "model", None), "records", []) or [])
        from unshuffle.logic.tree_organization import TreeOrganizationResolver

        validation = TreeOrganizationResolver().validate_profile(profile, records)
        if not validation.valid:
            QMessageBox.warning(self.app, "Invalid Custom Tree", "\n".join(validation.blocking_messages[:6]))
            return
        self.active_profile = profile
        self._persist_active_profile_id(profile.id)
        self._sync_active_profile()
        self._refresh_editor()

    def delete_profile(self, profile_id: str) -> None:
        try:
            self.repository.delete_profile(profile_id)
        except TreeOrganizationProfileStoreError as exc:
            QMessageBox.warning(self.app, "Tree Profiles Unavailable", str(exc))
            return
        if self.active_profile and self.active_profile.id == profile_id:
            self.disable_profile()
        else:
            self._refresh_editor()

    def disable_profile(self, *, refresh: bool = True) -> None:
        self.active_profile = None
        self._persist_active_profile_id(None)
        self._sync_active_profile(refresh=refresh)
        self._refresh_editor()

    def _load_persisted_active_profile(self) -> TreeOrganizationProfile | None:
        settings = getattr(self.app, "settings", None)
        if settings is None:
            return None
        profile_id = str(settings.value(ACTIVE_PROFILE_ID_KEY, "") or "").strip()
        if not profile_id:
            return None
        try:
            profile = self.repository.get_profile(profile_id)
        except TreeOrganizationProfileStoreError:
            settings.remove(ACTIVE_PROFILE_ID_KEY)
            return None
        if profile is None:
            settings.remove(ACTIVE_PROFILE_ID_KEY)
        return profile

    def _persist_active_profile_id(self, profile_id: str | None) -> None:
        settings = getattr(self.app, "settings", None)
        if settings is None:
            return
        value = (profile_id or "").strip()
        if value:
            settings.setValue(ACTIVE_PROFILE_ID_KEY, value)
        else:
            settings.remove(ACTIVE_PROFILE_ID_KEY)

    def _sync_active_profile(self, *, refresh: bool = True) -> None:
        profile = self.active_profile
        if getattr(self.app, "engine", None):
            setattr(self.app.engine, "active_tree_profile", profile)
            inner = getattr(self.app.engine, "engine", None)
            if inner is not None:
                setattr(inner, "active_tree_profile", profile)
        if getattr(self.app, "library_tab", None):
            self.app.library_tab.tree_model.set_custom_tree_profile(profile)
            self.app.library_tab.set_tree_organization_state(bool(profile), profile.name if profile else "")
        if refresh and getattr(self.app, "view_controller", None):
            self.app.view_controller.update_library_views(tree_delay_ms=0)

    def _profile_from_current_tree(self, records: list) -> TreeOrganizationProfile:
        now = utc_now_iso()
        library_tab = getattr(self.app, "library_tab", None)
        nodes = build_default_tree_nodes(records, library_tab, collapse_residual_other=False)
        return TreeOrganizationProfile(
            id=f"profile_{uuid.uuid4().hex[:12]}",
            name="Default",
            root_node_id="root",
            nodes=nodes,
            created_at=now,
            updated_at=now,
        )

    def _editable_profile_from_default(self, records: list) -> TreeOrganizationProfile:
        default = self._profile_from_current_tree(records)
        now = utc_now_iso()
        return TreeOrganizationProfile(
            id=f"profile_{uuid.uuid4().hex[:12]}",
            name="Custom Tree",
            root_node_id=default.root_node_id,
            nodes=list(default.nodes),
            created_at=now,
            updated_at=now,
        )

    def _refresh_editor(self) -> None:
        editor = self.editor_widget
        if editor is None:
            return
        try:
            records = list(getattr(getattr(self.app, "model", None), "records", []) or [])
            profile = self.active_profile or self._editable_profile_from_default(records)
            editor.reload(self.repository.list_profiles(), profile, records)
        except TreeOrganizationProfileStoreError as exc:
            QMessageBox.warning(self.app, "Tree Profiles Unavailable", str(exc))
        except RuntimeError:
            self.editor_widget = None

    def _append_profile_nodes(self, nodes: list[TreeOrganizationNode], parent_id: str, grouped, levels: list, path: tuple[str, ...]) -> None:
        append_default_profile_nodes(nodes, parent_id, grouped, levels, path, collapse_residual_other=False)

    def _ensure_default_utility_node(self, nodes: list[TreeOrganizationNode]) -> None:
        from gui.core.tree_organization_defaults import ensure_default_utility_node

        ensure_default_utility_node(nodes)

    @staticmethod
    def _node_id(parts: tuple[str, ...]) -> str:
        return default_node_id(parts)

    @staticmethod
    def _filter_query(field: str, value: str) -> str | None:
        return default_filter_query(field, value)
