from __future__ import annotations

from typing import Iterable

from PySide6.QtCore import Qt, Signal, QRect
from PySide6.QtGui import QAction, QColor, QPainter
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QComboBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QStyledItemDelegate,
    QStyle,
)

from unshuffle.core.constants import CATEGORIES

from .carousels import SidebarCarousel
from .labels import section_label
from .refinement_popup import RefinementTargetCombo
from ..utils.constants import (
    LIB_TAB_CONTENT_GAP,
    LIB_TAB_CONTENT_ZERO_MARGINS,
    LIB_TAB_SEARCH_BUTTON_HEIGHT,
    LIB_TAB_TOOL_BUTTON_ICON_SIZE,
    LIB_TAB_TOOLBAR_SPACING,
    SIDEBAR_HEADER_HEIGHT,
    SIDEBAR_MINOR_SECTION_SPACING,
    SIDEBAR_OUTER_MARGIN,
    SIDEBAR_OUTER_MARGIN_LEFT,
    SIDEBAR_OUTER_MARGIN_TOP,
    SIDEBAR_ROW_SPACING,
    SIDEBAR_SECTION_SPACING,
    SIDEBAR_WIDTH,
    WORKSPACE_CARD_MARGINS,
    WORKSPACE_CARD_V_SPACING,
    WORKSPACE_ROOT_MARGINS,
    WORKSPACE_ROOT_SPACING,
)
from ..utils.layout_helpers import apply_layout_margins, apply_layout_spacing
from ..utils.styles import (
    ColorPalette,
    make_qcolor,
    apply_style,
    button_style,
    sidebar_base_style,
    sidebar_header_style,
    sidebar_title_style,
    section_label_style,
    workspace_card_style,
    workspace_sidebar_button_style,
    workspace_banner_style,
    workspace_field_label_style,
    workspace_input_style,
    menu_style,
    workspace_primary_button_style,
    workspace_table_widget_style,
    scaled_px,
    vertical_header_style,
)
from .refinement_styles import anchor_action_combo_style, refinement_target_combo_style
from ..utils.widget_helpers import apply_fixed_height, apply_fixed_width


SYSTEM_CATEGORY_EXCLUSIONS = {"All", "Uncategorized", "Non-Audio Assets"}
ANCHOR_ID_ROLE = Qt.UserRole + 20
ANCHOR_PREVIEW_PATH_ROLE = Qt.UserRole + 21
ANCHOR_EXAMPLES_ROLE = Qt.UserRole + 22
ANCHOR_COHESION_RATIO_ROLE = Qt.UserRole + 23
ANCHOR_REFERENCE_PATHS_ROLE = Qt.UserRole + 24
ANCHOR_QUALITY_ROLE = Qt.UserRole + 25
ANCHOR_ACTION_ROLE = Qt.UserRole + 26


class SystemTableWidget(QTableWidget):
    spacePressed = Signal(int)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Space:
            row = self.currentRow()
            if row >= 0:
                self.spacePressed.emit(row)
                event.accept()
                return
        super().keyPressEvent(event)


class NumericTableWidgetItem(QTableWidgetItem):
    def __lt__(self, other):
        left = self.data(Qt.UserRole)
        right = other.data(Qt.UserRole) if isinstance(other, QTableWidgetItem) else None
        if isinstance(left, (int, float)) and isinstance(right, (int, float)):
            return left < right
        return super().__lt__(other)


class ActionComboBox(QComboBox):
    """QComboBox that ignores wheel events to prevent accidental selection changes on scroll."""
    def wheelEvent(self, event) -> None:
        event.ignore()


class SystemTableDelegate(QStyledItemDelegate):
    """Paints System table rows and classification pills matching Review Outliers popup styling."""

    def paint(self, painter, option, index) -> None:
        self.initStyleOption(option, index)
        

        bg = index.data(Qt.BackgroundRole)
        if isinstance(bg, QColor):
            painter.fillRect(option.rect, bg)
        else:
            painter.fillRect(option.rect, make_qcolor(ColorPalette.BG_LIST))

        if option.state & QStyle.State_Selected:
            painter.fillRect(option.rect, make_qcolor(ColorPalette.TABLE_SELECT))
        elif option.state & QStyle.State_MouseOver:
            painter.fillRect(option.rect, make_qcolor(ColorPalette.TABLE_HOVER))

        col = index.column()
        if col == 0:  
            pass
        elif col == 2:  
            self._paint_cohesion_bar(painter, option, index)
        else:
            self._paint_text(painter, option)

        painter.save()
        self._paint_separators(painter, option.rect)
        painter.restore()

    def _paint_text(self, painter: QPainter, option) -> None:
        painter.save()
        painter.setPen(make_qcolor(ColorPalette.TEXT_MAIN))
        margin = scaled_px(12)
        text_rect = option.rect.adjusted(margin, 0, -margin, 0)
        text = option.fontMetrics.elidedText(option.text, Qt.ElideRight, max(1, text_rect.width()))
        painter.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft, text)
        painter.restore()

    def _paint_taxonomy_pill(self, painter: QPainter, option, index) -> None:
        audio_type = str(index.data(Qt.UserRole + 1) or "")
        category = str(index.data(Qt.UserRole) or "")
        subcategory = str(index.data(Qt.UserRole + 2) or "")
        
        if not category and not audio_type:
         
            self._paint_text(painter, option)
            return
            
        from .refinement_taxonomy import _paint_taxonomy_pills
        _paint_taxonomy_pills(painter, option.rect, option.fontMetrics, audio_type, category, subcategory)

    def _paint_cohesion_bar(self, painter: QPainter, option, index) -> None:
        quality = str(index.data(ANCHOR_QUALITY_ROLE) or "")
        if quality == "too_broad":
            painter.save()
            painter.setPen(make_qcolor(ColorPalette.WARNING))
            margin = scaled_px(12)
            text_rect = option.rect.adjusted(margin, 0, -margin, 0)
            painter.drawText(text_rect, Qt.AlignCenter, "Too broad")
            painter.restore()
            return

        ratio = index.data(ANCHOR_COHESION_RATIO_ROLE)
        try:
            ratio_float = float(ratio)
        except (TypeError, ValueError):
            ratio_float = 0.0
        pct = 0.0 if ratio_float <= 0 else max(0.0, min(1.0, 1.0 / ratio_float))
        track_width = min(scaled_px(92), max(1, option.rect.width() - scaled_px(44)))
        track = QRect(0, 0, track_width, scaled_px(4))
        track.moveCenter(option.rect.center())
        fill = QRect(track)
        fill.setWidth(int(track.width() * pct))
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(make_qcolor(ColorPalette.BG_MED))
        painter.drawRoundedRect(track, scaled_px(2), scaled_px(2))
        painter.setBrush(make_qcolor(ColorPalette.PRIMARY))
        painter.drawRoundedRect(fill, scaled_px(2), scaled_px(2))
        painter.restore()

    def _paint_separators(self, painter: QPainter, rect: QRect) -> None:
        line = make_qcolor(ColorPalette.BORDER_LIGHT)
        line.setAlpha(10 if make_qcolor(ColorPalette.BG_LIST).lightness() < 120 else 14)
        painter.setPen(line)
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())
        painter.drawLine(rect.topRight(), rect.bottomRight())


