from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from PySide6.QtGui import QStandardItemModel, QStandardItem, QColor, QBrush, QFont, QIcon
from PySide6.QtCore import Qt, Signal
from unshuffle.core import PlanRecord, plan_record_sort_key
from unshuffle.core.assets import asset_path
from unshuffle.core.constants import DEFAULT_CLASSIFICATION_FLOOR
from unshuffle.core.paths import SYSTEM_FOLDER_NAME
from unshuffle.logic.tree_organization import TreeOrganizationProfile, TreeOrganizationResolver
from gui.models.library_tree_resolution import (
    ResolvedPresentationNode,
    build_custom_resolved_tree,
    build_normal_resolved_tree,
    build_route_resolved_tree,
    node_type_for_fields,
)
from gui.utils.constants import StagingColumn
from gui.utils.styles import ColorPalette, make_qcolor

RAW_NAME_ROLE = Qt.UserRole + 1
NODE_TYPE_ROLE = Qt.UserRole + 2
COUNT_ROLE = Qt.UserRole + 3
RECORDS_ROLE = Qt.UserRole + 4
POPULATED_ROLE = Qt.UserRole + 5
SEARCH_TEXT_ROLE = Qt.UserRole + 6
DUMMY_ROLE = Qt.UserRole + 7
FIELDS_ROLE = Qt.UserRole + 8
TAG_TEXT_ROLE = Qt.UserRole + 9
COLLISION_COUNT_ROLE = Qt.UserRole + 10
READ_ONLY_ROLE = Qt.UserRole + 11
RESIDUAL_ROLE = Qt.UserRole + 12
SOURCE_NODE_ID_ROLE = Qt.UserRole + 13
SOURCE_NODE_TYPE_ROLE = Qt.UserRole + 14

SEMANTIC_FIELD_NAMES = {"audio_type", "category", "subcategory", "pack"}
INTERNAL_PATH_NAMES = {".unshuffle", ".unshuffle_hashes.json", "unshuffle.log", SYSTEM_FOLDER_NAME.lower()}


@dataclass(frozen=True)
class _NodeRecordMetadata:
    tag_summary: str
    collision_count: int

TREE_LEVELS = [
    ("audio_type", "type"),
    ("category", "category"),
    ("subcategory", "subcategory"),
    ("pack", "pack"),
]
@lru_cache(maxsize=1)
def search_highlight_brush():
    return QBrush(make_qcolor(ColorPalette.SEARCH_HIGHLIGHT))

@lru_cache(maxsize=1)
def hands_off_bg_brush():
    return QBrush(make_qcolor(ColorPalette.HANDS_OFF_BG))

@lru_cache(maxsize=1)
def warning_color():
    return make_qcolor(ColorPalette.WARNING)

@lru_cache(maxsize=1)
def tree_midi_color():
    return make_qcolor(ColorPalette.TREE_MIDI)

@lru_cache(maxsize=1)
def tree_oneshot_color():
    return make_qcolor(ColorPalette.TREE_ONESHOT)

@lru_cache(maxsize=1)
def tree_loop_color():
    return make_qcolor(ColorPalette.TREE_LOOP)

@lru_cache(maxsize=1)
def tree_root_color():
    return make_qcolor(ColorPalette.TREE_ROOT)

@lru_cache(maxsize=1)
def tree_category_color():
    return make_qcolor(ColorPalette.TREE_CATEGORY)

@lru_cache(maxsize=1)
def tree_pack_color():
    return make_qcolor(ColorPalette.TREE_PACK)

def _get_tinted_icon_for_tree(icon_path, color_name) -> QIcon:
    from PySide6.QtGui import QPixmap, QPainter
    color = make_qcolor(color_name)
    pixmap = QPixmap(str(asset_path(*str(icon_path).replace("\\", "/").split("/"))))
    if pixmap.isNull():
        return QIcon()
    painter = QPainter(pixmap)
    try:
        painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
        painter.fillRect(pixmap.rect(), color)
    finally:
        painter.end()
    return QIcon(pixmap)

@lru_cache(maxsize=6)
def tree_file_sequence_color(idx: int):
    colors = ColorPalette.IDENTITY
    if not colors:
        return make_qcolor(ColorPalette.TEXT_LIGHT)
    color_name = colors[idx % len(colors)]
    return make_qcolor(color_name)

