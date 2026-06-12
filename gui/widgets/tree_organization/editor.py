from __future__ import annotations

import uuid
from dataclasses import replace

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QBrush, QIcon, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileIconProvider,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QSizePolicy,
    QWidget,
)

from unshuffle.logic.tree_organization import (
    TreeOrganizationNode,
    TreeOrganizationProfile,
    TreeOrganizationResolver,
    make_empty_profile,
)
from unshuffle.logic.tree_organization.models import utc_now_iso

from ...utils.constants import DELETE_ICON, EDIT_ICON, LIB_TAB_TOOL_BUTTON_ICON_SIZE, REDO_ICON, SAVE_ICON, UNDO_ICON
from ...utils.layout_helpers import apply_layout_margins, apply_layout_spacing
from ...utils.styles import ColorPalette, apply_style, make_qcolor, scaled_px
from ..buttons import AnimatedIconButton
from ..filter_suggestion_line_edit import FilterSuggestionLineEdit
from ..filter_suggestions import build_filter_suggestions, saved_filter_queries
from .constants import ACTION_KIND_ROLE, ADD_PARENT_ID_ROLE, NODE_ID_ROLE
from .logic import TreeOrganizationEditorLogicMixin
from .styles import WARNING_TEXT, button_style, editor_style
from .tree_view import TreeOrganizationTreeView


