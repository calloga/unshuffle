from __future__ import annotations

import uuid
from dataclasses import replace

from PySide6.QtWidgets import QMessageBox, QWidget

from gui.core.tree_organization_defaults import build_default_tree_nodes, make_default_shaped_profile
from unshuffle.logic.tree_organization import TreeOrganizationNode, TreeOrganizationProfile


class TreeOrganizationMutationMixin:
    def _build_default_tree_nodes(self, records: list, library_tab=None) -> list[TreeOrganizationNode]:
        return build_default_tree_nodes(records, library_tab, collapse_residual_other=True)

    def _make_default_shaped_profile(self, profile_id: str, name: str, records: list, library_tab=None) -> TreeOrganizationProfile:
        return make_default_shaped_profile(
            profile_id,
            name,
            records,
            library_tab,
            collapse_residual_other=True,
        )
    def _add_child_node(self) -> None:
        self._insert_node(self._selected_id or self._profile.root_node_id)

    def _insert_node(self, parent_id: str, row: int | None = None) -> None:
        parent = self._node_by_id().get(parent_id)
        if parent is None or self._is_utility_system_node(parent):
            return
        if hasattr(self, "_can_add_child_to") and not self._can_add_child_to(parent_id):
            return
        self._push_undo_state()
        siblings = self._children_by_parent().get(parent_id, [])
        insert_row = len(siblings) if row is None else max(0, min(row, len(siblings)))
        node = TreeOrganizationNode(
            id=f"node_{uuid.uuid4().hex[:8]}",
            parent_id=parent_id,
            name="New Folder",
            filter_query=None,
            node_type="custom",
            sort_order=insert_row + 1,
            enabled=True,
        )
        self._nodes.append(node)
        self._renumber_siblings(parent_id, preferred_order=[*siblings[:insert_row], node, *siblings[insert_row:]])
        self._after_structure_change(node.id)

    def _update_selected(self) -> None:
        if self._syncing_fields:
            return
        node = self._selected_node()
        if node is None or self._is_read_only_node(node):
            return
        updated = replace(
            node,
            name=self.node_name.text().strip() or "Folder",
            filter_query=self.node_filter.text().strip() or None,
            node_type=self.node_type.currentText(),
            enabled=True,
            hide_subbranches=bool(getattr(self, "node_hide_subbranches", None) and self.node_hide_subbranches.isChecked()),
        )
        if updated == node:
            return
        self._push_undo_state()
        self._replace_node(updated)
        self._after_structure_change(updated.id)

    def _remove_selected(self) -> None:
        self._remove_node_by_id(self._selected_id)

    def _remove_node_by_id(self, node_id: str) -> None:
        node = self._node_by_id().get(node_id)
        if node is None:
            return
        if self._is_read_only_node(node):
            QMessageBox.information(self if isinstance(self, QWidget) else None, "System Folder Locked", "This system folder cannot be changed.")
            return
        self._push_undo_state()
        remove_ids = self._descendant_ids(node.id) | {node.id}
        parent_id = node.parent_id or self._profile.root_node_id
        self._nodes = [item for item in self._nodes if item.id not in remove_ids]
        self._rebuild_node_indexes()
        self._renumber_siblings(parent_id)
        self._after_structure_change(parent_id)

    def _move_node(self, node_id: str, parent_id: str, row: int) -> None:
        if not self._can_move_node(node_id, parent_id):
            return
        node = self._node_by_id().get(node_id)
        if node is None:
            return
        old_parent_id = node.parent_id or self._profile.root_node_id
        new_parent_id = parent_id or self._profile.root_node_id
        siblings = [item for item in self._children_by_parent().get(new_parent_id, []) if item.id != node_id]
        insert_row = len(siblings) if row < 0 else max(0, min(row, len(siblings)))
        if old_parent_id == new_parent_id and node.sort_order == insert_row + 1:
            return
        self._push_undo_state()
        self._replace_node(replace(node, parent_id=new_parent_id))
        self._renumber_siblings(old_parent_id)
        moved = self._node_by_id()[node_id]
        self._renumber_siblings(new_parent_id, preferred_order=[*siblings[:insert_row], moved, *siblings[insert_row:]])
        self._after_structure_change(node_id)

    def _can_move_node(self, node_id: str, parent_id: str) -> bool:
        node = self._node_by_id().get(node_id)
        parent = self._node_by_id().get(parent_id)
        if node is None or parent is None:
            return False
        if self._is_read_only_node(node):
            return False
        if self._is_utility_system_node(parent):
            return False
        if node_id == parent_id:
            return False
        return parent_id not in self._descendant_ids(node_id)

    def _is_read_only_node(self, node: TreeOrganizationNode) -> bool:
        return node.id == self._profile.root_node_id or self._is_utility_system_node(node)

    def _is_utility_system_node(self, node: TreeOrganizationNode) -> bool:
        return (
            node.parent_id == self._profile.root_node_id
            and node.node_type == "system"
            and node.name == "Utility"
            and node.filter_query == 'type:"Non-Audio Assets"'
        )

    def _forbidden_drop_parent_ids(self, node_id: str) -> set[str]:
        return {node_id} | self._descendant_ids(node_id)

    def _delete_profile(self) -> None:
        self._delete_profile_by_id(self._profile.id)

    def _delete_profile_by_id(self, profile_id: str) -> None:
        parent = self if isinstance(self, QWidget) else None
        if QMessageBox.question(parent, "Delete Profile", "Delete this tree organization profile?") == QMessageBox.Yes:
            self.profileDeleted.emit(profile_id)
            if not self._embedded:
                self.accept()

    def _disable_custom_tree(self) -> None:
        self.profileDisabled.emit()

    def _reset_custom_tree(self) -> None:
        self._push_undo_state()
        app = self.parent() if hasattr(self, "parent") else None
        library_tab = getattr(app, "library_tab", None) if app else None
        default_nodes = self._build_default_tree_nodes(self._records, library_tab)

        self._nodes = default_nodes
        self._rebuild_node_indexes()
        self._after_structure_change("root")

        is_editing = hasattr(self, "page_stack") and self.page_stack.currentWidget() == self.editor_page
        if not is_editing:
            profile = self._profile_from_ui()
            self._profile = profile
            self.profileSaved.emit(profile)
            self._load_profiles()

    def _new_profile(self) -> None:
        app = self.parent() if hasattr(self, "parent") else None
        library_tab = getattr(app, "library_tab", None) if app else None
        new_profile = self._make_default_shaped_profile(f"profile_{uuid.uuid4().hex[:12]}", "Custom Tree", self._records, library_tab)
        self._load_profile(new_profile)

    def _state_snapshot(self) -> tuple[list[TreeOrganizationNode], str, str]:
        return (list(self._nodes), self._selected_id, self.profile_name.text())

    def _push_undo_state(self) -> None:
        self._undo_states.append(self._state_snapshot())
        self._redo_states.clear()
        self._refresh_undo_buttons()

    def _restore_state(self, state: tuple[list[TreeOrganizationNode], str, str]) -> None:
        nodes, selected_id, profile_name = state
        self._nodes = list(nodes)
        self.profile_name.setText(profile_name)
        self._rebuild_node_indexes()
        if selected_id not in self._node_lookup:
            selected_id = self._profile.root_node_id
        self._render_tree()
        self._select_node(selected_id)
        self._validate()
        self._schedule_count_refresh()
        self._refresh_undo_buttons()

    def _undo_tree_edit(self) -> None:
        if not self._undo_states:
            return
        self._redo_states.append(self._state_snapshot())
        self._restore_state(self._undo_states.pop())

    def _redo_tree_edit(self) -> None:
        if not self._redo_states:
            return
        self._undo_states.append(self._state_snapshot())
        self._restore_state(self._redo_states.pop())

    def _refresh_undo_buttons(self) -> None:
        if hasattr(self, "btn_undo"):
            self.btn_undo.setEnabled(bool(self._undo_states))
        if hasattr(self, "btn_redo"):
            self.btn_redo.setEnabled(bool(self._redo_states))