@lru_cache(maxsize=6)
def tree_file_sequence_icon(idx: int):
    colors = ColorPalette.IDENTITY
    if not colors:
        return QIcon()
    color_name = colors[idx % len(colors)]
    return _get_tinted_icon_for_tree("icons/waveform.png", color_name)

@lru_cache(maxsize=1)
def text_inactive_color():
    return make_qcolor(ColorPalette.TEXT_INACTIVE)


def clear_tree_color_caches():
    search_highlight_brush.cache_clear()
    hands_off_bg_brush.cache_clear()
    warning_color.cache_clear()
    tree_midi_color.cache_clear()
    tree_oneshot_color.cache_clear()
    tree_loop_color.cache_clear()
    tree_root_color.cache_clear()
    tree_category_color.cache_clear()
    tree_pack_color.cache_clear()
    text_inactive_color.cache_clear()
    tree_file_sequence_color.cache_clear()
    tree_file_sequence_icon.cache_clear()

def active_tree_levels_for_sort(sort_column: int):
    return list(TREE_LEVELS)

def build_tree_payload(
    records,
    levels,
    confidence_min: float = 0.0,
    confidence_max: float = 1.0,
    *,
    confidence_floor: float | None = None,
    confidence_filter_enabled: bool | None = None,
):
    levels = list(levels)
    if not levels:
        return list(records)

    field, _node_type = levels[0]
    grouped = {}
    for rec in records:
        try:
            conf = float(rec.confidence)
            if conf < confidence_min or conf > confidence_max:
                continue
        except (ValueError, TypeError):
            pass

        if confidence_filter_enabled is None:
            confidence_filter_enabled = True
        low_confidence_uncategorized = False
        if confidence_filter_enabled and confidence_floor is not None:
            try:
                low_confidence_uncategorized = (
                    float(rec.confidence) < confidence_floor
                    and not getattr(rec, "is_manual", False)
                    and not getattr(rec, "is_hands_off", False)
                )
            except (ValueError, TypeError):
                low_confidence_uncategorized = False

        if field == "confidence_band":
            try:
                conf = float(getattr(rec, "confidence", 0.0))
                if conf >= 0.9: val = "90-100% (High Confidence)"
                elif conf >= 0.7: val = "70-90% (Medium Confidence)"
                elif conf >= 0.5: val = "50-70% (Low Confidence)"
                else: val = "0-50% (Uncertain / Noise)"
            except (ValueError, TypeError):
                val = "Unknown Confidence"
        else:
            raw_val = getattr(rec, field, "")
            if raw_val is None:
                raw_val = ""
            val = str(raw_val).strip()
            if field == "audio_type" and val == "Non-Audio Assets":
                val = "Utility"
            if low_confidence_uncategorized and field in {"category", "subcategory"}:
                val = "Uncategorized" if field == "category" else ""
            if val=="" and field == "subcategory":
                val = "Other"

        grouped.setdefault(val, []).append(rec)

    return {
        name: build_tree_payload(
            group_records,
            levels[1:],
            confidence_min,
            confidence_max,
            confidence_floor=confidence_floor,
            confidence_filter_enabled=confidence_filter_enabled,
        )
        for name, group_records in grouped.items()
    }

def canonical_preview_key(rec: PlanRecord):
    return (
        str(getattr(rec, "audio_type", "")).strip().lower(),
        str(getattr(rec, "category", "")).strip().lower(),
        str(getattr(rec, "subcategory", "") or "").strip().lower(),
        str(getattr(rec, "pack", "")).strip().lower(),
        str(getattr(rec, "source_path", "") and getattr(rec, "source_path").name).strip().lower(),
    )

