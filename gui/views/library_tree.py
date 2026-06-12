from PySide6.QtCore import QTimer
import os
import json
import logging
import re
import shutil
import tempfile
from pathlib import Path
from PySide6.QtWidgets import QStyledItemDelegate, QStyle, QTreeView, QAbstractItemView, QApplication, QLabel, QMessageBox
from PySide6.QtCore import Qt, Signal, QUrl, QMimeData, QPoint, QSortFilterProxyModel, QItemSelectionModel
from PySide6.QtGui import QDrag, QPixmap
from unshuffle.core.constants import MAX_SYNC_FOLDER_EXPORT_RECORDS
from gui.models.library_tree import (
    FIELDS_ROLE,
    NODE_TYPE_ROLE,
    RAW_NAME_ROLE,
    READ_ONLY_ROLE,
    RECORDS_ROLE,
    SEARCH_TEXT_ROLE,
    SEMANTIC_FIELD_NAMES,
    SOURCE_NODE_ID_ROLE,
    SOURCE_NODE_TYPE_ROLE,
)
from gui.utils.styles import (
    apply_style,
    menu_style,
    tree_drop_hint_style,
    tree_header_style,
    tree_view_style,
)
from gui.widgets.library_filters import category_is_valid_for_audio_type, fallback_category_for_audio_type

TREE_RECORD_MIME = "application/x-unshuffle-record-paths"


class NoFocusRectDelegate(QStyledItemDelegate):
    """Default tree delegate without the platform focus rectangle."""

    def paint(self, painter, option, index):
        option.state &= ~QStyle.State_HasFocus
        super().paint(painter, option, index)