class SystemPage(QWidget):
    """System workspace for tree organization, discovery, additions, corrections, and anchors."""

    lookupRequested = Signal(str, str)
    addAliasesRequested = Signal(list, str)
    refreshDiscoveryRequested = Signal()
    gapAddRequested = Signal(str)
    refreshAdditionsRequested = Signal()
    removeAdditionsRequested = Signal(list)
    importAdditionsRequested = Signal()
    exportAdditionsRequested = Signal()
    refreshCorrectionsRequested = Signal()
    removeCorrectionsRequested = Signal(list)
    resetCorrectionsRequested = Signal()
    removeVerifiedAnchorsRequested = Signal(list)
    importAnchorsRequested = Signal()
    promoteAnchorsRequested = Signal(list)
    ignoreAnchorsRequested = Signal(list)
    anchorCandidateActionChanged = Signal(str, str)
    saveAnchorCandidateDraftRequested = Signal(bool)
    discardAnchorCandidateDraftRequested = Signal()
    exportAnchorsRequested = Signal(list)
    previewAnchorRequested = Signal(str)
    anchorSoundGroupChanged = Signal(str, str, str, str)
    treeOrganizationRequested = Signal()
    runCoherenceRequested = Signal()
    continuousRefinementRequested = Signal()
    autoCheckCoherenceChanged = Signal(bool)
    sectionChanged = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._can_write = False
        self._lookup_ready = False
        self._lookup_allows_add = False
        self.tree_organization_panel = None
        self.btn_nav_tree_organization = None
        self._section_headers: list[tuple[QWidget, QLabel]] = []
        self._anchor_status_level = "info"
        self._populating_anchor_rows = False
        self._anchor_preview_offsets: dict[str, int] = {}
        self._anchor_candidate_actions: dict[str, str] = {}
        self._setup_ui()
        self.set_mode(False)

    def _setup_ui(self) -> None:
        root = QHBoxLayout(self)
        apply_layout_margins(root, WORKSPACE_ROOT_MARGINS)
        apply_layout_spacing(root, LIB_TAB_CONTENT_GAP)

        self.sidebar = self._build_sidebar()
        root.addWidget(self.sidebar)

        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)

        self.discovery_panel = self._build_discovery_panel()
        self.additions_panel = self._build_additions_panel()
        self.corrections_panel = self._build_corrections_panel()
        self.my_anchors_panel = self._build_my_anchors_panel()
        self.anchors_panel = self._build_anchors_panel()
        for panel in (
            self.discovery_panel,
            self.additions_panel,
            self.corrections_panel,
            self.my_anchors_panel,
            self.anchors_panel,
        ):
            self.stack.addWidget(panel)

        self._refresh_theme()
        self._set_section("tree_organization")

    def _build_sidebar(self) -> QFrame:
        sidebar = QFrame()
        sidebar.setObjectName("LibrarySidebar")
        apply_fixed_width(sidebar, SIDEBAR_WIDTH)
        apply_style(sidebar, sidebar_base_style())

        layout = QVBoxLayout(sidebar)
        sidebar_margins = (
            SIDEBAR_OUTER_MARGIN_LEFT,
            SIDEBAR_OUTER_MARGIN_TOP,
            SIDEBAR_OUTER_MARGIN,
            SIDEBAR_OUTER_MARGIN,
        )
        apply_layout_margins(layout, sidebar_margins)
        apply_layout_spacing(layout, 0)
        inner_width = max(1, SIDEBAR_WIDTH - sidebar_margins[0] - sidebar_margins[2])
        body_width = max(1, inner_width - SIDEBAR_OUTER_MARGIN * 2)

        self.sidebar_header = QWidget()
        apply_fixed_height(self.sidebar_header, SIDEBAR_HEADER_HEIGHT)
        self.sidebar_header.setFixedWidth(inner_width)
        apply_style(self.sidebar_header, sidebar_header_style())
        header_layout = QHBoxLayout(self.sidebar_header)
        apply_layout_margins(header_layout, LIB_TAB_CONTENT_ZERO_MARGINS)
        self.sidebar_title = QLabel("System")
        apply_style(self.sidebar_title, sidebar_title_style())
        header_layout.addWidget(self.sidebar_title)
        layout.addWidget(self.sidebar_header, 0, Qt.AlignHCenter)

        layout.addSpacing(SIDEBAR_SECTION_SPACING)
        self.sidebar_body = QWidget()
        self.sidebar_body.setFixedWidth(body_width)
        body_layout = QVBoxLayout(self.sidebar_body)
        apply_layout_margins(body_layout, LIB_TAB_CONTENT_ZERO_MARGINS)
        apply_layout_spacing(body_layout, SIDEBAR_ROW_SPACING)

        def add_section_title(text: str) -> None:
            if body_layout.count():
                body_layout.addSpacing(SIDEBAR_MINOR_SECTION_SPACING)
            label = section_label(text)
            body_layout.addWidget(label)

        def add_nav_button(button: QPushButton) -> QPushButton:
            button.setFixedWidth(body_width)
            body_layout.addWidget(button)
            return button

        add_section_title("Structure")
        self.btn_nav_tree_organization = add_nav_button(self._sidebar_action("Tree Organization", self._open_tree_organization))

        add_section_title("Word Classification Tuning")
        self.btn_nav_discovery = add_nav_button(self._sidebar_action("Discovery", lambda: self._set_section("discovery")))
        self.btn_nav_additions = add_nav_button(self._sidebar_action("My Additions", lambda: self._set_section("additions")))
        self.btn_nav_corrections = add_nav_button(
            self._sidebar_action("Learned Corrections", lambda: self._set_section("corrections"))
        )

        add_section_title("Sound Classification Tuning")
        self.btn_nav_my_anchors = add_nav_button(self._sidebar_action("My Anchors", lambda: self._set_section("my_anchors")))
        self.btn_nav_anchors = add_nav_button(self._sidebar_action("Anchor Candidates", lambda: self._set_section("anchors")))

        layout.addWidget(self.sidebar_body, 0, Qt.AlignHCenter)
        layout.addStretch(1)
        return sidebar

    def _sidebar_action(self, text: str, slot) -> QPushButton:
        button = QPushButton(text)
        button.clicked.connect(slot)
        apply_style(button, workspace_sidebar_button_style())
        return button

    def _card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("WorkspaceCard")
        apply_style(card, workspace_card_style())
        return card

    def _build_my_anchors_panel(self) -> QFrame:
        card = self._card()
        layout = QVBoxLayout(card)
        apply_layout_margins(layout, WORKSPACE_CARD_MARGINS)
        apply_layout_spacing(layout, WORKSPACE_ROOT_SPACING)
        title_row = QHBoxLayout()
        apply_layout_margins(title_row, LIB_TAB_CONTENT_ZERO_MARGINS)
        title_row.addWidget(self._section_header("My Anchors"), 1)
        self.btn_remove_verified_anchors = QPushButton("Remove")
        self.btn_remove_verified_anchors.clicked.connect(lambda: self.removeVerifiedAnchorsRequested.emit(self.selected_my_anchors()))
        title_row.addWidget(self.btn_remove_verified_anchors)
        self.btn_import_verified_anchors = QPushButton("Import")
        self.btn_import_verified_anchors.clicked.connect(self.importAnchorsRequested.emit)
        title_row.addWidget(self.btn_import_verified_anchors)
        self.btn_export_verified_anchors = QPushButton("Export")
        self.btn_export_verified_anchors.clicked.connect(lambda: self.exportAnchorsRequested.emit([]))
        title_row.addWidget(self.btn_export_verified_anchors)
        layout.addLayout(title_row)
        self.my_anchor_status = QLabel("Files representing a specific type of sound for a particular category or subcategory.")
        self.my_anchor_status.setWordWrap(True)
        apply_style(self.my_anchor_status, workspace_banner_style())
        layout.addWidget(self.my_anchor_status)
        self.my_anchors_table = self._anchor_table(include_action=False)
        layout.addWidget(self.my_anchors_table, 1)
        return card

    def _build_anchors_panel(self) -> QFrame:
        card = self._card()
        layout = QVBoxLayout(card)
        apply_layout_margins(layout, WORKSPACE_CARD_MARGINS)
        apply_layout_spacing(layout, WORKSPACE_ROOT_SPACING)
        title_row = QHBoxLayout()
        apply_layout_margins(title_row, LIB_TAB_CONTENT_ZERO_MARGINS)
        title_row.addWidget(self._section_header("Anchor Candidates"), 1)
        self.btn_promote_anchors = QPushButton("Promote")
        self.btn_promote_anchors.clicked.connect(lambda: self.promoteAnchorsRequested.emit(self.selected_anchors()))
        title_row.addWidget(self.btn_promote_anchors)
        self.btn_ignore_anchors = QPushButton("Ignore")
        self.btn_ignore_anchors.clicked.connect(lambda: self.ignoreAnchorsRequested.emit(self.selected_anchors()))
        title_row.addWidget(self.btn_ignore_anchors)
        layout.addLayout(title_row)
        self.anchor_candidates_help = QLabel(
            "Files that could represent a specific type of sound for a category or subcategory. Double-click a row to preview."
        )
        self.anchor_candidates_help.setWordWrap(True)
        apply_style(self.anchor_candidates_help, workspace_banner_style())
        layout.addWidget(self.anchor_candidates_help)
        self.anchor_status = QLabel("")
        self.anchor_status.setWordWrap(True)
        self.anchor_status.setVisible(False)
        apply_style(self.anchor_status, workspace_banner_style("success"))
        layout.addWidget(self.anchor_status)
        self.anchors_table = self._anchor_table(include_action=True)
        self.anchors_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.anchors_table.customContextMenuRequested.connect(self._show_anchor_candidates_menu)
        layout.addWidget(self.anchors_table, 1)
        draft_row = QHBoxLayout()
        apply_layout_margins(draft_row, LIB_TAB_CONTENT_ZERO_MARGINS)
        apply_layout_spacing(draft_row, LIB_TAB_TOOLBAR_SPACING)
        self.anchor_draft_summary = QLabel("No anchor draft changes")
        self.anchor_draft_summary.setWordWrap(False)
        apply_style(self.anchor_draft_summary, workspace_field_label_style())
        draft_row.addWidget(self.anchor_draft_summary, 1)
        draft_row.addStretch(1)
        self.btn_discard_anchor_draft = QPushButton("Discard")
        self.btn_discard_anchor_draft.clicked.connect(self.discardAnchorCandidateDraftRequested.emit)
        draft_row.addWidget(self.btn_discard_anchor_draft)
        self.btn_save_anchor_draft = QPushButton("Save")
        self.btn_save_anchor_draft.clicked.connect(lambda: self.saveAnchorCandidateDraftRequested.emit(False))
        draft_row.addWidget(self.btn_save_anchor_draft)
        self.btn_apply_anchor_draft = QPushButton("Save and Apply")
        self.btn_apply_anchor_draft.clicked.connect(lambda: self.saveAnchorCandidateDraftRequested.emit(True))
        draft_row.addWidget(self.btn_apply_anchor_draft)
        layout.addLayout(draft_row)
        return card

    def _anchor_table(self, *, include_action: bool = False) -> SystemTableWidget:
        headers = ["Sound group", "Examples", "Cohesion"]
        if include_action:
            headers.append("Action")
        table = self._table(headers, show_row_numbers=True)
        table.setProperty("anchor_actions_enabled", include_action)
        table.setItemDelegate(SystemTableDelegate(table))
        table.cellDoubleClicked.connect(lambda row, col, source=table: self._handle_anchor_double_click(source, row, col))
        table.spacePressed.connect(lambda row, source=table: self._preview_anchor_row(source, row))
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        table.setColumnWidth(0, scaled_px(210))
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        if include_action:
            table.horizontalHeader().setStretchLastSection(False)
            table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Fixed)
            table.setColumnWidth(3, self._anchor_action_column_width(table))
        table.setSortingEnabled(True)
        return table

    def _section_header(self, title: str) -> QWidget:
        header = QWidget()
        apply_fixed_height(header, SIDEBAR_HEADER_HEIGHT)
        apply_style(header, sidebar_header_style())
        layout = QHBoxLayout(header)
        apply_layout_margins(layout, LIB_TAB_CONTENT_ZERO_MARGINS)
        label = QLabel(title)
        apply_style(label, sidebar_title_style())
        layout.addWidget(label, 1)
        self._section_headers.append((header, label))
        return header

    def _table(self, headers: list[str], show_row_numbers: bool = True) -> SystemTableWidget:
        table = SystemTableWidget(0, len(headers))
        apply_style(table, workspace_table_widget_style())
        table.setHorizontalHeaderLabels(headers)
        table.setAlternatingRowColors(True)
        table.setShowGrid(False)
        table.setWordWrap(False)
        vh = table.verticalHeader()
        if show_row_numbers:
            vh.setVisible(True)
            apply_style(vh, vertical_header_style())
            vh.setDefaultSectionSize(scaled_px(38))
            vh.setDefaultAlignment(Qt.AlignCenter)
            apply_fixed_width(vh, scaled_px(42))
        else:
            vh.setVisible(False)
            vh.setDefaultSectionSize(scaled_px(32))
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        table.horizontalHeader().setStretchLastSection(True)
        for idx in range(len(headers)):
            table.horizontalHeader().setSectionResizeMode(idx, QHeaderView.Stretch)
        return table

    def _preview_discovery_row(self, row: int) -> None:
        item = self.uncategorized_table.item(row, 2)
        path = item.text() if item else ""
        if path:
            self.previewAnchorRequested.emit(str(path))

    def _build_discovery_panel(self) -> QFrame:
        card = self._card()
        layout = QVBoxLayout(card)
        apply_layout_margins(layout, WORKSPACE_CARD_MARGINS)
        apply_layout_spacing(layout, WORKSPACE_ROOT_SPACING)
        layout.addWidget(self._section_header("Uncategorized Items"))

        self.discovery_help = QLabel(
            "Uncategorized files are sounds the classifier could not place with enough confidence. Repeated meaningful words from these files may be auto-suggested as aliases under Taxonomy Gaps."
        )
        self.discovery_help.setWordWrap(True)
        apply_style(self.discovery_help, workspace_banner_style())
        layout.addWidget(self.discovery_help)
        self.uncategorized_table = self._table(["File", "Package", "Path", "Confidence"], show_row_numbers=True)
        self.uncategorized_table.cellDoubleClicked.connect(lambda row, _col: self._preview_discovery_row(row))
        self.uncategorized_table.spacePressed.connect(self._preview_discovery_row)
        layout.addWidget(self.uncategorized_table, 2)
        self.gaps_header = self._section_header("Probable Taxonomy Gaps")
        layout.addWidget(self.gaps_header)
        self.gaps_table = self._table(["Token", "Files", "Context"], show_row_numbers=False)
        self.gaps_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.gaps_table.customContextMenuRequested.connect(self._show_gaps_menu)
        layout.addWidget(self.gaps_table, 1)
        return card

    def _build_alias_controls(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        apply_layout_margins(layout, LIB_TAB_CONTENT_ZERO_MARGINS)
        apply_layout_spacing(layout, WORKSPACE_CARD_V_SPACING)
        row = QHBoxLayout()
        apply_layout_margins(row, LIB_TAB_CONTENT_ZERO_MARGINS)
        apply_layout_spacing(row, LIB_TAB_TOOLBAR_SPACING)
        self.lbl_alias = QLabel("Alias")
        row.addWidget(self.lbl_alias)
        self.edit_lookup_alias = QLineEdit()
        self.edit_lookup_alias.setPlaceholderText("Look up Alias")
        self.edit_lookup_alias.textChanged.connect(self._on_lookup_inputs_changed)
        row.addWidget(self.edit_lookup_alias, 1)
        self.category_carousel = SidebarCarousel(
            "Category",
            [(cat, cat) for cat in CATEGORIES if cat not in SYSTEM_CATEGORY_EXCLUSIONS],
            inactive_text="",
            compact=True,
        )
        self.category_carousel.valueSelected.connect(lambda value: self._on_lookup_inputs_changed())
        self.category_carousel.activeChanged.connect(lambda value, active: self._on_lookup_inputs_changed())
        row.addWidget(self.category_carousel)
        self.btn_lookup = QPushButton("Lookup")
        self.btn_lookup.clicked.connect(self._emit_lookup)
        row.addWidget(self.btn_lookup)
        self.btn_add_alias = QPushButton("Add Selected")
        self.btn_add_alias.clicked.connect(self._emit_add_aliases)
        row.addWidget(self.btn_add_alias)
        layout.addLayout(row)

        self.lookup_status = QLabel("")
        self.lookup_status.setWordWrap(True)
        self.lookup_status.setVisible(False)
        layout.addWidget(self.lookup_status)
        self.lookup_table = self._table(["Type", "Alias", "Category", "Source"], show_row_numbers=False)
        self.lookup_table.setVisible(False)
        layout.addWidget(self.lookup_table)

        self.cooccurrence_header = self._section_header("Observed Phrase Variants")
        self.cooccurrence_header.setVisible(False)
        layout.addWidget(self.cooccurrence_header)
        self.cooccurrence_table = self._table(["Add", "Alias", "Frequency"], show_row_numbers=False)
        self.cooccurrence_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.cooccurrence_table.setVisible(False)
        layout.addWidget(self.cooccurrence_table)
        return panel

    def _build_additions_panel(self) -> QFrame:
        card = self._card()
        layout = QVBoxLayout(card)
        apply_layout_margins(layout, WORKSPACE_CARD_MARGINS)
        apply_layout_spacing(layout, WORKSPACE_ROOT_SPACING)
        title_row = QHBoxLayout()
        apply_layout_margins(title_row, LIB_TAB_CONTENT_ZERO_MARGINS)
        title_row.addWidget(self._section_header("My Additions"), 1)
        self.btn_import_additions = QPushButton("Import")
        self.btn_import_additions.clicked.connect(self.importAdditionsRequested.emit)
        title_row.addWidget(self.btn_import_additions)
        self.btn_export_additions = QPushButton("Export")
        self.btn_export_additions.clicked.connect(self.exportAdditionsRequested.emit)
        title_row.addWidget(self.btn_export_additions)
        layout.addLayout(title_row)
        self.additions_help = QLabel(
            "An alias is a meaningful word that helps auto-categorize sounds (like 'kick'->Kick category). You may extend the system's own alias bank by adding custom aliases to the taxonomy. This page shows custom aliases you have chosen to add to the taxonomy."
        )
        self.additions_help.setWordWrap(True)
        apply_style(self.additions_help, workspace_banner_style())
        layout.addWidget(self.additions_help)
        layout.addWidget(self._build_alias_controls())
        self.additions_table = self._table(["Alias", "Category", "Source"], show_row_numbers=False)
        self.additions_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.additions_table.customContextMenuRequested.connect(self._show_additions_menu)
        layout.addWidget(self.additions_table, 1)
        return card

    def _build_corrections_panel(self) -> QFrame:
        card = self._card()
        layout = QVBoxLayout(card)
        apply_layout_margins(layout, WORKSPACE_CARD_MARGINS)
        apply_layout_spacing(layout, WORKSPACE_ROOT_SPACING)
        header_row = QHBoxLayout()
        apply_layout_margins(header_row, LIB_TAB_CONTENT_ZERO_MARGINS)
        header_row.addWidget(self._section_header("Learned Corrections"), 1)
        self.btn_reset_corrections = QPushButton("Reset Learned Corrections")
        self.btn_reset_corrections.clicked.connect(self.resetCorrectionsRequested.emit)
        header_row.addWidget(self.btn_reset_corrections)
        layout.addLayout(header_row)
        self.corrections_help = QLabel(
            "The word-classification system learns from the adjustments you make to its predictions, by adjusting the importance it gives to certain aliases based on how practically telling they are. You may reset these adjustments at any time."
        )
        self.corrections_help.setWordWrap(True)
        apply_style(self.corrections_help, workspace_banner_style())
        layout.addWidget(self.corrections_help)
        self.corrections_table = self._table(["Token", "Category", "Direction", "Offset"], show_row_numbers=False)
        self.corrections_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.corrections_table.customContextMenuRequested.connect(self._show_corrections_menu)
        layout.addWidget(self.corrections_table, 1)
        return card

    def _selected_category(self) -> str:
        if not self.category_carousel.options:
            return ""
        return str(self.category_carousel.options[self.category_carousel.current_index][1])

    def _emit_lookup(self) -> None:
        self.lookupRequested.emit(self.edit_lookup_alias.text().strip(), self._selected_category())

    def _emit_add_aliases(self) -> None:
        aliases = [self.edit_lookup_alias.text().strip().lower()]
        aliases.extend(self.selected_cooccurrences())
        self.addAliasesRequested.emit(aliases, self._selected_category())

    def _on_lookup_inputs_changed(self) -> None:
        self._lookup_ready = False
        self._lookup_allows_add = False
        self.lookup_status.clear()
        self.lookup_status.setVisible(False)
        self.lookup_table.setRowCount(0)
        self.lookup_table.setVisible(False)
        self.cooccurrence_table.setRowCount(0)
        self.cooccurrence_header.setVisible(False)
        self.cooccurrence_table.setVisible(False)
        self._refresh_add_state()

    def _show_gaps_menu(self, pos) -> None:
        item = self.gaps_table.itemAt(pos)
        if item is None:
            return
        self.gaps_table.selectRow(item.row())
        token_item = self.gaps_table.item(item.row(), 0)
        token = token_item.text() if token_item else ""
        if not token:
            return
        menu = QMenu(self.gaps_table)
        action = QAction("Add to taxonomy", self)
        action.triggered.connect(lambda checked=False, value=token: self.gapAddRequested.emit(value))
        menu.addAction(action)
        menu.exec(self.gaps_table.mapToGlobal(pos))

    def _show_additions_menu(self, pos) -> None:
        if not self.additions_table.itemAt(pos):
            return
        menu = QMenu(self.additions_table)
        action = QAction("Remove selected additions", self)
        action.triggered.connect(lambda checked=False: self.removeAdditionsRequested.emit(self.selected_additions()))
        menu.addAction(action)
        menu.exec(self.additions_table.mapToGlobal(pos))

    def _show_corrections_menu(self, pos) -> None:
        if not self.corrections_table.itemAt(pos):
            return
        menu = QMenu(self.corrections_table)
        action = QAction("Remove selected corrections", self)
        action.triggered.connect(lambda checked=False: self.removeCorrectionsRequested.emit(self.selected_corrections()))
        menu.addAction(action)
        menu.exec(self.corrections_table.mapToGlobal(pos))

    def _show_anchor_candidates_menu(self, pos) -> None:
        item = self.anchors_table.itemAt(pos)
        if item is None:
            return
        if not self.anchors_table.selectionModel().isRowSelected(item.row()):
            self.anchors_table.selectRow(item.row())
        selected = self.selected_anchors()
        if not selected:
            return
        menu = QMenu(self.anchors_table)
        apply_style(menu, menu_style())
        promote = QAction("Promote", self)
        promote.triggered.connect(lambda checked=False: self.promoteAnchorsRequested.emit(self.selected_anchors()))
        menu.addAction(promote)
        ignore = QAction("Ignore", self)
        ignore.triggered.connect(lambda checked=False: self.ignoreAnchorsRequested.emit(self.selected_anchors()))
        menu.addAction(ignore)
        menu.exec(self.anchors_table.viewport().mapToGlobal(pos))

    def _set_section(self, section: str) -> None:
        mapping: dict[str, tuple[QWidget, QPushButton]] = {
            "discovery": (self.discovery_panel, self.btn_nav_discovery),
            "additions": (self.additions_panel, self.btn_nav_additions),
            "corrections": (self.corrections_panel, self.btn_nav_corrections),
            "my_anchors": (self.my_anchors_panel, self.btn_nav_my_anchors),
            "anchors": (self.anchors_panel, self.btn_nav_anchors),
        }
        if self.tree_organization_panel is not None and self.btn_nav_tree_organization is not None:
            mapping["tree_organization"] = (self.tree_organization_panel, self.btn_nav_tree_organization)
        default_key = "tree_organization" if "tree_organization" in mapping else "discovery"
        panel, active_button = mapping.get(section, mapping[default_key])
        self.stack.setCurrentWidget(panel)
        for _name, (_panel, button) in mapping.items():
            if button is None:
                continue
            button.setProperty("active", button is active_button)
            button.style().unpolish(button)
            button.style().polish(button)
        self.sectionChanged.emit(section if section in mapping else default_key)

    def _open_tree_organization(self) -> None:
        if self.tree_organization_panel is None:
            self.treeOrganizationRequested.emit()
            return
        self._set_section("tree_organization")

    def open_add_alias(self, alias: str = "") -> None:
        self._set_section("additions")
        if alias:
            self.edit_lookup_alias.setText(alias)
            self.edit_lookup_alias.setFocus()

    def set_tree_organization_panel(self, panel: QWidget) -> None:
        if self.tree_organization_panel is panel:
            self._set_section("tree_organization")
            return
        if self.tree_organization_panel is not None:
            self.stack.removeWidget(self.tree_organization_panel)
            self.tree_organization_panel.deleteLater()
        self.tree_organization_panel = panel
        self.stack.addWidget(panel)
        self._set_section("tree_organization")

    def set_mode(self, can_write: bool) -> None:
        self._can_write = can_write
        self.btn_import_additions.setEnabled(can_write)
        self.btn_export_additions.setEnabled(can_write)
        self.btn_reset_corrections.setEnabled(can_write)
        self.btn_remove_verified_anchors.setEnabled(can_write)
        self.btn_import_verified_anchors.setEnabled(can_write)
        self.btn_export_verified_anchors.setEnabled(can_write)
        self.btn_promote_anchors.setEnabled(can_write)
        self.btn_ignore_anchors.setEnabled(can_write)
        self.btn_discard_anchor_draft.setEnabled(False)
        self.btn_save_anchor_draft.setEnabled(False)
        self.btn_apply_anchor_draft.setEnabled(False)
        self._refresh_add_state()

    def set_alias_lookup(
        self,
        status: str,
        rows: Iterable[tuple[str, str, str, str]],
        cooccurrences: Iterable[tuple[str, int]],
        allows_add: bool,
    ) -> None:
        self._lookup_ready = True
        self._lookup_allows_add = allows_add
        self.lookup_status.setText(status)
        self.lookup_status.setVisible(bool(status))
        row_list = list(rows)
        self.lookup_table.setRowCount(len(row_list))
        for row, values in enumerate(row_list):
            for col, value in enumerate(values):
                self.lookup_table.setItem(row, col, QTableWidgetItem(value))
        self.lookup_table.setVisible(bool(row_list))
        co_rows = list(cooccurrences)
        self.cooccurrence_table.setRowCount(len(co_rows))
        for row, (alias, frequency) in enumerate(co_rows):
            item = QTableWidgetItem("")
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked)
            self.cooccurrence_table.setItem(row, 0, item)
            self.cooccurrence_table.setItem(row, 1, QTableWidgetItem(alias))
            self.cooccurrence_table.setItem(row, 2, QTableWidgetItem(str(frequency)))
        self.cooccurrence_header.setVisible(bool(co_rows))
        self.cooccurrence_table.setVisible(bool(co_rows))
        self._refresh_add_state()

    def selected_cooccurrences(self) -> list[str]:
        selected: list[str] = []
        for row in range(self.cooccurrence_table.rowCount()):
            check_item = self.cooccurrence_table.item(row, 0)
            alias_item = self.cooccurrence_table.item(row, 1)
            if check_item and alias_item and check_item.checkState() == Qt.Checked:
                selected.append(alias_item.text())
        return selected

    def set_discovery_rows(
        self,
        uncategorized: list[tuple[str, str, str, str]],
        gaps: list[tuple[str, int, str]],
    ) -> None:
        self.uncategorized_table.setRowCount(len(uncategorized))
        for row, values in enumerate(uncategorized):
            for col, value in enumerate(values):
                self.uncategorized_table.setItem(row, col, QTableWidgetItem(value))
        self.gaps_table.setRowCount(len(gaps))
        for row, values in enumerate(gaps):
            for col, value in enumerate(values):
                self.gaps_table.setItem(row, col, QTableWidgetItem(str(value)))
        has_gaps = bool(gaps)
        self.gaps_header.setVisible(has_gaps)
        self.gaps_table.setVisible(has_gaps)

    def set_additions(self, rows: list[tuple[str, str, str]]) -> None:
        self.additions_table.setRowCount(len(rows))
        for row, values in enumerate(rows):
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                if col == 0:
                    item.setData(Qt.UserRole, value)
                self.additions_table.setItem(row, col, item)

    def selected_additions(self) -> list[str]:
        aliases = []
        for index in self.additions_table.selectionModel().selectedRows():
            item = self.additions_table.item(index.row(), 0)
            if item and item.text():
                aliases.append(item.text())
        return aliases

    def set_corrections(self, rows: list[tuple[str, str, float]]) -> None:
        self.corrections_table.setRowCount(len(rows))
        for row, (token, category, offset) in enumerate(rows):
            direction = "Bonus" if offset > 0 else "Penalty"
            values = (token, category, direction, f"{offset:+.2f}")
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                if col == 0:
                    item.setData(Qt.UserRole, (token, category))
                self.corrections_table.setItem(row, col, item)

    def set_anchor_candidates(self, rows: list[dict]) -> None:
        self._set_anchor_rows(self.anchors_table, rows)

    def set_my_anchors(self, rows: list[dict]) -> None:
        self._set_anchor_rows(self.my_anchors_table, rows)

    def set_anchor_candidate_actions(self, actions: dict[str, str]) -> None:
        self._anchor_candidate_actions = {
            str(anchor_id): str(action or "")
            for anchor_id, action in dict(actions or {}).items()
            if str(anchor_id or "")
        }
        self._sync_anchor_action_widgets()

    def _set_anchor_rows(self, table: QTableWidget, rows: list[dict]) -> None:
        self._populating_anchor_rows = True
        table.setSortingEnabled(False)
        table.setRowCount(len(rows))
        include_action = bool(table.property("anchor_actions_enabled"))
        for row, payload in enumerate(rows):
            examples = payload.get("examples") or []
            example_names = [str(item.get("name") or "") for item in examples if isinstance(item, dict) and item.get("name")]
            preview_path = str(payload.get("preview_path") or "")
            example = ", ".join(example_names[:3]) or str(payload.get("example_name") or "")
            examples_text = "\n".join(example_names[:8])
            
            classification = self._taxonomy_label(
                payload.get("audio_type", ""),
                payload.get("category", ""),
                payload.get("subcategory", ""),
            )
            values = (
                classification,
                example,
                "",
            )
            if include_action:
                values = (*values, "")
            for col, value in enumerate(values):
                item = NumericTableWidgetItem(str(value)) if col == 2 else QTableWidgetItem(str(value))
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                if col == 0:
                    item.setData(Qt.UserRole, str(payload.get("category") or ""))
                    item.setData(Qt.UserRole + 1, str(payload.get("audio_type") or ""))
                    item.setData(Qt.UserRole + 2, str(payload.get("subcategory") or ""))
                    item.setData(ANCHOR_ID_ROLE, str(payload.get("anchor_id") or ""))
                    item.setData(ANCHOR_REFERENCE_PATHS_ROLE, payload.get("reference_paths") or [])
                    item.setToolTip("Choose a sound group.")
                elif col == 1:
                    item.setData(ANCHOR_PREVIEW_PATH_ROLE, preview_path)
                    item.setData(ANCHOR_EXAMPLES_ROLE, examples)
                    item.setData(ANCHOR_ID_ROLE, str(payload.get("anchor_id") or ""))
                    item.setToolTip(examples_text)
                elif col == 2:
                    ratio = payload.get("consistency_ratio")
                    try:
                        ratio_value = float(ratio) if ratio is not None else 0.0
                    except (TypeError, ValueError):
                        ratio_value = 0.0
                    quality = str(payload.get("anchor_quality") or "")
                    quality_text = str(payload.get("anchor_quality_text") or "")
                    item.setData(Qt.UserRole, ratio_value)
                    item.setData(ANCHOR_COHESION_RATIO_ROLE, ratio_value)
                    item.setData(ANCHOR_QUALITY_ROLE, quality)
                    tooltip_parts = [
                        f"Cohesion: {payload.get('consistency_text') or 'Pending'}",
                        f"Density: {payload.get('density_text') or 'Pending'}",
                    ]
                    if quality_text:
                        tooltip_parts.append("This cluster is broad enough that it probably needs review or splitting before promotion.")
                    item.setToolTip("\n".join(tooltip_parts))
                elif col == 3:
                    anchor_id = str(payload.get("anchor_id") or "")
                    action = self._anchor_candidate_actions.get(anchor_id, "")
                    item.setData(ANCHOR_ID_ROLE, anchor_id)
                    item.setData(ANCHOR_ACTION_ROLE, action)
                    item.setToolTip("Draft action for this anchor candidate.")
                table.setItem(row, col, item)
            sound_group = RefinementTargetCombo(
                list(CATEGORIES),
                str(payload.get("category") or ""),
                display_prefix=str(payload.get("audio_type") or ""),
                subcategory=str(payload.get("subcategory") or ""),
                parent=table,
            )
            apply_style(sound_group, refinement_target_combo_style())
            sound_group.currentIndexChanged.connect(
                lambda _idx=0, source=table, combo=sound_group: self._anchor_sound_group_combo_changed(source, combo)
            )
            table.setCellWidget(row, 0, sound_group)
            if include_action:
                self._set_anchor_action_widget(table, row, str(payload.get("anchor_id") or ""))
        table.setSortingEnabled(True)
        self._populating_anchor_rows = False

    def _set_anchor_action_widget(self, table: QTableWidget, row: int, anchor_id: str) -> None:
        container = QWidget(table)
        apply_style(container, "QWidget { background: transparent; }")
        layout = QHBoxLayout(container)
        layout.setContentsMargins(scaled_px(8), scaled_px(5), scaled_px(8), scaled_px(5))
        layout.setSpacing(0)

        combo = ActionComboBox(container)
        combo.addItem("None", "")
        combo.addItem("Promote", "promotion")
        combo.addItem("Ignore", "ignore")
        combo.addItem("Update", "update")
        action = self._anchor_candidate_actions.get(anchor_id, "")
        index = combo.findData(action)
        combo.setCurrentIndex(index if index >= 0 else 0)
        combo.setFixedWidth(self._anchor_action_combo_width(combo))
        combo.setToolTip("Set, change, or clear the staged action for this candidate.")
        self._apply_anchor_action_tone(combo)
        apply_style(combo, anchor_action_combo_style())
        combo.currentIndexChanged.connect(
            lambda _idx=0, source=table, widget=combo: self._anchor_action_combo_changed(source, widget)
        )
        layout.addStretch(1)
        layout.addWidget(combo, 0, Qt.AlignCenter)
        layout.addStretch(1)
        table.setCellWidget(row, 3, container)

    @staticmethod
    def _anchor_action_combo_width(combo: QComboBox) -> int:
        longest = max(
            (combo.fontMetrics().horizontalAdvance(combo.itemText(index)) for index in range(combo.count())),
            default=0,
        )
        return longest + scaled_px(72)

    @staticmethod
    def _anchor_action_column_width(table: QTableWidget) -> int:
        metrics = table.fontMetrics()
        longest = max(metrics.horizontalAdvance(label) for label in ("Promote", "Ignore", "Update", "None"))
        return longest + scaled_px(96)

    @staticmethod
    def _apply_anchor_action_tone(combo: QComboBox) -> None:
        tone = str(combo.currentData() or "none")
        combo.setProperty("actionTone", tone)
        combo.style().unpolish(combo)
        combo.style().polish(combo)
        combo.update()

    def _sync_anchor_action_widgets(self) -> None:
        table = getattr(self, "anchors_table", None)
        if table is None:
            return
        self._populating_anchor_rows = True
        try:
            for row in range(table.rowCount()):
                item = table.item(row, 0)
                if item is None:
                    continue
                anchor_id = str(item.data(ANCHOR_ID_ROLE) or "")
                action = self._anchor_candidate_actions.get(anchor_id, "")
                action_item = table.item(row, 3) if table.columnCount() > 3 else None
                if action_item is not None:
                    action_item.setData(ANCHOR_ACTION_ROLE, action)
                combo = table.cellWidget(row, 3) if table.columnCount() > 3 else None
                if not isinstance(combo, QComboBox) and combo is not None:
                    combo = combo.findChild(QComboBox)
                if isinstance(combo, QComboBox):
                    index = combo.findData(action)
                    combo.setCurrentIndex(index if index >= 0 else 0)
                    self._apply_anchor_action_tone(combo)
        finally:
            self._populating_anchor_rows = False

    @staticmethod
    def _anchor_reference_count(payload: dict) -> str:
        try:
            count = int(payload.get("n_reference_items") or 0)
        except (TypeError, ValueError):
            return ""
        return str(max(0, count))

    @staticmethod
    def _taxonomy_label(audio_type: str | None, category: str | None, subcategory: str | None = "") -> str:
        t_str = str(audio_type or "").strip()
        cat_str = str(category or "").strip()
        subcat_str = str(subcategory or "").strip()
        
        t_lower = t_str.lower()
        if "loop" in t_lower:
            t_shorthand = "Lp"
        elif "oneshot" in t_lower or "1s" in t_lower:
            t_shorthand = "1s"
        elif t_str:
            t_shorthand = t_str
        else:
            t_shorthand = ""
            
        parts = []
        if t_shorthand:
            parts.append(t_shorthand)
        if cat_str:
            parts.append(cat_str)
        if subcat_str:
            parts.append(subcat_str)
            
        return "/".join(parts)

    def set_anchor_status(self, message: str, level: str = "info") -> None:
        self._anchor_status_level = level
        self.anchor_status.setText(message)
        self.anchor_status.setVisible(bool(message))
        apply_style(self.anchor_status, workspace_banner_style(level))

    def set_anchor_draft_state(self, pending_count: int) -> None:
        enabled = self._can_write and pending_count > 0
        if pending_count:
            self.anchor_draft_summary.setText(f"{pending_count} anchor draft action{'s' if pending_count != 1 else ''} pending")
        else:
            self.anchor_draft_summary.setText("No anchor draft changes")
        self.btn_discard_anchor_draft.setEnabled(enabled)
        self.btn_save_anchor_draft.setEnabled(enabled)
        self.btn_apply_anchor_draft.setEnabled(enabled)

    def _handle_anchor_double_click(self, table: QTableWidget, row: int, col: int) -> None:
        if col == 0:
            widget = table.cellWidget(row, 0)
            if hasattr(widget, "showPopup"):
                widget.showPopup()
            return
        if bool(table.property("anchor_actions_enabled")) and col == 3:
            widget = table.cellWidget(row, 3)
            if not hasattr(widget, "showPopup") and widget is not None:
                widget = widget.findChild(QComboBox)
            if hasattr(widget, "showPopup"):
                widget.showPopup()
            return
        self._preview_anchor_row(table, row)

    def _preview_anchor_row(self, table: QTableWidget, row: int) -> None:
        group_item = table.item(row, 0)
        example_item = table.item(row, 1)
        anchor_id = str(group_item.data(ANCHOR_ID_ROLE) or "") if group_item else ""
        examples = example_item.data(ANCHOR_EXAMPLES_ROLE) if example_item else None
        path = None
        if isinstance(examples, list) and examples:
            offset = self._anchor_preview_offsets.get(anchor_id, 0)
            for step in range(len(examples)):
                candidate = examples[(offset + step) % len(examples)]
                if isinstance(candidate, dict) and candidate.get("path"):
                    path = str(candidate.get("path"))
                    self._anchor_preview_offsets[anchor_id] = offset + step + 1
                    break
        if not path and example_item:
            path = example_item.data(ANCHOR_PREVIEW_PATH_ROLE)
        if path:
            self.previewAnchorRequested.emit(str(path))

    def selected_anchors(self) -> list[str]:
        return self._selected_anchor_ids(self.anchors_table)

    def selected_my_anchors(self) -> list[str]:
        return self._selected_anchor_ids(self.my_anchors_table)

    def anchor_reference_paths(self, anchor_id: str) -> list[str]:
        anchor_id = str(anchor_id or "")
        for table in (self.anchors_table, self.my_anchors_table):
            for row in range(table.rowCount()):
                item = table.item(row, 0)
                if item and str(item.data(ANCHOR_ID_ROLE) or "") == anchor_id:
                    paths = item.data(ANCHOR_REFERENCE_PATHS_ROLE)
                    if isinstance(paths, list):
                        return [str(path) for path in paths if path]
        return []

    def _selected_anchor_ids(self, table: QTableWidget) -> list[str]:
        ids = []
        for index in table.selectionModel().selectedRows():
            item = table.item(index.row(), 0)
            anchor_id = item.data(ANCHOR_ID_ROLE) if item else None
            if anchor_id:
                ids.append(str(anchor_id))
        return ids

    def _anchor_sound_group_combo_changed(self, table: QTableWidget, combo: RefinementTargetCombo) -> None:
        if self._populating_anchor_rows:
            return
        row = self._row_for_cell_widget(table, combo)
        if row < 0:
            return
        item = table.item(row, 0)
        if item is None:
            return
        anchor_id = str(item.data(ANCHOR_ID_ROLE) or "")
        if not anchor_id:
            return
        audio_type = combo.audio_type()
        category = combo.value()
        subcategory = combo.subcategory()
        if not category:
            return
        self._populating_anchor_rows = True
        item.setData(Qt.UserRole, category)
        item.setData(Qt.UserRole + 1, audio_type)
        item.setData(Qt.UserRole + 2, subcategory)
        item.setText(self._taxonomy_label(audio_type, category, subcategory))
        self._populating_anchor_rows = False
        table.viewport().update()
        self.anchorSoundGroupChanged.emit(anchor_id, audio_type, category, subcategory)

    def _anchor_action_combo_changed(self, table: QTableWidget, combo: QComboBox) -> None:
        if self._populating_anchor_rows:
            return
        row = self._row_for_cell_widget(table, combo)
        if row < 0:
            return
        group_item = table.item(row, 0)
        action_item = table.item(row, 3)
        anchor_id = str(group_item.data(ANCHOR_ID_ROLE) or "") if group_item else ""
        if not anchor_id:
            return
        action = str(combo.currentData() or "")
        if action:
            self._anchor_candidate_actions[anchor_id] = action
        else:
            self._anchor_candidate_actions.pop(anchor_id, None)
        if action_item is not None:
            action_item.setData(ANCHOR_ACTION_ROLE, action)
        self._apply_anchor_action_tone(combo)
        self.anchorCandidateActionChanged.emit(anchor_id, action)

    @staticmethod
    def _row_for_cell_widget(table: QTableWidget, widget: QWidget) -> int:
        for row in range(table.rowCount()):
            for col in range(table.columnCount()):
                cell_widget = table.cellWidget(row, col)
                if cell_widget is widget:
                    return row
                if cell_widget is not None and widget.parent() is cell_widget:
                    return row
        return -1

    def selected_corrections(self) -> list[tuple[str, str]]:
        keys = []
        for index in self.corrections_table.selectionModel().selectedRows():
            item = self.corrections_table.item(index.row(), 0)
            key = item.data(Qt.UserRole) if item else None
            if isinstance(key, tuple) and len(key) == 2:
                keys.append((str(key[0]), str(key[1])))
        return keys

    def _refresh_add_state(self) -> None:
        has_alias = bool(self.edit_lookup_alias.text().strip())
        has_category = bool(self._selected_category().strip())
        self.btn_lookup.setEnabled(has_alias and has_category)
        self.btn_add_alias.setEnabled(self._can_write and self._lookup_ready and self._lookup_allows_add and has_alias and has_category)

    def refresh_theme(self) -> None:
        self._refresh_theme()

    def _refresh_theme(self) -> None:
        app = self.window()
        if app is not None:
            font = app.font()
            for widget in (
                self.edit_lookup_alias,
                self.lookup_table,
                self.cooccurrence_table,
                self.uncategorized_table,
                self.gaps_table,
                self.additions_table,
                self.corrections_table,
                self.my_anchors_table,
                self.anchors_table,
            ):
                widget.setFont(font)
                if hasattr(widget, "verticalHeader"):
                    widget.verticalHeader().setFont(font)

        apply_style(self.sidebar, sidebar_base_style())
        apply_style(self.sidebar_header, sidebar_header_style())
        apply_style(self.sidebar_title, sidebar_title_style())
        for label in self.sidebar.findChildren(QLabel):
            if label.property("sectionLabel"):
                apply_style(label, section_label_style())
        for header, label in self._section_headers:
            apply_style(header, sidebar_header_style())
            apply_style(label, sidebar_title_style())
        apply_style(self.lbl_alias, workspace_field_label_style())
        apply_style(self.anchor_draft_summary, workspace_field_label_style())
        apply_style(self.edit_lookup_alias, workspace_input_style())
        apply_style(self.discovery_help, workspace_banner_style())
        apply_style(self.additions_help, workspace_banner_style())
        apply_style(self.corrections_help, workspace_banner_style())
        apply_style(self.my_anchor_status, workspace_banner_style())
        apply_style(self.anchor_candidates_help, workspace_banner_style())
        apply_style(self.anchor_status, workspace_banner_style(self._anchor_status_level))
        self.category_carousel.refresh_theme()
        for card in (
            self.discovery_panel,
            self.additions_panel,
            self.corrections_panel,
            self.my_anchors_panel,
            self.anchors_panel,
        ):
            apply_style(card, workspace_card_style())
        for table in (
            self.lookup_table,
            self.cooccurrence_table,
            self.uncategorized_table,
            self.gaps_table,
            self.additions_table,
            self.corrections_table,
            self.my_anchors_table,
            self.anchors_table,
        ):
            apply_style(table, workspace_table_widget_style())
            if table.verticalHeader().isVisible():
                apply_style(table.verticalHeader(), vertical_header_style())
        for button in (self.btn_lookup, self.btn_add_alias, self.btn_promote_anchors, self.btn_save_anchor_draft, self.btn_apply_anchor_draft):
            apply_style(button, workspace_primary_button_style())
        for button in (
            self.btn_import_verified_anchors,
            self.btn_export_verified_anchors,
            self.btn_import_additions,
            self.btn_export_additions,
        ):
            apply_style(button, button_style("secondary", size="normal"))
        for button in (
            self.btn_reset_corrections,
            self.btn_remove_verified_anchors,
            self.btn_ignore_anchors,
            self.btn_discard_anchor_draft,
        ):
            apply_style(button, button_style("danger", size="normal"))
        for button in (
            self.btn_nav_discovery,
            self.btn_nav_additions,
            self.btn_nav_corrections,
            self.btn_nav_my_anchors,
            self.btn_nav_anchors,
            self.btn_nav_tree_organization,
        ):
            if button is not None:
                apply_style(button, workspace_sidebar_button_style())
        for combo in self.findChildren(RefinementTargetCombo):
            apply_style(combo, refinement_target_combo_style())
            combo.update()
        for combo in self.anchors_table.findChildren(QComboBox):
            apply_style(combo, anchor_action_combo_style())
            self._apply_anchor_action_tone(combo)