class LibraryTreeModel(QStandardItemModel):
    """Builds a type -> category -> pack tree from staged records."""
    rebuildFinished = Signal()
    def __init__(self, parent=None):
        super().__init__(parent)
        self.search_text = ""
        self._sort_column = StagingColumn.FILENAME
        self.confidence_min: float = 0.0
        self.confidence_max: float = 1.0
        self.confidence_floor: float = DEFAULT_CLASSIFICATION_FLOOR
        self.confidence_filter_enabled: bool = True
        self.custom_tree_profile: TreeOrganizationProfile | None = None
        self._preview_key_cache: dict[int, tuple[str, str, str, str, str]] = {}
        self._global_collision_keys: set[tuple[str, str, str, str, str]] = set()
        self._record_search_cache: dict[int, str] = {}
        self._record_tags_cache: dict[int, list[str]] = {}
        self._folder_icon: QIcon | None = None
        self._file_icon: QIcon | None = None
        self.setColumnCount(2)
        self.setHorizontalHeaderLabels(["Name", "Info"])

    def set_sort_column(self, column: StagingColumn | int):
        self._sort_column = StagingColumn(column)

    @property
    def _sample_icon(self):
        return tree_file_sequence_icon(0)

    def set_search_text(self, text: str):
        self.search_text = (text or "").strip().lower()

    def set_custom_tree_profile(self, profile: TreeOrganizationProfile | None):
        self.custom_tree_profile = profile

    def rebuild(self, records: list[PlanRecord], skip_fields: set[str] | None = None):
        records = [record for record in records if not self._is_internal_system_record(record)]
        self._prepare_rebuild_caches(records)
        self.clear()
        self.setColumnCount(2)
        self.setHorizontalHeaderLabels(["Name", "Info"])
        root = self.invisibleRootItem()
        if not records:
            item = self._make_node_item("No matching files", "empty", 0, {}, [])
            item.setEnabled(False)
            root.appendRow([item, self._make_aux_item("")])
            self.rebuildFinished.emit()
            return
        skip_fields = set(skip_fields or set())
        levels = [(field, node_type) for field, node_type in self._active_tree_levels() if field not in skip_fields]

        folder_icon = self._folder_icon or QIcon()

        if self.custom_tree_profile is not None:
            self._rebuild_custom(records, folder_icon)
            self.rebuildFinished.emit()
            return

        if not levels:
            self._append_file_items(root, records)
            return

        nodes = build_normal_resolved_tree(records, levels, self._group_records)
        self._append_resolved_nodes(root, nodes, folder_icon)
        self.rebuildFinished.emit()

    def _rebuild_custom(self, records: list[PlanRecord], folder_icon):
        root = self.invisibleRootItem()
        if self.custom_tree_profile is None:
            return
        resolver = TreeOrganizationResolver()
        validation = resolver.validate_profile(self.custom_tree_profile, [])
        if not validation.valid:
            item = self._make_node_item("Invalid Custom Tree", "custom", 0, {}, [])
            item.setToolTip("\n".join(validation.blocking_messages))
            item.setIcon(folder_icon)
            root.appendRow([item, self._make_aux_item("")])
            return
        try:
            nodes = build_custom_resolved_tree(
                self.custom_tree_profile,
                records,
                self._active_tree_levels(),
                self._group_records,
                confidence_min=self.confidence_min,
                confidence_max=self.confidence_max,
                confidence_floor=self.confidence_floor,
                confidence_filter_enabled=self.confidence_filter_enabled,
            )
        except ValueError as exc:
            item = self._make_node_item("Invalid Custom Tree", "custom", 0, {}, [])
            item.setToolTip(str(exc))
            item.setIcon(folder_icon)
            root.appendRow([item, self._make_aux_item("")])
            return
        self._append_resolved_nodes(root, nodes, folder_icon)

    @staticmethod
    def _custom_node_type(fields: dict[str, str]) -> str:
        return node_type_for_fields(fields)

    def _flatten_custom_records(self, value: dict) -> list[PlanRecord]:
        records = list(value.get("__records__", []))
        for key, child in value.items():
            if key != "__records__":
                records.extend(self._flatten_custom_records(child))
        return records

    def partial_rebuild(self, records: list[PlanRecord], top_level_names: set[str], skip_fields: set[str] | None = None):
        self._prepare_rebuild_caches(records)
        skip_fields = set(skip_fields or set())
        levels = [(field, node_type) for field, node_type in self._active_tree_levels() if field not in skip_fields]
        if not levels:
            self.rebuild(records, skip_fields=skip_fields)
            return

        if self.custom_tree_profile is not None:
            self._partial_rebuild_custom(records, top_level_names, levels)
            return

        top_field, _top_node_type = levels[0]
        folder_icon = self._folder_icon or QIcon()

        affected = {name for name in top_level_names if name}
        root = self.invisibleRootItem()

        current_records = {}
        for rec in records:
            current_records.setdefault(str(getattr(rec, top_field, "")), []).append(rec)

        existing_items = {}
        for row in range(root.rowCount()):
            child = root.child(row)
            existing_items[str(child.data(RAW_NAME_ROLE) or child.text())] = child

        for name in sorted(affected):
            branch_records = current_records.get(name, [])
            existing = existing_items.get(name)
            if not branch_records:
                if existing is not None:
                    root.removeRow(existing.row())
                continue

            child_grouped = self._group_records(branch_records, levels[1:]) if len(levels) > 1 else branch_records
            if existing is None:
                metadata = self._node_record_metadata(name, branch_records)
                count = len(branch_records)
                item = self._make_node_item(name, levels[0][1], count, {top_field: name}, branch_records, metadata)
                item.setData(branch_records, RECORDS_ROLE)
                item.setIcon(folder_icon)
                root.appendRow([item, self._make_aux_item(self._tag_summary(branch_records))])
                existing = item
            else:
                metadata = self._node_record_metadata(name, branch_records)
                count = len(branch_records)
                existing.setText(f"{name} ({count})")
                existing.setData(count, COUNT_ROLE)
                existing.setData({top_field: name}, FIELDS_ROLE)
                tag_summary = metadata.tag_summary
                existing.setData(tag_summary, TAG_TEXT_ROLE)
                existing.setData(metadata.collision_count, COLLISION_COUNT_ROLE)
                existing.setData(None, SEARCH_TEXT_ROLE)
                existing.setData(branch_records, RECORDS_ROLE)
                existing.setIcon(folder_icon)
                sibling = existing.parent().child(existing.row(), 1) if existing.parent() else self.invisibleRootItem().child(existing.row(), 1)
                if sibling is not None:
                    sibling.setText(self._collision_info(int(existing.data(COLLISION_COUNT_ROLE) or 0)))
                existing.removeRows(0, existing.rowCount())

            if len(levels) == 1:
                existing.setData(branch_records, RECORDS_ROLE)
                existing.setData(False, POPULATED_ROLE)
                dummy = QStandardItem("Loading...")
                dummy.setEnabled(False)
                dummy.setData(True, DUMMY_ROLE)
                existing.appendRow([dummy, QStandardItem("")])
            else:
                self._append_group_items(existing, child_grouped, levels[1:], folder_icon, {top_field: name})

        self._sort_root_items()

    def _partial_rebuild_custom(self, records: list[PlanRecord], top_level_names: set[str], levels):
        if self.custom_tree_profile is None:
            return

        affected = {name for name in top_level_names if name}
        if not affected:
            return

        folder_icon = self._folder_icon
        root = self.invisibleRootItem()

        existing_items = {}
        existing_count = 0
        for row in range(root.rowCount()):
            child = root.child(row)
            label = str(child.data(RAW_NAME_ROLE) or child.text())
            existing_items[label] = child
            existing_count += int(child.data(COUNT_ROLE) or 0)

        affected_existing_count = sum(
            int(item.data(COUNT_ROLE) or 0)
            for label, item in existing_items.items()
            if label in affected
        )
        if existing_count and affected_existing_count / max(1, existing_count) > 0.6:
            self.rebuild(records)
            return

        branch_records = {name: [] for name in affected}
        from unshuffle.logic.tree_organization import TreeRouteBuilder

        try:
            routes = TreeRouteBuilder().routes_for(
                records,
                self.custom_tree_profile,
                levels,
                presentation_mode=True,
                confidence_min=self.confidence_min,
                confidence_max=self.confidence_max,
                confidence_floor=self.confidence_floor,
                confidence_filter_enabled=self.confidence_filter_enabled,
            )
        except ValueError:
            self.rebuild(records)
            return

        for route in routes:
            if not route.parts:
                continue
            top_label = route.parts[0].label
            if top_label in branch_records:
                branch_records[top_label].append(route.record)

        for name in sorted(affected):
            existing = existing_items.get(name)
            records_for_branch = branch_records.get(name, [])
            if existing is not None:
                root.removeRow(existing.row())
            if not records_for_branch:
                continue
            nodes = build_custom_resolved_tree(
                self.custom_tree_profile,
                records_for_branch,
                levels,
                self._group_records,
                confidence_min=self.confidence_min,
                confidence_max=self.confidence_max,
                confidence_floor=self.confidence_floor,
                confidence_filter_enabled=self.confidence_filter_enabled,
            )
            for node in nodes:
                if node.label == name:
                    self._append_resolved_nodes(root, [node], folder_icon)
                    break

        self._sort_root_items()

    def _active_tree_levels(self):
        return active_tree_levels_for_sort(self._sort_column)

    def _prepare_rebuild_caches(self, records: list[PlanRecord]) -> None:
        if self._folder_icon is None or self._file_icon is None:
            from PySide6.QtWidgets import QFileIconProvider

            icon_provider = QFileIconProvider()
            self._folder_icon = icon_provider.icon(QFileIconProvider.Folder)
            self._file_icon = icon_provider.icon(QFileIconProvider.File)
        self._preview_key_cache = {id(rec): canonical_preview_key(rec) for rec in records}
        preview_counts = Counter(self._preview_key_cache.values())
        self._global_collision_keys = {key for key, count in preview_counts.items() if count > 1}
        self._record_search_cache = {}
        
        self._record_tags_cache = {}
        for rec in records:
            rec_tags = []
            for tag in getattr(rec, "tags", []) or []:
                value = str(tag or "").strip()
                if value:
                    rec_tags.append(value)
            self._record_tags_cache[id(rec)] = rec_tags

    def _preview_key_for_record(self, rec: PlanRecord):
        key = self._preview_key_cache.get(id(rec))
        if key is None:
            key = canonical_preview_key(rec)
            self._preview_key_cache[id(rec)] = key
        return key

    def _make_node_item(
        self,
        name: str,
        node_type: str,
        count: int,
        fields: dict[str, str] | None = None,
        records: list[PlanRecord] | None = None,
        metadata: _NodeRecordMetadata | None = None,
    ):
        if metadata is None:
            metadata = self._node_record_metadata(name, records or [])
        item = QStandardItem(f"{name} ({count})")
        item.setData(name, RAW_NAME_ROLE)
        item.setData(node_type, NODE_TYPE_ROLE)
        item.setData(count, COUNT_ROLE)
        item.setData(dict(fields or {}), FIELDS_ROLE)
        tag_summary = metadata.tag_summary
        collision_count = metadata.collision_count
        item.setData(tag_summary, TAG_TEXT_ROLE)
        item.setData(collision_count, COLLISION_COUNT_ROLE)
        item.setToolTip(self._node_tooltip(name, count, tag_summary, collision_count))
        return item

    @staticmethod
    def _is_internal_system_record(record: PlanRecord) -> bool:
        try:
            path = getattr(record, "source_path", None)
            if not path:
                return False
            path_str = str(path).lower()
        except Exception:
            return False
            
        parts = path_str.replace("\\", "/").split("/")
        return not INTERNAL_PATH_NAMES.isdisjoint(parts)

    def _append_resolved_nodes(self, parent, nodes: list[ResolvedPresentationNode], folder_icon):
        for node in nodes:
            fields = dict(node.semantic_fields or {})
            if node.source_node_id and node.visual_path:
                fields["custom_path"] = "/".join(node.visual_path)
            metadata = self._node_record_metadata(node.label, node.records)
            count = len(node.records)
            item = self._make_node_item(node.label, node.node_type, count, fields, node.records, metadata)
            item.setData(node.read_only, READ_ONLY_ROLE)
            item.setData(node.residual, RESIDUAL_ROLE)
            item.setData(node.source_node_id or "", SOURCE_NODE_ID_ROLE)
            item.setData(node.source_node_type or "", SOURCE_NODE_TYPE_ROLE)
            item.setData(node.records, RECORDS_ROLE)
            item.setIcon(folder_icon)
            parent.appendRow([item, self._make_aux_item(self._collision_info(int(item.data(COLLISION_COUNT_ROLE) or 0)))])
            if node.children:
                self._append_resolved_nodes(item, node.children, folder_icon)
            else:
                item.setData(False, POPULATED_ROLE)
                dummy = QStandardItem("Loading...")
                dummy.setEnabled(False)
                dummy.setData(True, DUMMY_ROLE)
                item.appendRow([dummy, QStandardItem("")])

    def _group_records(self, records, levels):
        return build_tree_payload(
            records,
            levels,
            self.confidence_min,
            self.confidence_max,
            confidence_floor=self.confidence_floor,
            confidence_filter_enabled=self.confidence_filter_enabled,
        )

    def _count_direct_children(self, child_data, remaining_levels):
        if not remaining_levels:
            return len(child_data)
        next_field, _ = remaining_levels[0]
        next_is_leaf = len(remaining_levels) == 1
        if next_field == "subcategory" and not next_is_leaf and len(child_data) == 1 and "Other" in child_data:
            return self._count_direct_children(child_data["Other"], remaining_levels[1:])
        if next_field == "subcategory" and next_is_leaf and len(child_data) == 1 and "Other" in child_data:
            return len(child_data["Other"])
        return len(child_data)

    def _append_group_items(self, parent, grouped, levels, folder_icon, fields):
        field, node_type = levels[0]
        is_leaf = len(levels) == 1

        if field == "subcategory" and not is_leaf and len(grouped) == 1 and "Other" in grouped:
            self._append_group_items(parent, grouped["Other"], levels[1:], folder_icon, fields)
            return
        if field == "subcategory" and is_leaf and len(grouped) == 1 and "Other" in grouped:
            records = list(grouped.get("Other") or [])
            parent.setData(records, RECORDS_ROLE)
            parent.setData(False, POPULATED_ROLE)
            if parent.rowCount() == 0:
                dummy = QStandardItem("Loading...")
                dummy.setEnabled(False)
                dummy.setData(True, DUMMY_ROLE)
                parent.appendRow([dummy, QStandardItem("")])
            return

        for name in sorted(grouped):
            child_data = grouped[name]
            records = child_data if is_leaf else self._flatten_group(child_data)
            node_fields = dict(fields)
            node_fields[field] = name
            metadata = self._node_record_metadata(name, records)
            count = len(records)
            item = self._make_node_item(name, node_type, count, node_fields, records, metadata)
            item.setData(records, RECORDS_ROLE)
            item.setIcon(folder_icon)
            parent.appendRow([item, self._make_aux_item(self._collision_info(int(item.data(COLLISION_COUNT_ROLE) or 0)))])

            if is_leaf:
                item.setData(False, POPULATED_ROLE)
                dummy = QStandardItem("Loading...")
                dummy.setEnabled(False)
                dummy.setData(True, DUMMY_ROLE)
                item.appendRow([dummy, QStandardItem("")])
            else:
                self._append_group_items(item, child_data, levels[1:], folder_icon, node_fields)

    def _append_file_items(self, parent, records):
        collision_counts = Counter(self._preview_key_for_record(rec) for rec in records)
        for loop_idx, rec in enumerate(sorted(records, key=self._record_sort_key)):
            file_item = QStandardItem(rec.source_path.name)
            file_item.setData(rec, Qt.UserRole)
            file_item.setData(rec.source_path.name, RAW_NAME_ROLE)
            file_item.setData("file", NODE_TYPE_ROLE)
            file_item.setData(rec.source_path.name, SEARCH_TEXT_ROLE)
            file_item.setData(max(0, collision_counts[self._preview_key_for_record(rec)] - 1), COLLISION_COUNT_ROLE)
            audio_type = str(getattr(rec, "audio_type", "")).strip()
            is_sample = audio_type not in {"Non-Audio Assets", "Utility"}
            if is_sample:
                file_item.setIcon(tree_file_sequence_icon(loop_idx))
            else:
                file_item.setIcon(self._file_icon or QIcon())
            file_item.setToolTip(str(rec.source_path))
            tags = ", ".join(str(tag) for tag in (getattr(rec, "tags", []) or []))
            collision_count = int(file_item.data(COLLISION_COUNT_ROLE) or 0)
            info_parts = []
            if tags:
                info_parts.append(tags)
            if collision_count:
                info_parts.append(f"⚠ {collision_count} naming collision{'s' if collision_count != 1 else ''}")
            parent.appendRow([file_item, self._make_aux_item("  ".join(info_parts))])

    def _make_aux_item(self, text: str):
        item = QStandardItem(text or "")
        item.setEditable(False)
        return item

    def _sort_root_items(self):
        root = self.invisibleRootItem()
        items = [root.takeRow(0) for _ in range(root.rowCount())]
        items.sort(key=lambda row: str(row[0].data(RAW_NAME_ROLE) or row[0].text()).lower() if row else "")
        for row in items:
            root.appendRow(row)

    def _record_sort_key(self, rec: PlanRecord):
        col = self._sort_column
        if col == StagingColumn.PACK:
            return plan_record_sort_key(rec, "pack")
        if col == StagingColumn.CATEGORY:
            return plan_record_sort_key(rec, "category")
        if col == StagingColumn.TAGS:
            return plan_record_sort_key(rec, "tags")
        if col == StagingColumn.PATH:
            return plan_record_sort_key(rec, "path")
        if col == StagingColumn.CONFIDENCE:
            return plan_record_sort_key(rec, "confidence")
        return plan_record_sort_key(rec, "filename")

    def _flatten_group(self, group):
        if isinstance(group, list):
            return group
        records = []
        for child in group.values():
            records.extend(self._flatten_group(child))
        return records

    def populate_index(self, index):
        item = self.itemFromIndex(index)
        if not item or item.data(POPULATED_ROLE) is not False:
            return

        item.removeRows(0, item.rowCount())
        records = item.data(RECORDS_ROLE) or []
        self._append_file_items(item, records)
        item.setData(True, POPULATED_ROLE)

    def _search_text_for_records(self, name, records):
        parts = [str(name or "").lower()]
        for rec in records:
            cached = self._record_search_cache.get(id(rec))
            if cached is None:
                cached = " ".join(
                    str(value)
                    for value in (
                        getattr(rec, "source_path", "") and getattr(rec, "source_path").name,
                        getattr(rec, "pack", ""),
                        getattr(rec, "category", ""),
                        *(getattr(rec, "tags", []) or []),
                    )
                    if value
                ).lower()
                self._record_search_cache[id(rec)] = cached
            if cached:
                parts.append(cached)
        return " ".join(parts)

    def _node_record_metadata(self, name, records, tag_limit: int = 3) -> _NodeRecordMetadata:
        if not records:
            return _NodeRecordMetadata("", 0)
        if len(records) == 1:
            rec = records[0]
            tags = []
            seen = set()
            for tag in self._record_tags_cache.get(id(rec), ()):
                if tag and tag not in seen:
                    seen.add(tag)
                    tags.append(tag)
                    if len(tags) >= tag_limit:
                        break
            return _NodeRecordMetadata(", ".join(tags), 0)

        tag_counts = Counter()
        preview_counts = Counter()
        collision_keys = self._global_collision_keys
        for rec in records:
            rec_id = id(rec)
            for tag in self._record_tags_cache.get(rec_id, ()):
                tag_counts[tag] += 1
            key = self._preview_key_cache.get(rec_id)
            if key in collision_keys:
                preview_counts[key] += 1

        if tag_counts:
            top = [tag for tag, _count in tag_counts.most_common(tag_limit)]
            extra = len(tag_counts) - len(top)
            tag_summary = f"{', '.join(top)} +{extra}" if extra > 0 else ", ".join(top)
        else:
            tag_summary = ""
        collision_count = sum(count - 1 for count in preview_counts.values() if count > 1) if preview_counts else 0
        return _NodeRecordMetadata(tag_summary, collision_count)

    def _tag_summary(self, records, limit: int = 3):
        counts = Counter()
        for rec in records:
            for tag in self._record_tags_cache.get(id(rec), ()):
                counts[tag] += 1
        if not counts:
            return ""
        top = [name for name, _count in counts.most_common(limit)]
        extra = len(counts) - len(top)
        return f"{', '.join(top)} +{extra}" if extra > 0 else ", ".join(top)

    def _collision_count(self, records):
        counts = Counter(self._preview_key_cache.get(id(rec)) for rec in records)
        return sum(count - 1 for count in counts.values() if count > 1)

    def _collision_info(self, collision_count: int) -> str:
        if not collision_count:
            return ""
        return f"⚠ {collision_count} naming collision{'s' if collision_count != 1 else ''}"

    def _node_tooltip(self, name: str, count: int, tag_summary: str, collision_count: int):
        lines = [f"{name} - {count} item{'s' if count != 1 else ''}"]
        if tag_summary:
            lines.append(f"Info: {tag_summary}")
        if collision_count > 0:
            lines.append(f"Potential canonical collisions: {collision_count}")
        return "\n".join(lines)

    def node_path(self, index) -> tuple[str, ...]:
        parts = []
        current = index
        while current.isValid():
            raw = current.data(RAW_NAME_ROLE) or current.data(Qt.DisplayRole)
            parts.append(str(raw))
            current = current.parent()
        return tuple(reversed(parts))

    def index_for_path(self, path: tuple[str, ...]):
        parent = self.invisibleRootItem()
        found_index = None
        for part in path:
            found_item = None
            for row in range(parent.rowCount()):
                child = parent.child(row)
                if str(child.data(RAW_NAME_ROLE) or child.text()) == part:
                    found_item = child
                    break
            if found_item is None:
                return None
            found_index = self.indexFromItem(found_item)
            if found_item.data(POPULATED_ROLE) is False:
                self.populate_index(found_index)
            parent = found_item
        return found_index

    def data(self, index, role=Qt.DisplayRole):
        if role == SEARCH_TEXT_ROLE:
            cached = super().data(index, SEARCH_TEXT_ROLE)
            if cached:
                return cached
            raw = str(
                super().data(index, RAW_NAME_ROLE)
                or super().data(index, Qt.DisplayRole)
                or ""
            )
            records = super().data(index, RECORDS_ROLE) or []
            search_text = self._search_text_for_records(raw, records) if records else raw.lower()
            item = self.itemFromIndex(index)
            if item is not None:
                item.setData(search_text, SEARCH_TEXT_ROLE)
            return search_text

        raw_name = str(
            super().data(index, RAW_NAME_ROLE)
            or super().data(index, Qt.DisplayRole)
            or ""
        )
        is_hands_off = "handsoff" in raw_name.lower()
        collision_count = int(super().data(index, COLLISION_COUNT_ROLE) or 0)

        if role == Qt.BackgroundRole and self.search_text:
            haystack = str(self.data(index, SEARCH_TEXT_ROLE)).lower()
            if self.search_text in haystack:
                return search_highlight_brush()
        elif role == Qt.BackgroundRole and is_hands_off:
            return hands_off_bg_brush()

        if role == Qt.FontRole:
            node_type = super().data(index, NODE_TYPE_ROLE)
            if node_type in {"type", "category", "pack"}:
                font = QFont()
                if is_hands_off:
                    font.setItalic(True)
                return font

        if role == Qt.ForegroundRole:
            node_type = super().data(index, NODE_TYPE_ROLE)
            if node_type == "file":
                return tree_file_sequence_color(index.row())

            depth = 0
            temp = index
            while temp.parent().isValid():
                temp = temp.parent()
                depth += 1

            if is_hands_off:
                return tree_midi_color()
            if depth == 0:
                text = raw_name.lower()
                if "one-shot" in text or "oneshot" in text:
                    return tree_oneshot_color()
                if "loop" in text:
                    return tree_loop_color()
                if "midi" in text:
                    return tree_midi_color()
                return tree_root_color()
            if depth == 1:
                return tree_category_color()
            if depth == 2:
                return tree_pack_color()

        if role == Qt.ToolTipRole and is_hands_off:
            return "HandsOff: preserved during migrate and excluded from normal categorization."
        if role == Qt.ToolTipRole and collision_count > 0:
            base = super().data(index, Qt.ToolTipRole) or raw_name
            return f"{base}\nPotential canonical collisions: {collision_count}"

        if index.column() == 1:
            if role == Qt.TextAlignmentRole:
                return int(Qt.AlignLeft | Qt.AlignVCenter)
            if role == Qt.ForegroundRole:
                if collision_count > 0:
                    return warning_color()
                return text_inactive_color()

        return super().data(index, role)