class LibraryTreeView(QTreeView):
    """Tree view displaying and reorganizing staged Library records."""

    MAX_SYNC_FOLDER_EXPORT_RECORDS = MAX_SYNC_FOLDER_EXPORT_RECORDS

    play_requested = Signal(object)
    similarity_requested = Signal(object)
    exclude_requested = Signal(object)
    preserve_requested = Signal(object, object)
    unpreserve_requested = Signal(object, object)
    reorganization_requested = Signal(object, object)
    category_change_requested = Signal(object, str)
    tags_edit_requested = Signal(object, object, object)
    quick_filter_requested = Signal(str, str)
    open_explorer_requested = Signal(object)
    focus_search_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHeaderHidden(False)
        self.setItemDelegate(NoFocusRectDelegate(self))
        self.setIndentation(20)
        self.setUniformRowHeights(True)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(False)
        self.setDragDropMode(QAbstractItemView.DragDrop)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setAutoScroll(True)
        self.setAutoScrollMargin(32)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)
        self.drag_start_pos = QPoint()
        self.drop_hint = QLabel(self.viewport())
        self.drop_hint.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.drop_hint.hide()
        self.doubleClicked.connect(self._on_double_clicked)
        self.expanded.connect(self._populate_on_expand)
        self._export_temp_dirs = []
        self._drag_in_progress = False
        self._internal_drag_active = False
        self._read_only_discovery = False
        self.header().setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._apply_theme_styles()
        self.setColumnWidth(0, 800)

    def _apply_theme_styles(self) -> None:
        apply_style(self.drop_hint, tree_drop_hint_style())
        apply_style(self, tree_view_style())
        apply_style(self.header(), tree_header_style())

    def refresh_theme(self) -> None:
        self._apply_theme_styles()
        self.viewport().update()
    def _apply_header_settings(self):
        from PySide6.QtWidgets import QHeaderView
        self.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.header().setStretchLastSection(False)
        self.header().setMinimumSectionSize(150)
        if self.model() and self.model().columnCount() > 1:
            self.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)

    def set_read_only_discovery(self, enabled: bool):
        self._read_only_discovery = enabled
        self.setAcceptDrops(not enabled)
        self.setDropIndicatorShown(False)
        self.setDragDropMode(QAbstractItemView.DragOnly if enabled else QAbstractItemView.DragDrop)

    def snapshot_state(self):
        model = self._source_model()
        if not model: return {"expanded": set(), "selected": set(), "current": None}
        expanded, selected = set(), set()
        
        for view_idx in self.selectedIndexes():
            if view_idx.column() == 0:
                source_idx = self._source_index(view_idx)
                if source_idx.isValid():
                    selected.add(model.node_path(source_idx))
                    
        current_index = self.currentIndex()
        current_path = None
        if current_index.isValid():
            source_idx = self._source_index(current_index)
            if source_idx.isValid():
                current_path = model.node_path(source_idx)

        def walk(parent):
            for row in range(model.rowCount(parent)):
                idx = model.index(row, 0, parent)
                view_idx = self._to_view_index(idx)
                if view_idx.isValid() and self.isExpanded(view_idx):
                    expanded.add(model.node_path(idx))
                    walk(idx)
                    
        walk(model.index(-1, -1))
        return {"expanded": expanded, "selected": selected, "current": current_path}

    def restore_state(self, state):
        model = self._source_model()
        if not model: return
        selection = self.selectionModel()
        if selection: selection.clearSelection()
        for path in state.get("expanded", set()):
            idx = model.index_for_path(path)
            if idx is not None:
                view_idx = self._to_view_index(idx)
                if view_idx.isValid(): self.setExpanded(view_idx, True)
        for path in state.get("selected", set()):
            idx = model.index_for_path(path)
            if idx is not None and selection:
                view_idx = self._to_view_index(idx)
                if view_idx.isValid(): selection.select(view_idx, QItemSelectionModel.Select | QItemSelectionModel.Rows)
        current_path = state.get("current")
        if current_path:
            idx = model.index_for_path(current_path)
            if idx is not None:
                view_idx = self._to_view_index(idx)
                if view_idx.isValid(): self.setCurrentIndex(view_idx)

    def keyPressEvent(self, event):
        recs = self._selected_records()
        if event.key() == Qt.Key_Space:
            if recs: self.play_requested.emit(recs[0])
            event.accept(); return
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            idx = self.currentIndex()
            if idx.isValid() and self.model().hasChildren(idx): self.setExpanded(idx, not self.isExpanded(idx))
            elif recs: self.play_requested.emit(recs[0])
            event.accept(); return
        if event.key() == Qt.Key_F:
            if event.modifiers() & Qt.ControlModifier:
                if event.modifiers() & Qt.ShiftModifier:
                    query = self._quick_filter_query_for_index(self.currentIndex())
                    if query: self.quick_filter_requested.emit(query, self._compose_mode(event.modifiers()))
                else: self.focus_search_requested.emit()
                event.accept(); return
            if recs: self.similarity_requested.emit(recs[0])
            event.accept(); return
        if event.key() == Qt.Key_E and event.modifiers() & Qt.ControlModifier:
            if recs: self.open_explorer_requested.emit(recs[0])
            event.accept(); return
        super().keyPressEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton: self.drag_start_pos = event.position().toPoint(); self._drag_in_progress = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        try:
            if not self._drag_in_progress and event.buttons() & Qt.LeftButton:
                if (event.position().toPoint() - self.drag_start_pos).manhattanLength() > QApplication.startDragDistance():
                    self._drag_in_progress = True
                    try: self.startDrag(Qt.CopyAction | Qt.MoveAction)
                    finally: self._drag_in_progress = False
                    return
        except Exception: self._drag_in_progress = False; logging.exception("Tree drag gesture failed.")
        super().mouseMoveEvent(event)

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat(TREE_RECORD_MIME): event.acceptProposedAction(); return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasFormat(TREE_RECORD_MIME):
            pos = event.position().toPoint()
            target_index = self._drop_index_at(pos)
            
  
            target_idx = self._source_index(target_index)
            if target_idx.isValid():
                dragged_nodes = self._source_nodes_from_mime(event.mimeData())
                if any(node.get("index") and self._is_ancestor(node["index"], target_idx) for node in dragged_nodes):
                    self._hide_drop_hint()
                    event.ignore()
                    return

            target = self._drop_target_fields(target_index)
            if target:
                self._show_drop_hint(pos, target)
                event.setDropAction(Qt.MoveAction)
                event.accept()
                self.viewport().update()
            else:
                self._hide_drop_hint()
                event.ignore()
                self.viewport().update()
        else:
            super().dragMoveEvent(event)

    def dragLeaveEvent(self, event):
        self._hide_drop_hint()
        if self._internal_drag_active:
            try: QDrag.cancel()
            except Exception: logging.exception("Failed to cancel internal tree drag.")
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        if self._read_only_discovery: self._hide_drop_hint(); event.ignore(); return
        if not event.mimeData().hasFormat(TREE_RECORD_MIME): super().dropEvent(event); return
        target_index = self._drop_index_at(event.position().toPoint())
        
   
        target_idx = self._source_index(target_index)
        if target_idx.isValid():
            dragged_nodes = self._source_nodes_from_mime(event.mimeData())
            if any(node.get("index") and self._is_ancestor(node["index"], target_idx) for node in dragged_nodes):
                self._hide_drop_hint()
                event.ignore()
                return

        target_fields, target_node_type = self._drop_target_fields(target_index), self._drop_target_node_type(target_index)
        records = self._records_from_mime(event.mimeData())
        if target_fields and records:
            source_nodes = self._source_nodes_from_mime(event.mimeData())
            applied_fields = dict(target_fields); applied_fields["__collision_scope"] = "direct"
            source_label = "selection"
            if len(source_nodes) == 1:
                source_node = source_nodes[0]
                source_label = str(source_node.get("name") or "selection")
                source_node_type = str(source_node.get("node_type") or "")
                if source_node_type == "pack" and target_node_type == "pack":
                    sn, tn = str(source_node.get("name") or "").strip(), str(target_fields.get("pack") or "").strip()
                    if (sn and tn and sn == tn) or not self._confirm_pack_merge(sn, tn): self._hide_drop_hint(); event.ignore(); return
                if not (source_node_type == "pack" and target_node_type == "pack"):
                    applied_fields = self._bucket_aware_drop_fields(source_node, target_fields, target_node_type)
                    applied_fields["__collision_scope"] = "direct"
            if not self._confirm_folder_move_if_needed(source_label, self._drop_target_label(applied_fields), records, applied_fields):
                self._hide_drop_hint(); event.ignore(); return
            self._hide_drop_hint(); self.reorganization_requested.emit(records, applied_fields); event.setDropAction(Qt.MoveAction); event.accept(); return
        self._hide_drop_hint(); event.ignore()

    def startDrag(self, supportedActions):
        selected_indexes = self._selected_source_indexes()
        if not selected_indexes: return
        mime = QMimeData(); mime.setData(TREE_RECORD_MIME, self._encode_source_indexes(selected_indexes))
        modifiers = QApplication.keyboardModifiers()
        export_drag = self._read_only_discovery or bool(modifiers & Qt.ControlModifier)
        if export_drag:
            folder_nodes = [idx for idx in selected_indexes if idx.data(NODE_TYPE_ROLE) != "file"]
            if folder_nodes and not self._confirm_sync_folder_export(folder_nodes): return
            try: export_urls = self._build_export_urls(selected_indexes)
            except Exception: logging.exception("Tree export failed."); export_urls = []
            if export_urls: mime.setUrls(export_urls)
            else: mime.setUrls([QUrl.fromLocalFile(str(r.source_path.absolute())) for r in self._records_for_source_indexes(selected_indexes)])
        drag = QDrag(self); drag.setMimeData(mime)
        drag_pixmap = QPixmap(1, 1)
        drag_pixmap.fill(Qt.transparent)
        drag.setPixmap(drag_pixmap)
        try:
            if export_drag: drag.exec(Qt.CopyAction | Qt.MoveAction, Qt.CopyAction)
            else:
                self._internal_drag_active = True
                try: drag.exec(Qt.MoveAction, Qt.MoveAction)
                finally: self._internal_drag_active = False
        except Exception: self._internal_drag_active = False; logging.exception("Tree drag failed.")

    def _confirm_sync_folder_export(self, folder_nodes) -> bool:
        record_count = len(self._records_for_source_indexes(folder_nodes))
        if record_count <= self.MAX_SYNC_FOLDER_EXPORT_RECORDS: return True
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.warning(self, "Export Too Large", f"Folder export limited to {self.MAX_SYNC_FOLDER_EXPORT_RECORDS} files. Current: {record_count}.")
        return False

    def _build_export_urls(self, selected_indexes):
        return [QUrl.fromLocalFile(str(r.source_path.absolute())) for r in self._records_for_source_indexes(selected_indexes)]

    def _on_double_clicked(self, index):
        rec = self._source_index(index).data(Qt.UserRole)
        if rec: self.play_requested.emit(rec)

    def setModel(self, model):
        old_model = self.model()
        if old_model:
            try:
                old_model.modelReset.disconnect(self._apply_header_settings)
            except (TypeError, RuntimeError):
                logging.exception("Failed to disconnect old model")
        if model:
            model.rebuildFinished.connect(self._apply_header_settings)
        super().setModel(model)
        from PySide6.QtWidgets import QHeaderView
        header = self.header(); header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setStretchLastSection(False)
        if model and model.columnCount() > 1: header.setSectionResizeMode(1, QHeaderView.ResizeToContents)

    def _show_context_menu(self, pos):
        index = self.indexAt(pos)
        if not index.isValid(): return
        source_index = self._source_index(index); rec = source_index.data(Qt.UserRole)
        target_recs = self._records_for_context(source_index, index); scope = self._scope_label(target_recs)
        from PySide6.QtWidgets import QMenu; from PySide6.QtGui import QAction; from unshuffle.core.constants import CATEGORIES
        menu = QMenu(self); apply_style(menu, menu_style())
        if target_recs:
            act_copy = QAction("Copy", self)
            act_copy.triggered.connect(lambda chk=False, rs=target_recs: self._copy_records_to_clipboard(rs))
            menu.addAction(act_copy)
            act_copy_path = QAction("Copy as Path", self)
            act_copy_path.triggered.connect(lambda chk=False, rs=target_recs: self._copy_record_paths_to_clipboard(rs))
            menu.addAction(act_copy_path)
            menu.addSeparator()
        if rec:
            act_play = QAction("Play Preview (Space)", self); act_play.triggered.connect(lambda chk=False, r=rec: self.play_requested.emit(r)); menu.addAction(act_play)
            act_sim = QAction("Similarity Explorer", self); act_sim.triggered.connect(lambda chk=False, r=rec: self.similarity_requested.emit(r)); menu.addAction(act_sim)
            act_explore = QAction("Show in Explorer", self); act_explore.triggered.connect(lambda chk=False, r=rec: self.open_explorer_requested.emit(r)); menu.addAction(act_explore)
            menu.addSeparator()
        qf_query = self._quick_filter_query_for_index(index)
        if qf_query:
            act = QAction(f"Filter by This {self._filter_level_label_for_index(source_index)}", self)
            act.triggered.connect(lambda chk=False, q=qf_query: self.quick_filter_requested.emit(q, self._compose_mode(QApplication.keyboardModifiers())))
            menu.addAction(act); menu.addSeparator()
        read_only_utility = self._is_read_only_utility_index(source_index)
        if not self._read_only_discovery and not read_only_utility:
            cat_menu = menu.addMenu(f"Set Category for {scope}"); cur_cat = self._shared_attr(target_recs, "category")
            for cat in CATEGORIES:
                act = QAction(cat, self); act.setCheckable(True)
                if cur_cat == cat: act.setChecked(True)
                act.triggered.connect(lambda chk=False, c=cat, rs=target_recs: self._bulk_categorize(rs, c))
                cat_menu.addAction(act)
        if not self._read_only_discovery and not read_only_utility and target_recs:
            menu.addSeparator()
            act_ren = QAction(f"Rename Pack for {scope}", self); act_ren.triggered.connect(lambda chk=False, rs=target_recs: self._prompt_rename_pack(rs)); menu.addAction(act_ren)
            act_tag = QAction(f"Edit Tags for {scope}", self); act_tag.triggered.connect(lambda chk=False, rs=target_recs: self._prompt_edit_tags(rs)); menu.addAction(act_tag)
            menu.addSeparator()
            act_del = QAction(f"Delete from Disk", self)
            act_del.triggered.connect(lambda chk=False, rs=target_recs: self.exclude_requested.emit(rs))
            menu.addAction(act_del)
            
            node_type = source_index.data(NODE_TYPE_ROLE)
            common_path = self._find_common_parent(target_recs)
            if node_type == "pack" and common_path:
                menu.addSeparator()
                from unshuffle.core.constants import PRESERVED_MARKER
                from pathlib import Path as _Path

                _preserved_dir = None
                for _r in target_recs:
                    _sp = _Path(str(_r.source_path))

                    if _sp.is_dir() and (_sp / PRESERVED_MARKER).exists():
                        _preserved_dir = _sp
                        break

                    if (_sp.parent / PRESERVED_MARKER).exists():
                        _preserved_dir = _sp.parent
                        break
                if _preserved_dir:
                    act_ho = QAction("Un-preserve this pack", self)
                    act_ho.triggered.connect(lambda chk=False, rs=list(self._unique_records(target_recs)), p=str(_preserved_dir): self.unpreserve_requested.emit(rs, p))
                else:
                    act_ho = QAction("Preserve this pack", self)
                    act_ho.triggered.connect(lambda chk=False, rs=list(self._unique_records(target_recs)), p=str(common_path): self.preserve_requested.emit(rs, p))
                menu.addAction(act_ho)
        menu.exec(self.viewport().mapToGlobal(pos))

    def _get_selected_records(self, source_index):
        recs = []; rec = source_index.data(Qt.UserRole)
        if rec: recs.append(rec)
        else: self._collect_records_recursive(source_index, recs)
        return recs

    def _collect_records_recursive(self, source_index, out_list):
        model = self._source_model()
        direct = source_index.data(RECORDS_ROLE) if source_index.isValid() else None
        if direct: out_list.extend(direct); return
        for i in range(model.rowCount(source_index)):
            child = model.index(i, 0, source_index); rec = child.data(Qt.UserRole)
            if rec: out_list.append(rec)
            else: self._collect_records_recursive(child, out_list)

    def _bulk_categorize(self, records, category): self.reorganization_requested.emit(records, {"category": category})

    def _copy_records_to_clipboard(self, records):
        lines = []
        for rec in self._unique_records(records):
            lines.append("\t".join([
                str(getattr(rec, "pack", "") or ""),
                str(getattr(rec, "source_path", Path("")).name or ""),
                str(getattr(rec, "category", "") or ""),
                str(getattr(rec, "subcategory", "") or ""),
                ", ".join(str(tag) for tag in (getattr(rec, "tags", []) or [])),
                str(getattr(rec, "confidence", "") or ""),
            ]))
        QApplication.clipboard().setText("\n".join(lines))

    def _copy_record_paths_to_clipboard(self, records):
        paths = [str(getattr(rec, "source_path", "") or "") for rec in self._unique_records(records)]
        QApplication.clipboard().setText("\n".join(paths))

    def _prompt_rename_pack(self, records):
        from PySide6.QtWidgets import QInputDialog
        sp = self._shared_attr(records, "pack") or ""
        np, ok = QInputDialog.getText(self, "Rename Pack", "New pack name:", text=sp)
        if ok and np.strip(): self.reorganization_requested.emit(records, {"pack": np.strip(), "__collision_scope": "direct"})

    def _prompt_edit_tags(self, records):
        from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QLineEdit, QVBoxLayout; from unshuffle.core import parse_tags
        dialog = QDialog(self); dialog.setWindowTitle("Edit Tags"); layout = QVBoxLayout(dialog)
        form = QFormLayout(); ea, er = QLineEdit(), QLineEdit(); form.addRow("Append:", ea); form.addRow("Remove:", er); layout.addLayout(form)
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel); btns.accepted.connect(dialog.accept); btns.rejected.connect(dialog.reject); layout.addWidget(btns)
        if dialog.exec() == QDialog.Accepted:
            at, rt = parse_tags(ea.text()), parse_tags(er.text())
            if at or rt: self.tags_edit_requested.emit(records, at, rt)

    def _populate_on_expand(self, view_index):
        model = self._source_model(); source_index = self._source_index(view_index)
        if hasattr(model, "populate_index"): model.populate_index(source_index)

    def _selected_records(self): return self._records_for_source_indexes(self._selected_source_indexes())

    def _selected_source_indexes(self):
        selected, seen = [], set(); model = self._source_model()
        for index in self.selectedIndexes():
            if index.column() != 0: continue
            si = self._source_index(index); key = model.node_path(si)
            if key not in seen: seen.add(key); selected.append(si)
        return selected

    def _records_for_context(self, source_index, view_index):
        if view_index in self.selectedIndexes():
            sel = self._selected_records()
            if sel: return sel
        return self._get_selected_records(source_index)

    def _unique_records(self, records):
        u, seen = [], set()
        for r in records:
            k = str(r.source_path)
            if k not in seen: seen.add(k); u.append(r)
        return u

    def _records_for_source_indexes(self, source_indexes):
        r = []
        for si in source_indexes: r.extend(self._get_selected_records(si))
        return self._unique_records(r)

    def _encode_source_indexes(self, source_indexes):
        p, m = [], self._source_model()
        for idx in source_indexes:
            p.append({
                "path": list(m.node_path(idx)),
                "node_type": str(idx.data(NODE_TYPE_ROLE) or ""),
                "name": str(idx.data(RAW_NAME_ROLE) or idx.data(Qt.DisplayRole) or ""),
                "source_node_id": str(idx.data(SOURCE_NODE_ID_ROLE) or ""),
                "source_node_type": str(idx.data(SOURCE_NODE_TYPE_ROLE) or ""),
            })
        return json.dumps(p).encode("utf-8")

    def _records_from_mime(self, mime):
        r, seen = [], set()
        for node in self._source_nodes_from_mime(mime):
            idx = node.get("index")
            if idx is None: continue
            for rec in self._get_selected_records(idx):
                k = str(rec.source_path)
                if k not in seen: seen.add(k); r.append(rec)
        return r

    def _source_nodes_from_mime(self, mime):
        try: payload = json.loads(bytes(mime.data(TREE_RECORD_MIME)).decode("utf-8"))
        except Exception: return []
        if not payload: return []
        m, nodes = self._source_model(), []
        for item in payload:
            if not isinstance(item, dict): continue
            rp = item.get("path")
            if not isinstance(rp, list): continue
            idx = m.index_for_path(tuple(str(p) for p in rp))
            if idx is None: continue
            nodes.append({
                "index": idx,
                "node_type": str(item.get("node_type") or idx.data(NODE_TYPE_ROLE) or ""),
                "name": str(item.get("name") or idx.data(RAW_NAME_ROLE) or idx.data(Qt.DisplayRole) or ""),
                "source_node_id": str(item.get("source_node_id") or idx.data(SOURCE_NODE_ID_ROLE) or ""),
                "source_node_type": str(item.get("source_node_type") or idx.data(SOURCE_NODE_TYPE_ROLE) or ""),
            })
        return nodes

    def _drop_target_fields(self, view_index):
        if not view_index.isValid(): return None
        si = self._source_index(view_index); nt = si.data(NODE_TYPE_ROLE)
        if nt == "file": si = si.parent()
        if bool(si.data(READ_ONLY_ROLE)) or self._is_read_only_utility_index(si):
            return None
        f = {key: value for key, value in dict(si.data(FIELDS_ROLE) or {}).items() if key in SEMANTIC_FIELD_NAMES}
        return f or None

    def _drop_index_at(self, pos):
        index = self.indexAt(pos)
        if not index.isValid():
            return index
        rect = self.visualRect(index)
        if not rect.isValid():
            return index
        margin = max(3, min(8, rect.height() // 4))
        if pos.y() < rect.top() + margin or pos.y() > rect.bottom() - margin:
            return self.model().index(-1, -1) if self.model() is not None else index.sibling(-1, -1)
        return index

    def _drop_target_node_type(self, view_index):
        if not view_index.isValid(): return None
        si = self._source_index(view_index); nt = si.data(NODE_TYPE_ROLE)
        if nt == "file": si = si.parent(); nt = si.data(NODE_TYPE_ROLE)
        return str(nt or "")

    def _drop_target_label(self, fields):
        if fields.get("pack"): return f"{fields['pack']} / {fields.get('category', '')}"
        if fields.get("category"): return fields["category"]
        return fields.get("audio_type", "Library")

    def _confirm_folder_move_if_needed(self, source_name, target_name, records, fields) -> bool:
        changed_records = self._records_changed_by_drop(records, fields)
        lines = self._drop_mutation_lines(changed_records, fields)
        if not lines:
            return True
        return self._confirm_record_reclassification(
            str(source_name or "selection"),
            str(target_name or "target"),
            lines,
            len(changed_records),
        )

    def _records_changed_by_drop(self, records, fields):
        changed = []
        for record in records or []:
            for field_name in SEMANTIC_FIELD_NAMES:
                if field_name not in fields:
                    continue
                value = str(fields.get(field_name) or "").strip()
                if not value:
                    continue
                if str(getattr(record, field_name, "") or "") != value:
                    changed.append(record)
                    break
        return changed

    def _drop_mutation_lines(self, records, fields) -> list[str]:
        labels = {
            "audio_type": "type",
            "category": "category",
            "subcategory": "subcategory",
            "pack": "pack",
        }
        lines = []
        for field_name, label in labels.items():
            if field_name not in fields:
                continue
            value = str(fields.get(field_name) or "").strip()
            if not value:
                continue
            if any(str(getattr(record, field_name, "") or "") != value for record in records or []):
                lines.append(f"{label} to {value}")
        return lines

    def _confirm_folder_move(self, source_name: str, target_name: str, mutation_lines: list[str], record_count: int) -> bool:
        return self._confirm_record_reclassification(source_name, target_name, mutation_lines, record_count)

    def _confirm_record_reclassification(self, source_name: str, target_name: str, mutation_lines: list[str], record_count: int) -> bool:
        item_word = "record" if record_count == 1 else "records"
        mutation_text = self._format_mutation_lines(mutation_lines)
        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Question)
        msg.setWindowTitle("Confirm File Reclassification")
        msg.setText(f'Reclassify {record_count} {item_word}?')
        msg.setInformativeText(
            f'Source: "{source_name}"\n\n'
            f'File changes: {mutation_text}.\n\n'
            f'Custom tree folders may still contain these files if they match the filter of the folder.\n'
            f'To change the tree structure itself, use Edit tree organization.'
        )
        msg.setMinimumWidth(420)
        msg.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        msg.setDefaultButton(QMessageBox.Yes)
        return msg.exec() == QMessageBox.Yes

    @staticmethod
    def _format_mutation_lines(lines: list[str]) -> str:
        items = [line for line in lines if line]
        if len(items) <= 1:
            return items[0] if items else ""
        if len(items) == 2:
            return f"{items[0]} and {items[1]}"
        return f"{', '.join(items[:-1])}, and {items[-1]}"

    def _folderized_drop_fields(self, source_node, target_fields, target_node_type):
        sn, st = str(source_node.get("name") or "").strip(), str(source_node.get("node_type") or "")
        f = dict(target_fields)
        if not sn: return f
        

        model = self._source_model()
        levels = model._active_tree_levels()
        next_field = None
        for i, (field_name, nt) in enumerate(levels):
            if nt == target_node_type:
                if i + 1 < len(levels):
                    next_field = levels[i+1][0]
                break
        
        if target_node_type == "type":
            if st == "category": f["category"] = sn
            elif st == "pack": f["pack"] = sn
            elif next_field: f[next_field] = sn
            else: f["category"] = sn
            return f
        if target_node_type == "category": f["pack"] = sn; return f
        if target_node_type == "pack": f["pack"] = sn; return f
        return f

    def _bucket_aware_drop_fields(self, source_node, target_fields, target_node_type):
        source_name = str(source_node.get("name") or "").strip()
        source_node_type = str(source_node.get("node_type") or "")
        fields = dict(target_fields)
        if not source_name:
            return self._normalize_drop_fields(fields)

        model = self._source_model()
        levels = model._active_tree_levels() if model and hasattr(model, "_active_tree_levels") else []
        next_field = None
        for index, (field_name, node_type) in enumerate(levels):
            if node_type == target_node_type and index + 1 < len(levels):
                next_field = levels[index + 1][0]
                break

        source_field_by_node_type = {
            "type": "audio_type",
            "category": "category",
            "subcategory": "subcategory",
            "pack": "pack",
        }
        source_field = source_field_by_node_type.get(source_node_type)
        if source_field and source_field == next_field:
            fields[source_field] = source_name
        return self._normalize_drop_fields(fields)

    def _normalize_drop_fields(self, fields):
        normalized = dict(fields)
        audio_type = str(normalized.get("audio_type") or "").strip()
        category = str(normalized.get("category") or "").strip()
        if audio_type and category and not category_is_valid_for_audio_type(category, audio_type):
            normalized["category"] = fallback_category_for_audio_type(audio_type)
            normalized.pop("subcategory", None)
        return normalized

    def _show_drop_hint(self, pos, fields):
        old_rect = self.drop_hint.geometry().adjusted(-2, -2, 2, 2) if self.drop_hint.isVisible() else None
        self.drop_hint.setText(f"Move into {self._drop_target_label(fields)}"); self.drop_hint.adjustSize()
        x = min(pos.x() + 14, max(0, self.viewport().width() - self.drop_hint.width() - 6))
        y = min(pos.y() + 14, max(0, self.viewport().height() - self.drop_hint.height() - 6))
        self.drop_hint.move(x, y); self.drop_hint.show()
        new_rect = self.drop_hint.geometry().adjusted(-2, -2, 2, 2)
        self.viewport().update(old_rect.united(new_rect) if old_rect is not None else new_rect)

    def _hide_drop_hint(self):
        if not self.drop_hint.isVisible():
            return
        rect = self.drop_hint.geometry().adjusted(-2, -2, 2, 2)
        self.drop_hint.hide()
        self.viewport().update(rect)

    def _confirm_pack_merge(self, source_name: str, target_name: str) -> bool:
        from PySide6.QtWidgets import QMessageBox
        sn, tn = (source_name or "").strip() or "Source", (target_name or "").strip() or "Target"
        msg = QMessageBox(self); msg.setWindowTitle("Merge Packs"); msg.setIcon(QMessageBox.Warning)
        msg.setText("Merge packs?"); msg.setInformativeText(f"'{sn}' into '{tn}'.")
        msg.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        return msg.exec() == QMessageBox.Yes

    def auto_expand_matches(self, query: str):
        q = (query or "").strip().lower()
        if not q: return
        m, matches = self._source_model(), []
        def walk(parent):
            for row in range(m.rowCount(parent)):
                idx = m.index(row, 0, parent)
                h = str(idx.data(SEARCH_TEXT_ROLE) or " ".join(m.node_path(idx))).lower()
                if q in h: matches.append(idx)
                walk(idx)
        walk(m.index(-1, -1))
        for idx in matches[:250]:
            p = idx
            while p.isValid():
                vi = self._to_view_index(p)
                if vi.isValid(): self.setExpanded(vi, True)
                p = p.parent()

    def _quick_filter_query_for_index(self, view_index):
        if not view_index.isValid(): return ""
        si = self._source_index(view_index); nt = si.data(NODE_TYPE_ROLE)
        if nt == "file": r = si.data(Qt.UserRole); return f'file:"{r.source_path.name}"' if r else ""
        if self._is_read_only_utility_index(si):
            return ""
        f = si.data(FIELDS_ROLE) or {}
        if nt == "type" and f.get("audio_type"): return f'type:"{f["audio_type"]}"'
        if nt == "category" and f.get("category"): return f'cat:"{f["category"]}"'
        if nt == "pack" and f.get("pack"): return f'pack:"{f["pack"]}"'
        return ""

    def _filter_level_label_for_index(self, source_index):
        nt = source_index.data(NODE_TYPE_ROLE)
        return {"file": "File", "type": "Type", "category": "Category", "pack": "Pack"}.get(nt, "Node")

    def _is_read_only_utility_index(self, source_index) -> bool:
        if not source_index.isValid():
            return False
        if source_index.data(NODE_TYPE_ROLE) != "type":
            return False
        fields = dict(source_index.data(FIELDS_ROLE) or {})
        raw_name = str(source_index.data(RAW_NAME_ROLE) or "")
        audio_type = str(fields.get("audio_type") or "")
        return raw_name == "Utility" and audio_type in {"Utility", "Non-Audio Assets"}

    def _compose_mode(self, modifiers):
        if modifiers & Qt.ShiftModifier: return "or"
        if modifiers & Qt.ControlModifier: return "and"
        return "replace"

    def _source_model(self):
        m = self.model()
        return m.sourceModel() if isinstance(m, QSortFilterProxyModel) else m

    def _source_index(self, index):
        if index.isValid() and index.column() != 0: index = index.siblingAtColumn(0)
        return self.model().mapToSource(index) if isinstance(self.model(), QSortFilterProxyModel) else index

    def _to_view_index(self, source_index):
        if source_index.isValid() and source_index.column() != 0: source_index = source_index.siblingAtColumn(0)
        return self.model().mapFromSource(source_index) if isinstance(self.model(), QSortFilterProxyModel) else source_index

    def _same_index(self, a, b): return a.isValid() and b.isValid() and a == b

    def _is_ancestor(self, ancestor_idx, descendant_idx) -> bool:
        if not ancestor_idx.isValid() or not descendant_idx.isValid():
            return False
        m = self._source_model()
        ancestor_path = m.node_path(ancestor_idx)
        descendant_path = m.node_path(descendant_idx)
        if not ancestor_path or not descendant_path:
            return False
        return len(descendant_path) >= len(ancestor_path) and descendant_path[:len(ancestor_path)] == ancestor_path


    def _scope_label(self, records): c = len(records); return f"{c} Item{'s' if c != 1 else ''}"

    def _shared_attr(self, records, attr):
        if not records: return None
        f = str(getattr(records[0], attr, ""))
        return f if all(str(getattr(r, attr, "")) == f for r in records) else None

    def _cleanup_export_temp_dirs(self):
        for p in self._export_temp_dirs: shutil.rmtree(p, ignore_errors=True)
        self._export_temp_dirs = []

    def _safe_folder_name(self, name: str):
        c = re.sub(r'[<>:"/\\|?*]+', "_", (name or "").strip()).strip(". ") or "Export"
        return c[:64].rstrip(". ") or "Export"

    def _find_common_parent(self, records):
        if not records: return None
        try:
            def _parent_of(sp):
                p = Path(str(sp))
                return p if p.is_dir() else p.parent

            parents = [_parent_of(record.source_path) for record in records]
            common_text = os.path.commonpath([str(parent) for parent in parents])
            return Path(common_text) if common_text else None
        except Exception:
            return None
