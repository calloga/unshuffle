from __future__ import annotations

import uuid

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QApplication, QMessageBox

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
        self._sync_profile_options()

    def open_editor(self) -> None:
        from gui.widgets.tree_organization import TreeOrganizationEditor

        if self.editor_widget is not None:
            self.show_profile_list()
            if getattr(self.app, "system_page", None):
                self.app.system_page.set_tree_organization_panel(self.editor_widget)
            if getattr(self.app, "open_system_workspace", None):
                self.app.open_system_workspace("tree_organization")
            return

        records = self._record_source()
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
            signature = self._ensure_profile_projection(saved)
            self._retain_profile_projection(saved.id, signature)
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
        self._sync_profile_options()

    def apply_profile(self, profile: TreeOrganizationProfile) -> None:
        from unshuffle.logic.tree_organization import TreeOrganizationResolver

        validation = TreeOrganizationResolver().validate_profile(profile, [])
        if not validation.valid:
            QMessageBox.warning(self.app, "Invalid Custom Tree", "\n".join(validation.blocking_messages[:6]))
            return
        monitor = getattr(self.app, "operation_monitor", None)
        needs_projection = not self._has_profile_projection(profile)
        token = monitor.start("Switching Tree Organization") if monitor is not None and needs_projection else None
        if token is not None:
            monitor.update({"phase": "Preparing tree", "message": "Preparing tree organization..."}, token=token)
            QApplication.processEvents()
        try:
            signature = self._ensure_profile_projection(profile)
        except ValueError as exc:
            if token is not None:
                monitor.fail(str(exc), token=token)
            QMessageBox.warning(self.app, "Invalid Custom Tree", str(exc))
            return
        self._retain_profile_projection(profile.id, signature)
        self.active_profile = profile
        self._persist_active_profile_id(profile.id)
        self._sync_active_profile()
        self._refresh_editor()
        if token is not None:
            monitor.finish("Tree organization ready.", token=token)

    def switch_profile(self, profile_id: str) -> None:
        profile_id = str(profile_id or "").strip()
        if (not profile_id and self.active_profile is None) or (
            self.active_profile is not None and self.active_profile.id == profile_id
        ):
            return
        drafting = getattr(self.app, "drafting_controller", None)
        if drafting is not None and not drafting.confirm_clear_pending_draft("switch tree organization"):
            self._sync_profile_options()
            return
        if not profile_id:
            self.disable_profile()
            return
        try:
            profile = self.repository.get_profile(profile_id)
        except TreeOrganizationProfileStoreError as exc:
            QMessageBox.warning(self.app, "Tree Profiles Unavailable", str(exc))
            return
        if profile is None:
            self._sync_profile_options()
            return
        self.apply_profile(profile)

    def delete_profile(self, profile_id: str) -> None:
        self._clear_profile_projection(profile_id)
        try:
            self.repository.delete_profile(profile_id)
        except TreeOrganizationProfileStoreError as exc:
            QMessageBox.warning(self.app, "Tree Profiles Unavailable", str(exc))
            return
        if self.active_profile and self.active_profile.id == profile_id:
            self.disable_profile()
        else:
            self._refresh_editor()
            self._sync_profile_options()

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
            from gui.core.tree_filter_options import custom_tree_filter_options

            self.app.library_tab.tree_model.set_custom_tree_profile(profile)
            if hasattr(self.app.library_tab, "set_custom_tree_filter_options"):
                self.app.library_tab.set_custom_tree_filter_options(custom_tree_filter_options(profile))
            self.app.library_tab.set_tree_organization_state(bool(profile), profile.name if profile else "")
            self._sync_profile_options()
        if refresh and getattr(self.app, "view_controller", None):
            self.app.view_controller.update_library_views(tree_delay_ms=0)

    def _profile_from_current_tree(self, records: list) -> TreeOrganizationProfile:
        now = utc_now_iso()
        library_tab = getattr(self.app, "library_tab", None)
        store = getattr(self.app, "session_store", None)
        tree_model = getattr(library_tab, "tree_model", None)
        if store is not None:
            from gui.core.tree_organization_defaults import build_default_tree_nodes_from_group_values

            values = store.default_tree_group_values(
                confidence_floor=float(getattr(tree_model, "confidence_floor", 0.0)),
                confidence_filter_enabled=bool(getattr(tree_model, "confidence_filter_enabled", True)),
            )
            nodes = build_default_tree_nodes_from_group_values(values, collapse_residual_other=False)
        else:
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
            records = self._record_source()
            profile = self.active_profile or self._editable_profile_from_default(records)
            editor.reload(self.repository.list_profiles(), profile, records)
        except TreeOrganizationProfileStoreError as exc:
            QMessageBox.warning(self.app, "Tree Profiles Unavailable", str(exc))
        except RuntimeError:
            self.editor_widget = None

    def _record_source(self):
        model = getattr(self.app, "model", None)
        return getattr(model, "records", []) or []

    def _clear_profile_projection(self, profile_id: str) -> None:
        store = getattr(self.app, "session_store", None)
        if store is not None and hasattr(store, "clear_custom_tree_projections"):
            store.clear_custom_tree_projections(profile_id)

    def _retain_profile_projection(self, profile_id: str, signature: str) -> None:
        store = getattr(self.app, "session_store", None)
        if store is not None and hasattr(store, "clear_custom_tree_projections"):
            store.clear_custom_tree_projections(profile_id, keep_signature=signature)

    def _ensure_profile_projection(self, profile: TreeOrganizationProfile) -> str:
        store = getattr(self.app, "session_store", None)
        tree_model = getattr(getattr(self.app, "library_tab", None), "tree_model", None)
        if store is None or tree_model is None:
            return ""
        levels = list(tree_model._active_tree_levels())
        return store.ensure_custom_tree_projection(
            profile,
            levels,
            confidence_floor=float(getattr(tree_model, "confidence_floor", 0.0)),
            confidence_filter_enabled=bool(getattr(tree_model, "confidence_filter_enabled", True)),
        )

    def _has_profile_projection(self, profile: TreeOrganizationProfile) -> bool:
        store = getattr(self.app, "session_store", None)
        tree_model = getattr(getattr(self.app, "library_tab", None), "tree_model", None)
        if store is None or tree_model is None:
            return True
        signature = store.custom_tree_projection_signature(
            profile,
            list(tree_model._active_tree_levels()),
            confidence_floor=float(getattr(tree_model, "confidence_floor", 0.0)),
            confidence_filter_enabled=bool(getattr(tree_model, "confidence_filter_enabled", True)),
        )
        return store.has_custom_tree_projection(profile.id, signature)

    def _sync_profile_options(self) -> None:
        tab = getattr(self.app, "library_tab", None)
        if tab is None or not hasattr(tab, "set_tree_organization_options"):
            return
        try:
            profiles = self.repository.list_profiles()
        except TreeOrganizationProfileStoreError:
            profiles = []
        tab.set_tree_organization_options(
            profiles,
            self.active_profile.id if self.active_profile is not None else "",
        )

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
