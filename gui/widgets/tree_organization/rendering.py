from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QIcon, QStandardItem

from unshuffle.logic.tree_organization import TreeOrganizationNode

from ...utils.constants import DELETE_ICON
from ...utils.styles import ColorPalette, make_qcolor, scaled_px
from .constants import ACTION_KIND_ROLE, ADD_PARENT_ID_ROLE, NODE_ID_ROLE


class TreeOrganizationRenderingMixin:
    def _render_tree(self) -> None:
        had_rendered_tree = bool(self._tree_items)
        expanded_ids = self._expanded_node_ids()
        scroll_value = self.tree.verticalScrollBar().value()
        self.tree_model.removeRows(0, self.tree_model.rowCount())
        self._tree_items.clear()
        children = self._children_by_parent()
        root = self._node_by_id().get(self._profile.root_node_id)
        if root is None:
            return
        self._append_tree_node(self.tree_model.invisibleRootItem(), root, children, set())
        if had_rendered_tree:
            self._restore_expanded_nodes(expanded_ids or {self._profile.root_node_id})
        else:
            root_item = self._tree_items.get(self._profile.root_node_id)
            if root_item is not None:
                self.tree.setExpanded(self.tree_model.indexFromItem(root_item), True)
        self.tree.setColumnWidth(2, scaled_px(80))
        self.tree.verticalScrollBar().setValue(min(scroll_value, self.tree.verticalScrollBar().maximum()))

    def _expanded_node_ids(self) -> set[str]:
        expanded = set()
        for node_id, item in self._tree_items.items():
            index = self.tree_model.indexFromItem(item)
            if index.isValid() and self.tree.isExpanded(index):
                expanded.add(node_id)
        return expanded

    def _restore_expanded_nodes(self, node_ids: set[str]) -> None:
        for node_id in node_ids:
            item = self._tree_items.get(node_id)
            if item is not None:
                self.tree.setExpanded(self.tree_model.indexFromItem(item), True)

    def _append_tree_node(
        self,
        parent_item: QStandardItem,
        node: TreeOrganizationNode,
        children: dict[str, list[TreeOrganizationNode]],
        seen: set[str],
    ) -> None:
        if node.id in seen:
            return
        seen = {*seen, node.id}
        read_only = self._is_read_only_node(node)
        name_item = QStandardItem(node.name)
        name_item.setEditable(False)
        name_item.setIcon(self._folder_icon)
        name_item.setData(node.id, NODE_ID_ROLE)
        count = self._preview_count(node)
        name_item.setToolTip(f"{node.name}\n{count} routed item{'s' if count != 1 else ''}")
        filter_item = QStandardItem(node.filter_query or "")
        filter_item.setEditable(False)
        filter_item.setData(node.id, NODE_ID_ROLE)
        action_item = QStandardItem("")
        action_item.setEditable(False)
        action_item.setData(node.id, NODE_ID_ROLE)
        action_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
        action_item.setToolTip("" if read_only else "Remove folder")
        if not read_only:
            action_item.setData("delete", ACTION_KIND_ROLE)
            action_item.setIcon(self._icon_delete_danger)
            action_item.setForeground(QBrush(make_qcolor(ColorPalette.DANGER_LIGHT)))
        parent_item.appendRow([name_item, filter_item, action_item])
        self._tree_items[node.id] = name_item
        for child in children.get(node.id, []):
            self._append_tree_node(name_item, child, children, seen)
        if not self._is_utility_system_node(node):
            self._append_add_child_row(name_item, node.id)

    def _append_add_child_row(self, parent_item: QStandardItem, parent_id: str) -> None:
        name_item = QStandardItem("+  Add child")
        name_item.setEditable(False)
        name_item.setData(parent_id, ADD_PARENT_ID_ROLE)
        name_item.setData("add_child", ACTION_KIND_ROLE)
        name_item.setToolTip("Add child folder")
        name_item.setForeground(QBrush(make_qcolor(ColorPalette.PRIMARY_BRIGHT)))
        filter_item = QStandardItem("")
        filter_item.setEditable(False)
        filter_item.setData(parent_id, ADD_PARENT_ID_ROLE)
        action_item = QStandardItem("")
        action_item.setEditable(False)
        action_item.setData(parent_id, ADD_PARENT_ID_ROLE)
        parent_item.appendRow([name_item, filter_item, action_item])

    def _on_tree_current_changed(self, current, _previous) -> None:
        node_id = current.siblingAtColumn(0).data(NODE_ID_ROLE)
        if node_id:
            self._select_node(str(node_id), sync_tree=False)

    def _select_node(self, node_id: str, *, sync_tree: bool = True) -> None:
        if hasattr(self, "page_stack") and hasattr(self, "editor_page"):
            self.page_stack.setCurrentWidget(self.editor_page)
        self._selected_id = node_id
        if sync_tree:
            item = self._tree_items.get(node_id)
            if item is not None:
                index = self.tree_model.indexFromItem(item)
                self.tree.setCurrentIndex(index)
                self.tree.scrollTo(index)
        self._sync_fields_from_selection()

    def _selected_node(self) -> TreeOrganizationNode | None:
        return self._node_by_id().get(self._selected_id)

    def _sync_fields_from_selection(self) -> None:
        node = self._selected_node()
        if node is None:
            self.detail_panel.hide()
            return
        self._syncing_fields = True
        self.detail_panel.show()
        count = self._preview_count(node)
        self.folder_title_label.setText(node.name)
        self.selected_label.setText(f"{count} routed item{'s' if count != 1 else ''}")
        self.node_name.setText(node.name)
        self.node_filter.setText(node.filter_query or "")
        if hasattr(self, "node_hide_subbranches"):
            self.node_hide_subbranches.setChecked(bool(getattr(node, "hide_subbranches", False)))
        self.node_type.setCurrentText(node.node_type)
        read_only = self._is_read_only_node(node)
        self.node_name.setEnabled(not read_only)
        self.node_filter.setEnabled(not read_only)
        if hasattr(self, "node_hide_subbranches"):
            self.node_hide_subbranches.setEnabled(not read_only)
        self.node_type.setEnabled(not read_only)
        self.btn_update.setEnabled(not read_only)
        self.btn_remove.setEnabled(not read_only)
        if hasattr(self, "node_actions"):
            self.node_actions.setVisible(not read_only)
        self._syncing_fields = False
        self._refresh_detail_action_state()

    def _focus_details(self, node_id: str) -> None:
        self._select_node(node_id)
        if self.node_name.isEnabled():
            self.node_name.setFocus()
            self.node_name.selectAll()

    def _selected_detail_has_unsaved_changes(self) -> bool:
        if getattr(self, "_syncing_fields", False):
            return False
        node = self._selected_node()
        if node is None or self._is_read_only_node(node):
            return False
        return (
            (self.node_name.text().strip() or "Folder") != node.name
            or (self.node_filter.text().strip() or None) != node.filter_query
            or self.node_type.currentText() != node.node_type
            or bool(
                getattr(self, "node_hide_subbranches", None)
                and self.node_hide_subbranches.isChecked()
            )
            != bool(getattr(node, "hide_subbranches", False))
        )

    def _can_add_child_to(self, parent_id: str | None) -> bool:
        parent = self._node_by_id().get(parent_id or "")
        if parent is None or self._is_utility_system_node(parent):
            return False
        if parent.id == self._selected_id and self._selected_detail_has_unsaved_changes():
            return False
        return True

    def _refresh_detail_action_state(self) -> None:
        if not hasattr(self, "btn_child"):
            return
        can_add = self._can_add_child_to(self._selected_id or self._profile.root_node_id)
        self.btn_child.setEnabled(can_add)
        if can_add:
            self.btn_child.setToolTip("Add child folder")
        else:
            self.btn_child.setToolTip("Update this folder before adding a child.")

