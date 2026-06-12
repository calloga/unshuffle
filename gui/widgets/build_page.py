import os
from pathlib import Path
from PySide6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QCheckBox, QFileDialog, QTreeWidget, QTreeWidgetItem,
    QSplitter, QHeaderView, QWidget
)
from PySide6.QtCore import QSize, Qt, Signal
from unshuffle.logic.execution import DestinationResolver
from unshuffle.logic.tree_organization import TreeOrganizationProfile, TreeOrganizationResolver
from ..utils.constants import (
    BUILD_COMPARE_DEFAULT_HEIGHT,
    BUILD_COMPARE_DEFAULT_WIDTH,
    BUILD_COMPARE_MARGIN,
    BUILD_COMPARE_SPACING,
    BUILD_COMPARE_TREE_INFO_MIN_WIDTH,
    BUILD_COMPARE_TREE_INFO_WIDTH,
)
from ..utils.styles import (
    apply_style,
    build_page_style,
    dock_save_search_button_style,
    scaled_px,
)
from ..utils.layout_helpers import apply_layout_margins, apply_layout_spacing
from ..widgets.delegates.compare_tree_delegate import CompareTreeDelegate
from unshuffle.core import get_pack_prefix


MAX_COMPARE_FILES_PER_FOLDER = 120
MAX_COMPARE_FILE_ITEMS = 1200
COMPARE_TREE_ROW_HEIGHT = 28


def _normalized_resolved_path(path: Path) -> Path:
    try:
        return Path(os.path.normcase(str(Path(path).resolve())))
    except OSError:
        return Path(os.path.normcase(str(Path(path).absolute())))


def paths_overlap(left: Path, right: Path) -> bool:
    left_resolved = _normalized_resolved_path(left)
    right_resolved = _normalized_resolved_path(right)
    return (
        left_resolved == right_resolved
        or left_resolved in right_resolved.parents
        or right_resolved in left_resolved.parents
    )


def target_source_overlap_message(target: Path, source_roots: list[Path]) -> str:
    if not str(target).strip():
        return ""
    for source_root in source_roots:
        if paths_overlap(target, source_root):
            return "Target must be different from source."
    return ""