class TreeOrganizationEditor(TreeOrganizationEditorLogicMixin, QDialog):
    profileApplied = Signal(object)
    profileSaved = Signal(object)
    profileDeleted = Signal(str)
    profileDisabled = Signal()

    def __init__(
        self,
        profiles: list[TreeOrganizationProfile],
        active_profile: TreeOrganizationProfile | None,
        records: list,
        parent=None,
        *,
        embedded: bool = False,
    ):
        super().__init__(parent)
        self._embedded = embedded
        self.setObjectName("TreeOrganizationEditorRoot")
        self.setWindowTitle("Edit Tree View Organization")
        self.resize(1120, 720)
        self._profiles = list(profiles)
        self._records = list(records)
        self._active_profile_id = active_profile.id if active_profile is not None else ""
        self._selected_profile_id = self._active_profile_id
        self._pending_profile = active_profile  
        self._editor_built = False
        self._update_tinted_icons()  
        self._setup_ui()
        self._show_profile_list()

    def _get_tinted_icon(self, icon_path, color_name) -> QIcon:
        from PySide6.QtGui import QPixmap, QPainter
        color = make_qcolor(color_name)
        pixmap = QPixmap(str(icon_path))
        if pixmap.isNull():
            return QIcon()
        painter = QPainter(pixmap)
        try:
            painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
            painter.fillRect(pixmap.rect(), color)
        finally:
            painter.end()
        return QIcon(pixmap)

    def _update_tinted_icons(self) -> None:
        self._icon_edit_light = self._get_tinted_icon(EDIT_ICON, ColorPalette.TEXT_LIGHT)
        self._icon_edit_inverse = self._get_tinted_icon(EDIT_ICON, ColorPalette.TEXT_INVERSE)
        self._icon_delete_light = self._get_tinted_icon(DELETE_ICON, ColorPalette.TEXT_LIGHT)
        self._icon_delete_inverse = self._get_tinted_icon(DELETE_ICON, ColorPalette.TEXT_INVERSE)
        self._icon_delete_danger = self._get_tinted_icon(DELETE_ICON, ColorPalette.DANGER)
        self._icon_save_inverse = self._get_tinted_icon(SAVE_ICON, ColorPalette.TEXT_INVERSE)

    def profile(self) -> TreeOrganizationProfile:
        return self._profile_from_ui()

    def set_active_profile(self, profile: TreeOrganizationProfile | None) -> None:
        self._active_profile_id = profile.id if profile is not None else ""
        self._selected_profile_id = self._active_profile_id
        self._render_profile_list()

    def open_current_profile_editor(self) -> None:
        if self._active_profile_id:
            profile = self._profile_by_id(self._active_profile_id)
            if profile is not None:
                self._show_editor_page(profile)

    def show_profile_list(self) -> None:
        self._show_profile_list()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            if self._editor_built and hasattr(self, "detail_panel"):
                self.detail_panel.hide()
            event.accept()
            return
        super().keyPressEvent(event)

    def _on_focus_changed(self, _old, now) -> None:
        if now is None or not self.detail_panel.isVisible():
            return
        if self.tree.isAncestorOf(now) or self.detail_panel.isAncestorOf(now) or now in {self.tree, self.detail_panel}:
            return
        if self.isVisible():
            self.detail_panel.hide()

    def _setup_ui(self) -> None:
        apply_style(self, editor_style())
        root_layout = QVBoxLayout(self)
        apply_layout_margins(root_layout, (0, 0, 0, 0))
        apply_layout_spacing(root_layout, 0)

        self.page_stack = QStackedWidget()
        root_layout.addWidget(self.page_stack, 1)

        self.profile_list_page = self._build_profile_list_page()
        self.page_stack.addWidget(self.profile_list_page)
        self.editor_page = None  

    def _ensure_editor_built(self) -> None:
        if self._editor_built:
            return
        self._editor_built = True
        self._saved_filter_suggestions = self._load_saved_filter_suggestions(self.parent())
        self._filter_suggestions = self._build_filter_suggestions()
        self._profile = self._pending_profile or (
            self._profiles[0] if self._profiles else make_empty_profile(f"profile_{uuid.uuid4().hex[:12]}", "Custom Tree")
        )
        self._nodes: list[TreeOrganizationNode] = []
        self._selected_id = self._profile.root_node_id
        self._match_count_cache: dict[str, int] = {}
        self._folder_icon = QFileIconProvider().icon(QFileIconProvider.Folder)
        self._tree_items: dict[str, QStandardItem] = {}
        self._node_lookup: dict[str, TreeOrganizationNode] = {}
        self._children_lookup: dict[str, list[TreeOrganizationNode]] = {}
        self._descendant_lookup: dict[str, set[str]] = {}
        self._syncing_fields = False
        self._counts_dirty = False
        self._undo_states: list[tuple[list[TreeOrganizationNode], str, str]] = []
        self._redo_states: list[tuple[list[TreeOrganizationNode], str, str]] = []
        self._count_refresh_timer = QTimer(self)
        self._count_refresh_timer.setSingleShot(True)
        self._count_refresh_timer.timeout.connect(self._refresh_counts_after_idle)
        self._build_editor_page()
        assert self.editor_page is not None
        self.page_stack.addWidget(self.editor_page)
        app = QApplication.instance()
        if app is not None:
            app.focusChanged.connect(self._on_focus_changed)
        self._load_profiles()
        self._load_profile(self._profile)

    def _build_editor_page(self) -> None:
        content = QFrame()
        content.setObjectName("TreeEditorContent")
        content.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.editor_page = content

        layout = QVBoxLayout(content)
        apply_layout_margins(layout, (12, 12, 12, 12))
        apply_layout_spacing(layout, 10)

        title = QLabel("Tree Organization")
        title.setObjectName("TreeEditorHeader")
        layout.addWidget(title)

        warning = QLabel(WARNING_TEXT)
        warning.setObjectName("TreeEditorHelpBanner")
        warning.setWordWrap(True)
        layout.addWidget(warning)

        top = QHBoxLayout()
        apply_layout_spacing(top, 8)
        self.profile_combo = QComboBox()
        self.profile_combo.setVisible(False)
        self.profile_combo.currentIndexChanged.connect(self._on_profile_selected)
        top.addWidget(QLabel("Profile"))
        self.profile_name = QLineEdit()
        self.profile_name.setPlaceholderText("Profile name")
        top.addWidget(self.profile_name, 1)
        self.btn_undo = AnimatedIconButton(
            UNDO_ICON,
            QSize(LIB_TAB_TOOL_BUTTON_ICON_SIZE, LIB_TAB_TOOL_BUTTON_ICON_SIZE),
        )
        self.btn_undo.setToolTip("Undo tree edit")
        self.btn_undo.clicked.connect(lambda checked=False: self._undo_tree_edit())
        top.addWidget(self.btn_undo)
        self.btn_redo = AnimatedIconButton(
            REDO_ICON,
            QSize(LIB_TAB_TOOL_BUTTON_ICON_SIZE, LIB_TAB_TOOL_BUTTON_ICON_SIZE),
        )
        self.btn_redo.setToolTip("Redo tree edit")
        self.btn_redo.clicked.connect(lambda checked=False: self._redo_tree_edit())
        top.addWidget(self.btn_redo)
        self.btn_back_to_list = QPushButton("Back")
        apply_style(self.btn_back_to_list, button_style())
        self.btn_back_to_list.clicked.connect(self._show_profile_list)
        top.addWidget(self.btn_back_to_list)
        self.btn_options = QPushButton("Options")
        apply_style(self.btn_options, button_style())
        self.btn_options.clicked.connect(self._show_options_menu)
        top.addWidget(self.btn_options)
        self.btn_new = QPushButton("New")
        self.btn_new.setVisible(False)
        apply_style(self.btn_new, button_style())
        self.btn_new.clicked.connect(self._new_profile)
        top.addWidget(self.btn_new)
        self.btn_delete = QPushButton("")
        self.btn_delete.setVisible(False)
        self._make_icon_button(self.btn_delete, self._icon_delete_light, "Delete tree")
        apply_style(self.btn_delete, button_style())
        self.btn_delete.clicked.connect(self._delete_profile)
        top.addWidget(self.btn_delete)
        self.btn_disable = QPushButton("Reset Custom Tree")
        self.btn_disable.setVisible(False)
        apply_style(self.btn_disable, button_style())
        self.btn_disable.clicked.connect(self._reset_custom_tree)
        top.addWidget(self.btn_disable)
        layout.addLayout(top)

        body = QHBoxLayout()
        apply_layout_spacing(body, 10)
        self.tree_model = QStandardItemModel(self)
        self.tree_model.setColumnCount(3)
        self.tree_model.setHorizontalHeaderLabels(["Folder", "Filter", "Actions"])
        self.tree = TreeOrganizationTreeView()
        self.tree.setModel(self.tree_model)
        self.tree.canDropNode = self._can_move_node
        self.tree.forbiddenDropParentsForNode = self._forbidden_drop_parent_ids
        self.tree.itemForNode = lambda node_id: self._tree_items.get(node_id)
        self.tree.setUniformRowHeights(True)
        self.tree.setRootIsDecorated(False)
        header = self.tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.Fixed)
        self.tree.setColumnWidth(2, scaled_px(80))
        self.tree.selectionModel().currentChanged.connect(self._on_tree_current_changed)
        self.tree.addChildRequested.connect(lambda node_id: self._insert_node(node_id))
        self.tree.focusDetailsRequested.connect(self._focus_details)
        self.tree.deleteNodeRequested.connect(self._remove_node_by_id)
        self.tree.moveNodeRequested.connect(self._move_node)
        body.addWidget(self.tree, 1)

        self.detail_panel = self._build_detail_panel()
        body.addWidget(self.detail_panel, 0, Qt.AlignTop)
        layout.addLayout(body, 1)

        bottom = QHBoxLayout()
        apply_layout_spacing(bottom, 8)
        self.validation_label = QLabel("")
        self.validation_label.setWordWrap(True)
        bottom.addWidget(self.validation_label, 1, Qt.AlignBottom)

        buttons = QDialogButtonBox()
        self.btn_save = buttons.addButton("Save", QDialogButtonBox.ActionRole)
        self.btn_apply = buttons.addButton("Save + Apply", QDialogButtonBox.AcceptRole)
        for button in (self.btn_save, self.btn_apply):
            apply_style(button, button_style("primary"))
        close = buttons.addButton(QDialogButtonBox.Close)
        apply_style(close, button_style())
        close.setVisible(not self._embedded)
        self.btn_save.clicked.connect(self._save)
        self.btn_apply.clicked.connect(self._apply)
        buttons.rejected.connect(self.reject)
        bottom.addWidget(buttons, 0, Qt.AlignBottom)
        layout.addLayout(bottom)

    def _build_profile_list_page(self) -> QFrame:
        page = QFrame()
        page.setObjectName("TreeEditorContent")
        page.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout = QVBoxLayout(page)
        apply_layout_margins(layout, (12, 12, 12, 12))
        apply_layout_spacing(layout, 10)

        title_row = QHBoxLayout()
        title = QLabel("Tree Organization")
        title.setObjectName("TreeEditorHeader")
        title_row.addWidget(title, 1)
        self.btn_set_active_from_list = QPushButton("Set Active")
        apply_style(self.btn_set_active_from_list, button_style("primary"))
        self.btn_set_active_from_list.clicked.connect(self._set_selected_profile_active)
        title_row.addWidget(self.btn_set_active_from_list)
        self.btn_new_from_list = QPushButton("New Tree")
        apply_style(self.btn_new_from_list, button_style("primary"))
        self.btn_new_from_list.clicked.connect(self._new_profile_from_list)
        title_row.addWidget(self.btn_new_from_list)
        layout.addLayout(title_row)

        warning = QLabel(WARNING_TEXT)
        warning.setObjectName("TreeEditorHelpBanner")
        warning.setWordWrap(True)
        layout.addWidget(warning)

        self.profile_scroll = QScrollArea()
        self.profile_scroll.setObjectName("TreeProfileScroll")
        self.profile_scroll.setWidgetResizable(True)
        self.profile_scroll.setFrameShape(QFrame.NoFrame)
        self.profile_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.profile_scroll_content = QWidget()
        self.profile_scroll_content.setObjectName("TreeProfileContent")
        self.profile_rows_layout = QVBoxLayout(self.profile_scroll_content)
        apply_layout_margins(self.profile_rows_layout, (0, 0, 0, 0))
        apply_layout_spacing(self.profile_rows_layout, 8)
        self.profile_rows_layout.setAlignment(Qt.AlignTop)
        self.profile_scroll.setWidget(self.profile_scroll_content)
        layout.addWidget(self.profile_scroll, 1)

        self.new_tree_dialog = QFrame()
        self.new_tree_dialog.setObjectName("TreeNewDialog")
        self.new_tree_dialog.setVisible(False)
        dialog_layout = QVBoxLayout(self.new_tree_dialog)
        apply_layout_margins(dialog_layout, (10, 8, 10, 8))
        apply_layout_spacing(dialog_layout, 2)
        controls_row = QHBoxLayout()
        apply_layout_margins(controls_row, (0, 0, 0, 0))
        apply_layout_spacing(controls_row, 8)
        name_label = QLabel("Tree name")
        controls_row.addWidget(name_label, 0, Qt.AlignVCenter)
        self.new_tree_name = QLineEdit()
        self.new_tree_name.setPlaceholderText("New tree name")
        self.new_tree_name.textChanged.connect(self._update_new_tree_name_availability)
        self.new_tree_name.returnPressed.connect(self._confirm_new_profile_name)
        controls_row.addWidget(self.new_tree_name, 1, Qt.AlignVCenter)
        self.btn_create_tree = QPushButton("Create")
        apply_style(self.btn_create_tree, button_style("primary"))
        self.btn_create_tree.clicked.connect(self._confirm_new_profile_name)
        controls_row.addWidget(self.btn_create_tree, 0, Qt.AlignVCenter)
        self.btn_cancel_new_tree = QPushButton("Cancel")
        apply_style(self.btn_cancel_new_tree, button_style())
        self.btn_cancel_new_tree.clicked.connect(self._hide_new_tree_prompt)
        controls_row.addWidget(self.btn_cancel_new_tree, 0, Qt.AlignVCenter)
        dialog_layout.addLayout(controls_row)

        alert_row = QHBoxLayout()
        apply_layout_margins(alert_row, (0, 0, 0, 0))
        apply_layout_spacing(alert_row, 8)
        alert_row.addSpacing(name_label.sizeHint().width() + 8)
        self.new_tree_error = QLabel("")
        self.new_tree_error.setObjectName("TreeNameError")
        self.new_tree_error.setText(" ")
        alert_row.addWidget(self.new_tree_error, 1)
        dialog_layout.addLayout(alert_row)
        layout.addWidget(self.new_tree_dialog)
        return page

    def _show_profile_list(self) -> None:
        if self._selected_profile_id is None:
            self._selected_profile_id = self._active_profile_id
        self._render_profile_list()
        self.page_stack.setCurrentWidget(self.profile_list_page)

    def _show_editor_page(self, profile: TreeOrganizationProfile) -> None:
        self._ensure_editor_built()
        self._load_profile(profile)
        assert self.editor_page is not None
        self.page_stack.setCurrentWidget(self.editor_page)

    def _render_profile_list(self) -> None:
        if not hasattr(self, "profile_rows_layout"):
            return
        while self.profile_rows_layout.count():
            item = self.profile_rows_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self.profile_rows_layout.addWidget(
            self._profile_row(
                None,
                "Default Tree",
                "Unshuffle's generated library tree.",
                active=not bool(self._active_profile_id),
                selected=not bool(self._selected_profile_id),
            )
        )
        profiles = [profile for profile in self._profiles if (profile.name or "").strip().lower() != "default"]
        if not profiles:
            empty = QLabel("No custom trees saved yet.")
            empty.setObjectName("TreeProfileMeta")
            self.profile_rows_layout.addWidget(empty)
            self._refresh_selected_profile_actions()
            return
        for profile in profiles:
            meta = f"{len(profile.nodes)} folder{'s' if len(profile.nodes) != 1 else ''}"
            self.profile_rows_layout.addWidget(
                self._profile_row(
                    profile,
                    profile.name or "Custom Tree",
                    meta,
                    active=profile.id == self._active_profile_id,
                    selected=profile.id == self._selected_profile_id,
                )
            )
        self._refresh_selected_profile_actions()

    def _profile_row(self, profile: TreeOrganizationProfile | None, name: str, meta: str, *, active: bool, selected: bool) -> QFrame:
        row = QFrame()
        row.setObjectName("TreeProfileRow")
        row.setProperty("active", active)
        row.setProperty("selected", selected)
        apply_style(row, editor_style())
        profile_id = profile.id if profile is not None else ""
        row.mousePressEvent = lambda event, selected_id=profile_id: self._select_profile_row(selected_id)
        layout = QHBoxLayout(row)
        apply_layout_margins(layout, (12, 6, 12, 6))
        apply_layout_spacing(layout, 8)

        text = QVBoxLayout()
        apply_layout_margins(text, (0, 0, 0, 0))
        title = QLabel(name)
        title.setObjectName("TreeProfileName")
        text.addWidget(title)
        subtitle = QLabel(meta)
        subtitle.setObjectName("TreeProfileMeta")
        text.addWidget(subtitle)
        layout.addLayout(text, 1)

        if active:
            active_label = QLabel("Active")
            active_label.setObjectName("TreeProfileActive")
            layout.addWidget(active_label)

        if profile is not None:
            btn_edit = QPushButton("")
            self._make_icon_button(btn_edit, self._icon_edit_light, "Edit this tree")
            apply_style(btn_edit, button_style())
            btn_edit.clicked.connect(lambda checked=False, item=profile: self._show_editor_page(item))
            layout.addWidget(btn_edit)

            btn_delete = QPushButton("")
            self._make_icon_button(btn_delete, self._icon_delete_inverse, "Delete tree")
            apply_style(btn_delete, button_style("danger"))
            btn_delete.clicked.connect(lambda checked=False, profile_id=profile.id: self._delete_profile_by_id(profile_id))
            layout.addWidget(btn_delete)
        return row

    def _make_icon_button(self, button: QPushButton, icon: QIcon, tooltip: str) -> None:
        button.setText("")
        button.setIcon(icon)
        button.setIconSize(QSize(scaled_px(16), scaled_px(16)))
        button.setToolTip(tooltip)
        button.setAccessibleName(tooltip)

    def _profile_by_id(self, profile_id: str) -> TreeOrganizationProfile | None:
        for profile in self._profiles:
            if profile.id == profile_id:
                return profile
        return None

    def _select_profile_row(self, profile_id: str) -> None:
        self._selected_profile_id = (profile_id or "")
        self._render_profile_list()

    def _refresh_selected_profile_actions(self) -> None:
        if not hasattr(self, "btn_set_active_from_list"):
            return
        selected_is_active = self._selected_profile_id == self._active_profile_id
        self.btn_set_active_from_list.setEnabled(not selected_is_active)

    def _set_selected_profile_active(self) -> None:
        if not self._selected_profile_id:
            self._set_default_active()
            return
        profile = self._profile_by_id(self._selected_profile_id)
        if profile is not None:
            self._set_profile_active(profile)

    def _set_default_active(self) -> None:
        self.profileDisabled.emit()
        self._active_profile_id = ""
        self._show_profile_list()

    def _set_profile_active(self, profile: TreeOrganizationProfile) -> None:
        self.profileApplied.emit(profile)
        self._active_profile_id = profile.id
        self._show_profile_list()

    def _new_profile_from_list(self) -> None:
        self.new_tree_error.setText(" ")
        self.new_tree_name.clear()
        self.new_tree_dialog.setVisible(True)
        self._update_new_tree_name_availability("")
        self.new_tree_name.setFocus()

    def _hide_new_tree_prompt(self) -> None:
        self.new_tree_dialog.setVisible(False)
        self.new_tree_error.setText(" ")

    def _confirm_new_profile_name(self) -> None:
        name = self.new_tree_name.text().strip()
        if not self._new_tree_name_available(name):
            self._update_new_tree_name_availability(name)
            return
        self._hide_new_tree_prompt()
        app = self.parent() if hasattr(self, "parent") else None
        library_tab = getattr(app, "library_tab", None) if app else None
        new_profile = self._make_default_shaped_profile(f"profile_{uuid.uuid4().hex[:12]}", name, self._records, library_tab)
        self._ensure_editor_built()
        self._load_profile(new_profile)
        assert self.editor_page is not None
        self.page_stack.setCurrentWidget(self.editor_page)

    def _update_new_tree_name_availability(self, text: str) -> None:
        name = (text or "").strip()
        available = self._new_tree_name_available(name)
        if not name:
            self.new_tree_error.setText(" ")
            self.new_tree_error.setProperty("available", False)
        elif available:
            self.new_tree_error.setText("Available")
            self.new_tree_error.setProperty("available", True)
        else:
            self.new_tree_error.setText("Not available")
            self.new_tree_error.setProperty("available", False)
        self.new_tree_error.style().unpolish(self.new_tree_error)
        self.new_tree_error.style().polish(self.new_tree_error)
        self.btn_create_tree.setEnabled(available)

    def _new_tree_name_available(self, name: str) -> bool:
        normalized = self._normalize_profile_name(name)
        if not normalized:
            return False
        return not self._profile_name_conflict(name)

    def _profile_name_conflict(self, name: str, *, exclude_profile_id: str | None = None) -> bool:
        normalized = self._normalize_profile_name(name)
        if not normalized:
            return False
        for profile in self._profiles:
            if exclude_profile_id is not None and profile.id == exclude_profile_id:
                continue
            if self._normalize_profile_name(profile.name) == normalized:
                return True
        return False

    @staticmethod
    def _normalize_profile_name(name: str) -> str:
        return " ".join((name or "").strip().casefold().split())

    def _load_saved_filter_suggestions(self, parent) -> list[str]:
        controller = getattr(parent, "settings_controller", None)
        if controller is None:
            return []
        try:
            filters = controller.get_saved_filters()
        except Exception:
            return []
        return saved_filter_queries(filters)

    def _build_filter_suggestions(self) -> list[str]:
        return build_filter_suggestions(self._records, self._saved_filter_suggestions)

    def _build_detail_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("TreeEditorDetail")
        panel.setFixedWidth(scaled_px(320))
        panel.setFixedHeight(scaled_px(420))
        panel.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        panel_layout = QVBoxLayout(panel)
        apply_layout_margins(panel_layout, (12, 14, 12, 14))
        apply_layout_spacing(panel_layout, 10)

        folder_header = QFrame()
        folder_header.setObjectName("TreeEditorFolderHeader")
        folder_header.setMinimumHeight(scaled_px(64))
        folder_layout = QHBoxLayout(folder_header)
        apply_layout_margins(folder_layout, (10, 8, 10, 8))
        apply_layout_spacing(folder_layout, 8)
        icon_label = QLabel()
        icon_label.setPixmap(self._folder_icon.pixmap(scaled_px(20), scaled_px(20)))
        folder_layout.addWidget(icon_label, 0, Qt.AlignVCenter)
        self.folder_title_label = QLabel("Folder Details")
        self.folder_title_label.setObjectName("TreeEditorFolderTitle")
        self.folder_title_label.setWordWrap(False)
        folder_layout.addWidget(self.folder_title_label, 1, Qt.AlignVCenter)
        self.selected_label = QLabel("")
        self.selected_label.setObjectName("TreeEditorFolderMeta")
        self.selected_label.setWordWrap(False)
        self.selected_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        folder_layout.addWidget(self.selected_label, 0, Qt.AlignVCenter)
        panel_layout.addWidget(folder_header)

        name_label = QLabel("Folder name")
        name_label.setObjectName("TreeFieldLabel")
        panel_layout.addWidget(name_label)
        self.node_name = QLineEdit()
        self.node_name.textChanged.connect(lambda _text: self._refresh_detail_action_state())
        panel_layout.addWidget(self.node_name)
        filter_label = QLabel("Filter")
        filter_label.setObjectName("TreeFieldLabel")
        panel_layout.addWidget(filter_label)
        self.node_filter = FilterSuggestionLineEdit(
            popup_object_name="TreeFilterCompleter",
            popup_style_provider=editor_style,
        )
        self.node_filter.setPlaceholderText('Example: tag:"warm"')
        self.node_filter.set_suggestions(self._filter_suggestions, self._saved_filter_suggestions)
        self.node_filter.textChanged.connect(lambda _text: self._refresh_detail_action_state())
        panel_layout.addWidget(self.node_filter)
        self.node_hide_subbranches = QCheckBox("Hide sub-branches")
        self.node_hide_subbranches.setToolTip("Show matching files directly under this folder in the Library tree.")
        self.node_hide_subbranches.toggled.connect(lambda _checked: self._refresh_detail_action_state())
        panel_layout.addWidget(self.node_hide_subbranches)
        panel_layout.addSpacing(scaled_px(2))
        self.node_type = QComboBox()
        self.node_type.addItems(["custom", "system", "fallback"])
        self.node_type.currentTextChanged.connect(lambda _text: self._refresh_detail_action_state())
        self.node_type.setVisible(False)

        self.node_actions = QFrame()
        self.node_actions.setObjectName("TreeEditorActionRow")
        action_row = QHBoxLayout(self.node_actions)
        apply_layout_margins(action_row, (0, 0, 0, 0))
        apply_layout_spacing(action_row, 6)
        self.btn_update = QPushButton("")
        self._make_icon_button(self.btn_update, self._icon_save_inverse, "Update folder")
        apply_style(self.btn_update, button_style("folder"))
        self.btn_update.clicked.connect(self._update_selected)
        action_row.addWidget(self.btn_update)
        self.btn_remove = QPushButton("")
        self._make_icon_button(self.btn_remove, self._icon_delete_inverse, "Delete folder")
        apply_style(self.btn_remove, button_style("danger"))
        self.btn_remove.clicked.connect(self._remove_selected)
        action_row.addWidget(self.btn_remove)
        panel_layout.addWidget(self.node_actions)
        panel_layout.addSpacing(scaled_px(2))

        self.btn_child = QPushButton("Add child")
        self.btn_child.setObjectName("TreeAddChildButton")
        apply_style(self.btn_child, button_style("add_child"))
        self.btn_child.clicked.connect(self._add_child_node)
        panel_layout.addWidget(self.btn_child)
        panel_layout.addSpacing(scaled_px(4))

        logic_hint = QLabel("Use AND, comma, or & to require multiple matches. Use OR or | to accept either match.")
        logic_hint.setObjectName("TreeFilterHint")
        logic_hint.setWordWrap(True)
        panel_layout.addWidget(logic_hint)

        panel_layout.addStretch(1)
        return panel

    def refresh_theme(self) -> None:
        self._update_tinted_icons()
        apply_style(self, editor_style())
        for button in (
            self.btn_set_active_from_list,
            self.btn_new_from_list,
            self.btn_create_tree,
            self.btn_cancel_new_tree,
        ):
            apply_style(button, button_style())
        if self._editor_built:
            self._make_icon_button(self.btn_update, self._icon_save_inverse, "Update folder")
            self._make_icon_button(self.btn_remove, self._icon_delete_inverse, "Delete folder")
            self._make_icon_button(self.btn_delete, self._icon_delete_light, "Delete tree")
            for button in (
                self.btn_back_to_list,
                self.btn_options,
                self.btn_new,
                self.btn_delete,
                self.btn_disable,
            ):
                apply_style(button, button_style())
            apply_style(self.btn_child, button_style("add_child"))
            apply_style(self.btn_update, button_style("folder"))
            apply_style(self.btn_remove, button_style("danger"))
            apply_style(self.node_actions, editor_style())
            for button in (self.btn_save, self.btn_apply):
                apply_style(button, button_style("primary"))
            if hasattr(self.node_filter, "refresh_theme"):
                self.node_filter.refresh_theme()
            self.tree.viewport().update()
            self.detail_panel.update()
        self._render_profile_list()