class BuildPage(QWidget):
    accepted = Signal()
    rejected = Signal()
    stabilityReviewRequested = Signal()

    def __init__(self, settings, records, source_roots, parent=None, *, active_tree_profile=None):
        super().__init__(parent)
        self.setObjectName("BuildPage")
        self.settings = settings
        self.records = list(records or [])
        self.source_roots = [Path(root) for root in (source_roots or [])]
        self._saved_target = str(settings.value("last_target", "")).strip()
        self.target_dir = Path(self._saved_target) if self._saved_target else Path.cwd()
        self._active_tree_profile: TreeOrganizationProfile | None = active_tree_profile
        self._tree_resolver = TreeOrganizationResolver()
        self._destination_resolver = DestinationResolver(tree_resolver=self._tree_resolver)
        self._before_tree_widget: QTreeWidget | None = None
        self._projected_cache: dict[tuple, list[tuple[Path, object]]] = {}
        self.setWindowTitle("Compare Build")
        self.resize(BUILD_COMPARE_DEFAULT_WIDTH, BUILD_COMPARE_DEFAULT_HEIGHT)
        self._setup_ui()
        self.refresh_theme()

    def accept(self) -> None:
        self.accepted.emit()

    def reject(self) -> None:
        self.rejected.emit()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        apply_layout_margins(
            layout,
            (
                BUILD_COMPARE_MARGIN,
                BUILD_COMPARE_MARGIN,
                BUILD_COMPARE_MARGIN,
                BUILD_COMPARE_MARGIN,
            ),
        )
        apply_layout_spacing(layout, BUILD_COMPARE_SPACING)

    
        splitter = QSplitter(Qt.Horizontal)
        splitter.setObjectName("CompareSplitter")
        self.before_panel = self._make_tree_panel("Current Directories", "source")
        self.after_panel = self._make_tree_panel("After Migration", "target")
        splitter.addWidget(self.before_panel)
        splitter.addWidget(self.after_panel)
        splitter.setSizes([500, 500])
        layout.addWidget(splitter, 1)

   
        options_group = QWidget()
        options_group.setObjectName("CompareOptionsCard")
        options_layout = QVBoxLayout(options_group)
        apply_layout_margins(
            options_layout,
            (
                20,  
                16,  
                20,  
                16,  
            ),
        )
        apply_layout_spacing(options_layout, 14)

        options_title = QLabel("Build Options")
        options_title.setObjectName("CompareCardTitle")
        options_layout.addWidget(options_title)

        target_row = QHBoxLayout()
        apply_layout_spacing(target_row, 10)
        self.edit_target = QLineEdit("")
        self.edit_target.setObjectName("CompareTargetInput")
        self.edit_target.setText(self._saved_target)
        self.edit_target.setPlaceholderText("Select where to build your library...")
        self.edit_target.textChanged.connect(lambda: self._refresh_compare_views(refresh_before=False))
        btn_browse = QPushButton("Browse...")
        btn_browse.setObjectName("CompareBrowseButton")
        btn_browse.clicked.connect(self._browse_target)
        target_label = QLabel("Target Directory")
        target_label.setObjectName("CompareFieldLabel")
        target_row.addWidget(target_label)
        target_row.addWidget(self.edit_target, 1)
        target_row.addWidget(btn_browse)
        options_layout.addLayout(target_row)
        self.target_error = QLabel(" ")
        self.target_error.setObjectName("CompareTargetError")
        self.target_error.setWordWrap(True)
        self.target_error.setFixedHeight(scaled_px(22))
        options_layout.addWidget(self.target_error)

        options_layout.addSpacing(6)
        checks_row = QHBoxLayout()
        apply_layout_spacing(checks_row, 16)
        self.check_move = QCheckBox("Move files (instead of copy)")
        self.check_flat = QCheckBox("Flat structure (ignore category folders)")
        self.check_no_px = QCheckBox("No Prefix (strip pack information from filenames)")
        self.check_move.setChecked(self.settings.value("exec_move", False, type=bool))
        self.check_flat.setChecked(self.settings.value("exec_flat", False, type=bool))
        self.check_no_px.setChecked(self.settings.value("exec_no_px", False, type=bool))
        self.check_move.toggled.connect(self._refresh_summary_footers)
        self.check_flat.toggled.connect(self._on_flat_toggled)
        self.check_no_px.toggled.connect(lambda: self._refresh_compare_views(refresh_before=False))
        checks_row.addWidget(self.check_move)
        checks_row.addWidget(self.check_flat)
        checks_row.addWidget(self.check_no_px)
        
        checks_row.addStretch()
        
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setObjectName("secondary")
        self.btn_cancel.clicked.connect(self.reject)
        checks_row.addWidget(self.btn_cancel)

        self.btn_build = QPushButton("BUILD LIBRARY")
        self.btn_build.setObjectName("primary")
        apply_style(self.btn_build, dock_save_search_button_style())
        self.btn_build.clicked.connect(self._accept_with_save)
        checks_row.addWidget(self.btn_build)
        
        options_layout.addLayout(checks_row)
        layout.addWidget(options_group)
        self._refresh_compare_views()

    def _make_tree_panel(self, title: str, tone: str) -> QWidget:
        panel = QWidget()
        panel.setObjectName("CompareTreePanel")
        panel.setProperty("compareTone", tone)
        panel_layout = QVBoxLayout(panel)
        apply_layout_margins(
            panel_layout,
            (
                20,  
                12,  
                20,  
                12, 
            ),
        )
        apply_layout_spacing(panel_layout, 8)
        header = QLabel(title)
        header.setObjectName("ComparePanelHeader")
        panel_layout.addWidget(header)
        footer = QLabel("")
        footer.setObjectName("ComparePanelFooter")
        footer.setWordWrap(True)
        panel_layout.addWidget(footer)
        if title == "Current Directories":
            self.before_footer = footer
        else:
            self.after_footer = footer
        return panel

    def _refresh_summary_footers(self):
        move_mode = "move" if self.check_move.isChecked() else "copy"
        is_flat = self.check_flat.isChecked()
        no_prefix = self.check_no_px.isChecked()
        if self._active_tree_profile is not None:
            structure = "custom tree + flat native levels" if is_flat else "custom tree"
        else:
            structure = "flat" if is_flat else "canonical folders"
        if is_flat:
            prefix_mode = "strip pack prefixes" if no_prefix else "add pack prefixes"
        else:
            prefix_mode = "keep source filenames"
        roots = self._source_tree_roots()
        self.before_footer.setText(f"{len(roots)} source root(s) | Mode: {move_mode}")
        self.after_footer.setText(
            f"{len(self.records)} projected files | {structure} | {prefix_mode}"
        )

    def _refresh_compare_views(self, *, refresh_before: bool = True):
        self._sync_target_dir()
        if not self.check_flat.isChecked() and self.check_no_px.isChecked():
            self.check_no_px.blockSignals(True)
            self.check_no_px.setChecked(False)
            self.check_no_px.blockSignals(False)
        self.check_no_px.setEnabled(self.check_flat.isChecked())
        self._refresh_build_button_state()
        self._refresh_summary_footers()
        if refresh_before:
            self._replace_panel_widget(self.before_panel, self._build_before_panel())
        self._replace_panel_widget(self.after_panel, self._build_after_panel())

    def _sync_target_dir(self) -> None:
        raw_target = self.edit_target.text().strip() or self._saved_target or str(Path.cwd())
        self.target_dir = Path(raw_target)

    def _target_validation_message(self) -> str:
        target = self.edit_target.text().strip()
        if not target:
            return "Select a target directory."
        return target_source_overlap_message(Path(target), self.source_roots)

    def _refresh_build_button_state(self) -> bool:
        message = self._target_validation_message()
        is_valid = not message
        self.btn_build.setEnabled(is_valid)
        if message:
            self.target_error.setText(message)
        else:
            self.target_error.setText(" ")
        return is_valid

    def _on_flat_toggled(self, checked):
        if not checked and self.check_no_px.isChecked():
            self.check_no_px.blockSignals(True)
            self.check_no_px.setChecked(False)
            self.check_no_px.blockSignals(False)
        self._refresh_compare_views(refresh_before=False)

    @staticmethod
    def _replace_panel_widget(panel, widget):
        layout = panel.layout()
        while layout.count() > 2:
            item = layout.takeAt(1)
            old_widget = item.widget()
            if old_widget is not None:
                old_widget.deleteLater()
        layout.insertWidget(1, widget, 1)

    def _accept_with_save(self):
        target = self.edit_target.text().strip()
        if not self._refresh_build_button_state():
            return
        self.settings.setValue("last_target", target)
        self.settings.setValue("exec_move", self.check_move.isChecked())
        if self._active_tree_profile is None:
            self.settings.setValue("exec_flat", self.check_flat.isChecked())
            self.settings.setValue("exec_no_px", self.check_no_px.isChecked())
        self.accept()

    def get_options(self):
        return {
            "target": self.edit_target.text().strip(),
            "move": self.check_move.isChecked(),
            "flat": self.check_flat.isChecked(),
            "no_px": self.check_no_px.isChecked(),
        }

    def _build_before_panel(self):
        if self._before_tree_widget is not None:
            return self._before_tree_widget
        tree = self._new_tree_widget()
        tree.setProperty("compareTone", "source")
        roots = self._source_tree_roots()
        if not roots:
            tree.addTopLevelItem(self._tree_item(["No source roots loaded", ""]))
        else:
            root_records: dict[Path, list] = {root: [] for root in roots}
            for rec in self.records:
                try:
                    sp = Path(getattr(rec, "source_path", ""))
                    for root in roots:
                        try:
                            sp.relative_to(root)
                            root_records[root].append(rec)
                            break
                        except ValueError:
                            continue
                except Exception:
                    continue
            for root in roots:
                self._populate_source_tree_from_records(tree, root, root_records[root])
        self._before_tree_widget = tree
        return tree

    def _build_after_panel(self):
        tree = self._new_tree_widget()
        tree.setProperty("compareTone", "target")
        self._populate_projected_tree(tree)
        return tree

    def _new_tree_widget(self):
        tree = QTreeWidget()
        tree.setHeaderLabels(["Name", "Info"])
        tree.setAlternatingRowColors(True)
        tree.setRootIsDecorated(True)
        tree.setUniformRowHeights(True)
        tree.setSelectionMode(QTreeWidget.NoSelection)
        tree.setLayoutDirection(Qt.LeftToRight)
        tree.viewport().setLayoutDirection(Qt.LeftToRight)
        tree.setItemDelegate(CompareTreeDelegate(parent=tree))
        tree.header().setStretchLastSection(False)
        tree.header().setLayoutDirection(Qt.LeftToRight)
        tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        tree.header().setSectionResizeMode(1, QHeaderView.Fixed)
        tree.setColumnWidth(1, BUILD_COMPARE_TREE_INFO_WIDTH)
        tree.header().setMinimumSectionSize(BUILD_COMPARE_TREE_INFO_MIN_WIDTH)
        return tree

    @staticmethod
    def _tree_item(values: list[str] | tuple[str, str]) -> QTreeWidgetItem:
        item = QTreeWidgetItem(list(values))
        row_size = QSize(0, scaled_px(COMPARE_TREE_ROW_HEIGHT))
        item.setSizeHint(0, row_size)
        item.setSizeHint(1, row_size)
        return item

    def _source_tree_roots(self):
        roots = []
        seen = set()
        for root in self.source_roots:
            try:
                resolved = root.resolve()
            except OSError:
                resolved = Path(root)
            key = str(resolved).lower()
            if key in seen:
                continue
            seen.add(key)
            roots.append(resolved)
        return roots

    def _populate_source_tree_from_records(self, tree: QTreeWidget, root: Path, records: list) -> None:
        top = self._tree_item([root.name, "folder"])
        tree.addTopLevelItem(top)

        child_folders: dict[str, int] = {}
        direct_files: list[str] = []

        for rec in records:
            try:
                sp = Path(getattr(rec, "source_path"))
                rel = sp.relative_to(root)
                parts = rel.parts
                if len(parts) <= 1:
                    direct_files.append(sp.name)
                else:
                    child_folders[parts[0]] = child_folders.get(parts[0], 0) + 1
            except (ValueError, TypeError, AttributeError):
                continue

        for name in sorted(direct_files, key=str.lower)[:MAX_COMPARE_FILES_PER_FOLDER]:
            top.addChild(self._tree_item([name, "file"]))
        if len(direct_files) > MAX_COMPARE_FILES_PER_FOLDER:
            top.addChild(self._tree_item(["...", f"{len(direct_files) - MAX_COMPARE_FILES_PER_FOLDER} more file(s)"]))

        for folder_name in sorted(child_folders.keys(), key=str.lower):
            count = child_folders[folder_name]
            top.addChild(self._tree_item([folder_name, f"{count} file(s)"]))

        top.setExpanded(True)

    def _populate_projected_tree(self, tree: QTreeWidget):
        target_root = self._tree_item([self.target_dir.name or str(self.target_dir), "target root"])
        tree.addTopLevelItem(target_root)

        self._populate_projected_destination_tree(target_root)
        tree.collapseAll()
        target_root.setExpanded(True)

    def _populate_projected_destination_tree(self, target_root: QTreeWidgetItem):
        projected = self._projected_tree_records()
        folder_items: dict[tuple[str, ...], QTreeWidgetItem] = {(): target_root}
        file_counts: dict[tuple[str, ...], int] = {}
        leaf_records: dict[tuple[str, ...], list[tuple[str, object]]] = {}

        for relative_path, rec in projected:
            parts = tuple(str(part) for part in relative_path.parts if str(part) not in {"", "."})
            if not parts:
                parts = (self._projected_output_filename(rec),)
            folder_parts = parts[:-1]
            filename = parts[-1]
            for depth, part in enumerate(folder_parts):
                key = folder_parts[: depth + 1]
                if key not in folder_items:
                    parent_item = folder_items[folder_parts[:depth]]
                    folder_item = self._tree_item([part, ""])
                    parent_item.addChild(folder_item)
                    folder_items[key] = folder_item
                for ancestor_depth in range(depth + 1):
                    ancestor_key = folder_parts[: ancestor_depth + 1]
                    file_counts[ancestor_key] = file_counts.get(ancestor_key, 0) + 1
            leaf_records.setdefault(folder_parts, []).append((filename, rec))

        for key, item in sorted(folder_items.items(), key=lambda kv: (len(kv[0]), kv[0])):
            if key:
                item.setText(1, f"{file_counts.get(key, 0)} file(s)")

        file_items_remaining = MAX_COMPARE_FILE_ITEMS
        hidden_file_count = 0
        for folder_parts, files in sorted(leaf_records.items(), key=lambda kv: tuple(part.lower() for part in kv[0])):
            parent_item = folder_items.get(folder_parts, target_root)
            visible_limit = min(MAX_COMPARE_FILES_PER_FOLDER, max(file_items_remaining, 0))
            sorted_files = sorted(files, key=lambda item: item[0].lower())
            for filename, rec in sorted_files[:visible_limit]:
                parent_item.addChild(self._tree_item([filename, "file"]))
            hidden_count = len(files) - min(len(files), visible_limit)
            if hidden_count > 0:
                parent_item.addChild(self._tree_item(["...", f"{hidden_count} more file(s)"]))
                hidden_file_count += hidden_count
            file_items_remaining -= min(len(files), visible_limit)

        target_info = f"{len(projected)} file(s)"
        if hidden_file_count:
            target_info = f"{target_info}, preview capped"
        target_root.setText(1, target_info)

    def _projected_tree_records(self):
        key = self._projected_cache_key()
        cached = self._projected_cache.get(key)
        if cached is not None:
            return list(cached)

        sorted_records = sorted(self.records, key=lambda r: str(getattr(r, "source_path", "")).lower())
        projected = self._projected_relative_paths_for_records(sorted_records)
        projected.sort(key=lambda item: tuple(part.lower() for part in item[0].parts))
        self._projected_cache[key] = list(projected)
        return projected

    def _projected_relative_paths_for_records(self, records: list) -> list[tuple[Path, object]]:
        if self._active_tree_profile is None:
            return [(self._preview_relative_path(rec), rec) for rec in records]

        flat = self.check_flat.isChecked()
        resolved_custom = self._tree_resolver.resolve_records(
            [
                rec
                for rec in records
                if not self._uses_default_preview_path(rec)
            ],
            self._active_tree_profile,
            flat=flat,
            append_native=True,
        )
        projected = []
        for rec in records:
            if self._uses_default_preview_path(rec):
                projected.append((self._preview_relative_path(rec), rec))
                continue
            relative_folder = resolved_custom.get(id(rec), Path("."))
            filename = self._default_projected_filename(rec)
            projected.append((relative_folder / filename, rec))
        return projected

    def _uses_default_preview_path(self, rec) -> bool:
        return bool(getattr(rec, "is_preserved", False)) or str(getattr(rec, "audio_type", "")) in {
            "Non-Audio Assets",
            "Utility",
        }

    def _preview_relative_path(self, rec) -> Path:
        if bool(getattr(rec, "is_preserved", False)):
            return self._preserved_projected_relative_path(rec)
        return self._default_projected_relative_path(rec)

    def _projected_cache_key(self) -> tuple:
        profile = self._active_tree_profile
        profile_key = (
            getattr(profile, "id", None),
            getattr(profile, "updated_at", None),
            len(getattr(profile, "nodes", []) or []),
        )
        return (
            str(self.target_dir),
            self.check_flat.isChecked(),
            self.check_no_px.isChecked(),
            profile_key,
            len(self.records),
        )

    def _projected_relative_path(self, rec, *, records: list | None = None) -> Path:
        if bool(getattr(rec, "is_preserved", False)):
            return self._preserved_projected_relative_path(rec)

        resolution = self._destination_resolver.resolve(
            rec,
            self.target_dir,
            self.check_flat.isChecked(),
            self.check_no_px.isChecked(),
            {},
            active_tree_profile=self._active_tree_profile,
            records=records or self.records,
        )
        return resolution.relative_path

    def _preserved_projected_relative_path(self, rec) -> Path:
        preserved_root = Path(getattr(rec, "preserved_root", None) or getattr(rec, "source_path"))
        source_path = Path(getattr(rec, "source_path"))
        dest_root = self._resolve_preserved_destination_root(preserved_root)
        try:
            rel_parts = source_path.relative_to(preserved_root).parts
        except ValueError:
            rel_parts = (source_path.name,)
        dest_path = dest_root.joinpath(*rel_parts)
        try:
            return dest_path.relative_to(self.target_dir)
        except (OSError, ValueError):
            return Path(dest_path.name)

    def _default_projected_relative_path(self, rec) -> Path:
        audio_type = str(getattr(rec, "audio_type", "") or "")
        pack = str(getattr(rec, "pack", "") or "Loose Files")
        if audio_type in {"Non-Audio Assets", "Utility"}:
            return Path("Non-Audio Assets") / pack / Path(getattr(rec, "source_path")).name

        category = str(getattr(rec, "category", "") or "Uncategorized")
        subcategory = str(getattr(rec, "subcategory", "") or "").strip()
        filename = self._default_projected_filename(rec)
        if self.check_flat.isChecked():
            base = Path(audio_type) / category
            return base / subcategory / filename if subcategory else base / filename
        base = Path(audio_type) / category
        return base / subcategory / pack / filename if subcategory else base / pack / filename

    def _default_projected_filename(self, rec) -> str:
        source_name = Path(getattr(rec, "source_path")).name
        audio_type = str(getattr(rec, "audio_type", "") or "")
        if audio_type in {"Non-Audio Assets", "Utility"}:
            return source_name
        if not self.check_flat.isChecked() or self.check_no_px.isChecked():
            return source_name
        pack = str(getattr(rec, "pack", "") or "")
        category = str(getattr(rec, "category", "") or "")
        prefix = get_pack_prefix(pack, category, audio_type)
        return f"{prefix}_{source_name}" if prefix else source_name

    def _resolve_preserved_destination_root(self, preserved_root: Path) -> Path:
        try:
            resolved_root = Path(preserved_root).resolve()
        except OSError:
            resolved_root = Path(preserved_root)
        source_roots = []
        for root in self.source_roots:
            try:
                source_roots.append(Path(root).resolve())
            except OSError:
                source_roots.append(Path(root))
        source_roots.sort(key=lambda path: len(path.parts), reverse=True)

        for source_root in source_roots:
            try:
                rel_parent = resolved_root.parent.relative_to(source_root)
            except ValueError:
                continue
            if rel_parent == Path("."):
                return self.target_dir / resolved_root.name
            candidate_parent = self.target_dir / rel_parent
            if candidate_parent.exists() and candidate_parent.is_dir():
                return candidate_parent / resolved_root.name
            return self.target_dir / resolved_root.name
        return self.target_dir / resolved_root.name

    def _projected_output_filename(self, rec):
        if not self.check_flat.isChecked():
            return rec.source_path.name
        if self.check_no_px.isChecked():
            return rec.source_path.name
        pack = str(getattr(rec, "pack", "") or "")
        category = str(getattr(rec, "category", "") or "")
        audio_type = str(getattr(rec, "audio_type", "") or "")
        prefix = get_pack_prefix(pack, category, audio_type)
        return f"{prefix}_{rec.source_path.name}" if prefix else rec.source_path.name

    @staticmethod
    def _format_file_size(path: Path):
        try:
            size = path.stat().st_size
        except OSError:
            return "file"
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024 or unit == "GB":
                return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
            size /= 1024.0
        return "file"

    def _browse_target(self):
        start_dir = self.edit_target.text().strip() or self._saved_target or self._default_browse_start_dir()
        path = QFileDialog.getExistingDirectory(self, "Select Target Library Root", start_dir)
        if path:
            self.edit_target.setText(path)

    def _default_browse_start_dir(self) -> str:
        roots = self._source_tree_roots()
        if roots:
            return str(roots[0])
        return str(Path.home())

    def refresh_theme(self):
        apply_style(self, build_page_style())
        for tree in self.findChildren(QTreeWidget):
            tree.viewport().update()
            tree.header().viewport().update()

