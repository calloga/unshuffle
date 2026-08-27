import os
from pathlib import Path

from .. import widgets
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLineEdit,
    QPushButton, QFrame, QSizePolicy, QStackedWidget, QButtonGroup,
    QScrollArea, QToolButton, QApplication, QLabel, QMenu, QStyle, QStyledItemDelegate,
    QStyleOptionViewItem,
)
from PySide6.QtCore import Property, QEasingCurve, QPropertyAnimation, QRect, QSize, Signal, Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QIcon, QPainter, QPalette, QPen, QPixmap

from unshuffle.core.constants import CATEGORIES

from .. import widgets as sw
from .library_tree import LibraryTreeView
from ..utils.constants import (
    DOCKED_HEADER_LAYOUT_MARGINS,
    DOCKED_HEADER_LAYOUT_SPACING,
    DOCKED_MAIN_LAYOUT_MARGINS,
    DOCKED_MAIN_LAYOUT_SPACING,
    DOCKED_MINIMUM_HEIGHT,
    DOCKED_MINIMUM_WIDTH,
    DOCKED_OPTIONS_LAYOUT_SPACING,
    LIB_TAB_CONTENT_ZERO_MARGINS,
    DOCKED_SEARCH_BAR_FIXED_HEIGHT,
    DOCKED_SEARCH_BAR_MINIMUM_WIDTH,
    DOCKED_SCROLL_CONTENT_MIN_HEIGHT,
    DOCKED_SEARCH_ROW_MARGINS,
    DOCKED_SEARCH_ROW_SPACING,
    DOCKED_TREE_PANEL_MIN_HEIGHT,
    LIB_TAB_VIEW_BUTTON_HEIGHT,
    LIB_TAB_VIEW_BUTTON_ICON_BOX_HEIGHT,
    LIB_TAB_VIEW_BUTTON_ICON_BOX_WIDTH,
    LIB_TAB_VIEW_BUTTON_ICON_SIZE,
    LIB_TAB_VIEW_BUTTON_WIDTH,
)
from ..utils.styles import (
    ColorPalette,
    apply_style,
    dock_options_button_style,
    dock_save_search_button_style,
    dock_view_style,
    scaled_px,
)
from ..utils.layout_helpers import apply_layout_margins, apply_layout_spacing
from ..utils.app_icon import app_icon
from ..utils.widget_helpers import apply_fixed_height, apply_fixed_width, apply_minimum_width
from ..widgets.buttons import SidebarIconButton
from ..widgets import AnimatedIconButton
from ..widgets.preview_control_bar import DragOutIconButton
from ..utils.constants import PAUSE_ICON, PLAY_ICON, STOP_ICON
from ..core.dock_appearance import DockAdaptivePalette, transfer_palette_color


class DockHoverTitleStrip(QWidget):
    undockRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("DockHoverTitleStrip")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._background_color = QColor(ColorPalette.BG_MED)
        self._border_color = QColor(ColorPalette.BORDER)
        self.setFixedHeight(3)
        self.setMouseTracking(True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 2, 4, 2)
        layout.setSpacing(4)
        self.logo = QLabel(self)
        self.logo.setPixmap(app_icon().pixmap(18, 18))
        self.logo.setFixedSize(20, 20)
        self.logo.setAlignment(Qt.AlignCenter)
        self.logo.setAttribute(Qt.WA_TransparentForMouseEvents)
        layout.addWidget(self.logo)
        layout.addStretch(1)
        self.menu_buttons: list[QToolButton] = []
        self.window_buttons: list[tuple[QToolButton, QStyle.StandardPixmap]] = []
        style = QApplication.style()
        for standard_icon, tooltip, callback in (
            (QStyle.SP_TitleBarMinButton, "Minimize", lambda: self.window().showMinimized()),
            (QStyle.SP_TitleBarNormalButton, "Return to normal mode", self.undockRequested.emit),
            (QStyle.SP_TitleBarCloseButton, "Close", lambda: self.window().close()),
        ):
            button = QToolButton(self)
            button.setIcon(style.standardIcon(standard_icon))
            button.setIconSize(QSize(14, 14))
            button.setToolTip(tooltip)
            button.setFixedSize(24, 22)
            button.clicked.connect(callback)
            layout.addWidget(button)
            self.window_buttons.append((button, standard_icon))

    def set_menus(self, *menus) -> None:
        layout = self.layout()
        if layout is None:
            return
        insert_at = max(0, layout.count() - 3)
        for button in self.menu_buttons:
            layout.removeWidget(button)
            button.deleteLater()
        self.menu_buttons.clear()
        available_menus = [menu for menu in menus if menu is not None]
        if available_menus:
            overflow_menu = QMenu(self)
            for menu in available_menus:
                overflow_menu.addMenu(menu)
            button = QToolButton(self)
            button.setObjectName("DockMenusButton")
            button.setToolTip("Menus")
            button.setMenu(overflow_menu)
            button.setPopupMode(QToolButton.InstantPopup)
            button.setAutoRaise(True)
            button.setText("Menus")
            button.setFixedSize(58, 22)
            button.setStyleSheet(
                "QToolButton#DockMenusButton::menu-indicator { image: none; width: 0px; }"
            )
            layout.insertWidget(insert_at, button)
            self.menu_buttons.append(button)

    def set_background_colors(self, background: str, border: str) -> None:
        self._background_color = QColor(background)
        self._border_color = QColor(border)
        self.update()

    def set_foreground_color(self, color: str) -> None:
        tint = QColor(color)
        style = QApplication.style()
        for button, standard_icon in self.window_buttons:
            pixmap = style.standardIcon(standard_icon).pixmap(button.iconSize())
            if pixmap.isNull():
                continue
            painter = QPainter(pixmap)
            painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
            painter.fillRect(pixmap.rect(), tint)
            painter.end()
            button.setIcon(QIcon(pixmap))

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), self._background_color)
        painter.end()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            handle = self.window().windowHandle()
            if handle is not None and handle.startSystemMove():
                event.accept()
                return
        super().mousePressEvent(event)


class DockVerticalResizeEdge(QWidget):
    def __init__(self, edge: Qt.Edge, parent=None):
        super().__init__(parent)
        self.edge = edge
        self.setFixedHeight(4)
        self.setCursor(Qt.SizeVerCursor)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            handle = self.window().windowHandle()
            if handle is not None and handle.startSystemResize(self.edge):
                event.accept()
                return
        super().mousePressEvent(event)


class DockChromeSpacer(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._extent = 0
        self.setFixedHeight(0)

    def get_extent(self) -> int:
        return self._extent

    def set_extent(self, value: int) -> None:
        self._extent = max(0, int(value))
        self.setFixedHeight(self._extent)

    extent = Property(int, get_extent, set_extent)


class DockSideResizeEdge(QWidget):
    def __init__(self, edge: Qt.Edge, parent=None):
        super().__init__(parent)
        self.edge = edge
        self.setFixedWidth(4)
        self.setCursor(Qt.SizeHorCursor)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            handle = self.window().windowHandle()
            if handle is not None and handle.startSystemResize(self.edge):
                event.accept()
                return
        super().mousePressEvent(event)


class DockEnvironmentScreenOverlay(QWidget):
    """Click-through border pulse around the dock while its environment is sampled."""

    sweepFinished = Signal()

    def __init__(self, dock_view):
        super().__init__(
            None,
            Qt.Tool
            | Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.WindowTransparentForInput
            | Qt.NoDropShadowWindowHint,
        )
        self.dock_view = dock_view
        self._progress = 0.0
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._timer = QTimer(self)
        self._timer.setInterval(32)
        self._timer.timeout.connect(self._advance)
        dock_view.destroyed.connect(self.deleteLater)
        self.hide()

    def set_scanning(self, scanning: bool) -> None:
        if scanning:
            window = self.dock_view.window()
            if (
                window is None
                or not window.isVisible()
                or getattr(window, "_defer_window_show", False)
                or getattr(window, "_frontloading_startup", False)
            ):
                self._timer.stop()
                self.hide()
                return
            screen = window.screen() if window is not None else None
            if screen is None:
                return
            self._progress = 0.0
            self.setGeometry(screen.geometry())
            self.show()
            self.raise_()
            self._timer.start()
        else:
            self._timer.stop()
            self.hide()

    def _advance(self) -> None:
        self._progress += 1.0 / 45.0
        if self._progress >= 1.0:
            self._timer.stop()
            self.hide()
            self.sweepFinished.emit()
            return
        self.update()

    def paintEvent(self, _event) -> None:
        dock_window = self.dock_view.window()
        if dock_window is None:
            return
        dock_rect = QRect(dock_window.frameGeometry())
        dock_rect.translate(-self.geometry().topLeft())

        palette = getattr(self.dock_view, "_adaptive_palette", None)
        accent = QColor(palette.accent if palette is not None else ColorPalette.PRIMARY)
        painter = QPainter(self)
        pulse = 1.0 - abs((self._progress * 2.0) - 1.0)
        glow = QColor(accent)
        glow.setAlpha(round(35 + (90 * pulse)))
        painter.setPen(QPen(glow, 3))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(dock_rect.adjusted(-3, -3, 3, 3), 7, 7)
        painter.end()


class DockPaletteTransitionOverlay(QWidget):
    def __init__(self, snapshot: QPixmap, parent=None):
        super().__init__(parent)
        self._snapshot = snapshot
        self._progress = 0.0
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_TranslucentBackground)

    def get_progress(self) -> float:
        return self._progress

    def set_progress(self, value: float) -> None:
        self._progress = max(0.0, min(1.0, float(value)))
        self.update()

    progress = Property(float, get_progress, set_progress)

    def paintEvent(self, _event) -> None:
        if self._snapshot.isNull():
            return
        top = round(self.height() * self._progress)
        if top >= self.height():
            return
        painter = QPainter(self)
        source_top = round(self._snapshot.height() * self._progress)
        painter.drawPixmap(
            QRect(0, top, self.width(), self.height() - top),
            self._snapshot,
            QRect(0, source_top, self._snapshot.width(), self._snapshot.height() - source_top),
        )
        painter.end()


class DockAdaptiveTreeDelegate(QStyledItemDelegate):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.palette = None

    def initStyleOption(self, option: QStyleOptionViewItem, index) -> None:
        super().initStyleOption(option, index)
        if self.palette is None:
            return
        foreground = index.data(Qt.ForegroundRole)
        color = foreground.color() if isinstance(foreground, QBrush) else foreground
        if not isinstance(color, QColor):
            color = option.palette.color(QPalette.Text)
        shifted = transfer_palette_color(color, self.palette)
        option.palette.setColor(QPalette.Text, shifted)
        option.palette.setColor(QPalette.HighlightedText, shifted)

    def paint(self, painter, option, index) -> None:
        option.state &= ~QStyle.State_HasFocus
        super().paint(painter, option, index)


class DockView(QWidget):
    """
    Read-only discovery side-car for docked mode.
    """

    searchChanged = Signal(str)
    typeToggleClicked = Signal(bool, bool, bool)
    orientationRequested = Signal()
    playRequested = Signal(object)
    similarityRequested = Signal(object)
    excludeRequested = Signal(object)
    quickFilterRequested = Signal(str, str)
    categoryChangeRequested = Signal(object, str)
    tagsEditRequested = Signal(object, object, object)
    openExplorerRequested = Signal(object)
    saveSearchRequested = Signal(str)
    filterRequested = Signal(str, bool)
    categoryFilterRequested = Signal(str, bool)
    rangeChanged = Signal(float, float)
    vibeBiasChanged = Signal(int)
    viewModeChanged = Signal(str)
    audioPreviewRequested = Signal(str)
    anchorRequested = Signal(str)
    findRequested = Signal(str)
    undockRequested = Signal()

    def __init__(self, tree_model, parent=None):
        super().__init__(parent)
        self.setObjectName("DockView")
        self.tree_model = tree_model
        self._vibe_state = {"anchor_text": "", "bias": 0, "visible": False}
        self._view_mode = "tree"
        self._map_available = True
        self.map_page = None
        self._adaptive_palette = None
        self._palette_overlay = None
        self._palette_overlay_animation = None
        self._chrome_enabled = False
        self._chrome_visible = False
        self._setup_ui()
        self._apply_normal_theme()
        self.setMinimumSize(DOCKED_MINIMUM_WIDTH, DOCKED_MINIMUM_HEIGHT)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def _setup_ui(self):
        if self.layout():
            layout = self.layout()
            while layout.count():
                item = layout.takeAt(0)
                widget = item.widget()
                if widget:
                    widget.deleteLater()
            if layout is not None:
                QWidget().setLayout(layout)

        root_layout = QVBoxLayout(self)
        apply_layout_margins(root_layout, LIB_TAB_CONTENT_ZERO_MARGINS)
        apply_layout_spacing(root_layout, LIB_TAB_CONTENT_ZERO_MARGINS[0])

        self.chrome_spacer = DockChromeSpacer(self)
        root_layout.addWidget(self.chrome_spacer)

        self.hover_title_strip = DockHoverTitleStrip(self)
        self.hover_title_strip.undockRequested.connect(self.undockRequested.emit)
        self._apply_default_chrome_style()
        self.hover_title_strip.hide()
        self.top_resize_edge = DockVerticalResizeEdge(Qt.TopEdge, self)
        self.top_resize_edge.hide()
        self.bottom_resize_edge = DockVerticalResizeEdge(Qt.BottomEdge, self)
        self.bottom_resize_edge.hide()
        self.left_resize_edge = DockSideResizeEdge(Qt.LeftEdge, self)
        self.right_resize_edge = DockSideResizeEdge(Qt.RightEdge, self)
        self.left_resize_edge.hide()
        self.right_resize_edge.hide()
        self.environment_screen_overlay = DockEnvironmentScreenOverlay(self)

        self.scroll_area = QScrollArea()
        self.scroll_area.setObjectName("DockScrollArea")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        root_layout.addWidget(self.scroll_area, 1)

        self.scroll_content = QWidget()
        self.scroll_content.setObjectName("DockScrollContent")
        self.scroll_content.setMinimumWidth(0)
        self.scroll_content.setMinimumHeight(DOCKED_SCROLL_CONTENT_MIN_HEIGHT)
        self.scroll_area.setWidget(self.scroll_content)

        self.main_layout = QVBoxLayout(self.scroll_content)
        apply_layout_margins(self.main_layout, DOCKED_MAIN_LAYOUT_MARGINS)
        apply_layout_spacing(self.main_layout, DOCKED_MAIN_LAYOUT_SPACING)
        apply_style(self, dock_view_style())

        search_row = QHBoxLayout()
        apply_layout_margins(search_row, DOCKED_SEARCH_ROW_MARGINS)
        apply_layout_spacing(search_row, DOCKED_SEARCH_ROW_SPACING)

        self.edit_search = QLineEdit()
        self.edit_search.setPlaceholderText("Search...")
        self.edit_search.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        apply_minimum_width(self.edit_search, DOCKED_SEARCH_BAR_MINIMUM_WIDTH)
        apply_fixed_height(self.edit_search, DOCKED_SEARCH_BAR_FIXED_HEIGHT)
        self.edit_search.textChanged.connect(self.searchChanged.emit)
        self.edit_search.textChanged.connect(lambda _t: self._refresh_search_button_state())
        search_row.addWidget(self.edit_search, 1)

        self.btn_save_search = QPushButton("Save")
        apply_fixed_width(self.btn_save_search, DOCKED_SEARCH_BAR_MINIMUM_WIDTH)
        apply_fixed_height(self.btn_save_search, DOCKED_SEARCH_BAR_FIXED_HEIGHT)
        self.btn_save_search.setEnabled(False)
        apply_style(self.btn_save_search, dock_save_search_button_style())
        self.btn_save_search.clicked.connect(lambda: self.saveSearchRequested.emit(self.edit_search.text()))
        search_row.addWidget(self.btn_save_search)

        self._search_row = search_row

        view_row = QHBoxLayout()
        apply_layout_margins(view_row, DOCKED_SEARCH_ROW_MARGINS)
        apply_layout_spacing(view_row, DOCKED_SEARCH_ROW_SPACING)
        self.btn_tree_view = SidebarIconButton(
            "icons/tree.png",
            QSize(LIB_TAB_VIEW_BUTTON_ICON_SIZE, LIB_TAB_VIEW_BUTTON_ICON_SIZE),
            QSize(LIB_TAB_VIEW_BUTTON_ICON_BOX_WIDTH, LIB_TAB_VIEW_BUTTON_ICON_BOX_HEIGHT),
        )
        self.btn_tree_view.setCheckable(True)
        self.btn_tree_view.clicked.connect(lambda: self.set_docked_view_mode("tree"))
        self.btn_map_view = SidebarIconButton(
            "icons/map.png",
            QSize(LIB_TAB_VIEW_BUTTON_ICON_SIZE, LIB_TAB_VIEW_BUTTON_ICON_SIZE),
            QSize(LIB_TAB_VIEW_BUTTON_ICON_BOX_WIDTH, LIB_TAB_VIEW_BUTTON_ICON_BOX_HEIGHT),
        )
        self.btn_map_view.setCheckable(True)
        self.btn_map_view.clicked.connect(lambda: self.set_docked_view_mode("map"))
        for button in (self.btn_tree_view, self.btn_map_view):
            button.setMinimumWidth(LIB_TAB_VIEW_BUTTON_WIDTH)
            button.setMaximumWidth(16777215)
            apply_fixed_height(button, LIB_TAB_VIEW_BUTTON_HEIGHT)
            button.setStyleSheet(
                f"QPushButton {{ padding: 0; border: none; min-height: {LIB_TAB_VIEW_BUTTON_HEIGHT}px; "
                f"max-height: {LIB_TAB_VIEW_BUTTON_HEIGHT}px; }}"
            )
            button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            button.setCursor(Qt.PointingHandCursor)
        self.view_group = QButtonGroup(self)
        self.view_group.setExclusive(True)
        self.view_group.addButton(self.btn_tree_view)
        self.view_group.addButton(self.btn_map_view)
        view_row.addWidget(self.btn_tree_view, 1, Qt.AlignVCenter)
        view_row.addWidget(self.btn_map_view, 1, Qt.AlignVCenter)
        self.type_picker = widgets.TypeToggle()
        self.type_picker.set_expanding(True)
        self.type_picker.typeChanged.connect(self._on_type_clicked)
        view_row.addWidget(self.type_picker, 3, Qt.AlignVCenter)
        from gui.core.audio_player import SoundPreviewPlayer

        self._preview_player = SoundPreviewPlayer.instance()
        self.btn_preview_play = AnimatedIconButton(PLAY_ICON, QSize(18, 18))
        self.btn_preview_play.setToolTip("Play/Pause")
        self.btn_preview_play.clicked.connect(lambda checked=False: self._toggle_docked_playback())
        self.btn_preview_stop = AnimatedIconButton(STOP_ICON, QSize(16, 16))
        self.btn_preview_stop.setToolTip("Stop")
        self.btn_preview_stop.clicked.connect(lambda checked=False: self._preview_player.stop())
        self.btn_preview_export = DragOutIconButton(self._preview_player)
        self.btn_preview_export.setToolTip("Export current sample")
        self.transport_shell = QFrame(self)
        self.transport_shell.setObjectName("DockTransportShell")
        transport_row = QHBoxLayout(self.transport_shell)
        transport_row.setContentsMargins(scaled_px(10), scaled_px(4), scaled_px(10), scaled_px(4))
        transport_row.setSpacing(scaled_px(14))
        transport_row.addStretch(1)
        for button in (self.btn_preview_play, self.btn_preview_stop, self.btn_preview_export):
            button.setEnabled(False)
            transport_row.addWidget(button, 0, Qt.AlignVCenter)
        transport_row.addStretch(1)
        self._transport_hide_timer = QTimer(self)
        self._transport_hide_timer.setSingleShot(True)
        self._transport_hide_timer.setInterval(3000)
        self._transport_hide_timer.timeout.connect(self._hide_inactive_transport)
        self.transport_shell.hide()
        self._preview_player.stateChanged.connect(self._update_docked_play_icon)
        self.main_layout.addLayout(view_row)

        self.options_section = sw.CollapsibleSection("OPTIONS", use_scroll=False)
        self.options_section.btn.setObjectName("DockOptionsButton")
        apply_style(self.options_section.btn, dock_options_button_style())
        
        opt_layout = self.options_section.content_layout
        apply_layout_spacing(opt_layout, DOCKED_OPTIONS_LAYOUT_SPACING)

        self.filter_carousel = sw.SidebarCarousel("Filters", [], inactive_text="None")
        self.filter_carousel.activeChanged.connect(self._on_filter_toggled)
        self.filter_carousel.valueSelected.connect(self._on_filter_selected)
        opt_layout.addWidget(self.filter_carousel)

        self.category_carousel = sw.SidebarCarousel("Categories", [(cat, cat) for cat in CATEGORIES], inactive_text="All")
        self.category_carousel.activeChanged.connect(self._on_category_toggled)
        self.category_carousel.valueSelected.connect(self._on_category_selected)
        opt_layout.addWidget(self.category_carousel)

        self.main_layout.addWidget(self.options_section)

        self.view_stack = QStackedWidget()
        self.view_stack.setMinimumHeight(DOCKED_TREE_PANEL_MIN_HEIGHT)

        self.view_tree = LibraryTreeView()
        self._adaptive_tree_delegate = DockAdaptiveTreeDelegate(self.view_tree)
        self.view_tree.setItemDelegate(self._adaptive_tree_delegate)
        self.view_tree.setModel(self.tree_model)
        apply_minimum_width(self.view_tree, LIB_TAB_CONTENT_ZERO_MARGINS[0])
        self.view_tree.setHeaderHidden(True)
        self.view_tree.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.view_tree.set_read_only_discovery(True)
        self.view_tree.play_requested.connect(lambda target: self.playRequested.emit(target))
        self.view_tree.similarity_requested.connect(lambda target: self.similarityRequested.emit(target))
        self.view_tree.exclude_requested.connect(lambda path: self.excludeRequested.emit(path))
        self.view_tree.quick_filter_requested.connect(lambda query, mode: self.quickFilterRequested.emit(query, mode))
        self.view_tree.category_change_requested.connect(lambda rec, category: self.categoryChangeRequested.emit(rec, category))
        self.view_tree.tags_edit_requested.connect(lambda records, add_tags, remove_tags: self.tagsEditRequested.emit(records, add_tags, remove_tags))
        self.view_tree.open_explorer_requested.connect(lambda target: self.openExplorerRequested.emit(target))
        self.view_tree.clicked.connect(lambda _index: self._sync_docked_transport_selection())
        self._force_single_tree_column()
        if hasattr(self.tree_model, "modelReset"):
            self.tree_model.modelReset.connect(self._force_single_tree_column)
        if hasattr(self.tree_model, "rebuildFinished"):
            self.tree_model.rebuildFinished.connect(self._force_single_tree_column)
        self.view_stack.addWidget(self.view_tree)
        self.main_layout.addWidget(self.view_stack, 1)
        self.search_shell = QFrame(self)
        self.search_shell.setObjectName("DockSearchShell")
        self.search_shell.setLayout(self._search_row)
        self._search_row.setContentsMargins(
            scaled_px(10), scaled_px(4), scaled_px(10), scaled_px(10)
        )
        root_layout.addWidget(self.transport_shell)
        root_layout.addWidget(self.search_shell)

        self.set_docked_view_mode("tree", emit=False)

    def _selected_audio_path(self):
        index = self.view_tree.currentIndex()
        record = self.view_tree.preview_record_for_index(index)
        if record is None:
            return None
        if str(getattr(record, "audio_type", "") or "") in {"Non-Audio Assets", "Utility"}:
            return None
        path = getattr(record, "source_path", None)
        return Path(path) if path else None

    def _sync_docked_transport_selection(self) -> None:
        path = self._selected_audio_path()
        available = bool(path and path.exists())
        self.btn_preview_export.selected_path = path if available else None
        for button in (self.btn_preview_play, self.btn_preview_stop, self.btn_preview_export):
            button.setEnabled(available)
        if available:
            self._show_transport()
            if not self._preview_player.is_playing():
                self._schedule_transport_hide()
        else:
            self._schedule_transport_hide()

    def sync_transport_state(self) -> None:
        current_path = getattr(self._preview_player, "current_path", None)
        path = Path(current_path) if current_path else self._selected_audio_path()
        available = bool(path and path.exists())
        self.btn_preview_export.selected_path = path if available else None
        for button in (self.btn_preview_play, self.btn_preview_stop, self.btn_preview_export):
            button.setEnabled(available)
            button.updateGeometry()
        self._update_docked_play_icon(self._preview_player.get_state())
        self.main_layout.invalidate()
        self.main_layout.activate()
        self.scroll_content.adjustSize()
        self.scroll_content.updateGeometry()

    def set_category_options(self, options: list[tuple[str, str]]) -> None:
        active = set(self.category_carousel.active_values or set())
        self.category_carousel.set_options(options)
        self.category_carousel.set_active_values(active & {value for _label, value in options})

    def _toggle_docked_playback(self) -> None:
        path = self._selected_audio_path()
        if path is None:
            return
        if self._preview_player.current_path == path:
            self._preview_player.toggle_play_pause()
        else:
            self.playRequested.emit(path)

    def _update_docked_play_icon(self, state) -> None:
        from PySide6.QtMultimedia import QMediaPlayer

        playing = state == QMediaPlayer.PlayingState or (
            hasattr(QMediaPlayer, "PlaybackState")
            and state == QMediaPlayer.PlaybackState.PlayingState
        )
        self.btn_preview_play.setIcon(QIcon(str(PAUSE_ICON if playing else PLAY_ICON)))
        if playing:
            self._show_transport()
        else:
            self._schedule_transport_hide()

    def _show_transport(self) -> None:
        self._transport_hide_timer.stop()
        self.transport_shell.show()

    def _schedule_transport_hide(self) -> None:
        from PySide6.QtMultimedia import QMediaPlayer

        state = self._preview_player.get_state()
        paused = state == QMediaPlayer.PausedState or (
            hasattr(QMediaPlayer, "PlaybackState")
            and state == QMediaPlayer.PlaybackState.PausedState
        )
        if paused:
            self._show_transport()
            return
        self._transport_hide_timer.start()

    def _hide_inactive_transport(self) -> None:
        if not self._preview_player.is_playing():
            self.transport_shell.hide()

    def _force_single_tree_column(self) -> None:
        from PySide6.QtWidgets import QHeaderView

        model = self.view_tree.model()
        if model is None:
            return
        for column in range(1, model.columnCount()):
            self.view_tree.setColumnHidden(column, True)
        header = self.view_tree.header()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setStretchLastSection(True)
        self.view_tree.setColumnWidth(0, max(1, self.view_tree.viewport().width()))
        self.view_tree.horizontalScrollBar().setValue(0)


    def _on_type_clicked(self, _button):
        oneshots, loops, all_files = self.type_picker.get_state()
        self.typeToggleClicked.emit(oneshots, loops, all_files)

    def _on_filter_toggled(self, query, is_active):
        self.filter_carousel.set_active_values({query} if is_active else set())
        self.filterRequested.emit(query, is_active)

    def _on_filter_selected(self, query):
        self.filter_carousel.set_active_values({query})
        self.filterRequested.emit(query, True)

    def _on_category_toggled(self, category, is_active):
        self.category_carousel.set_active_values({category} if is_active else set())
        self.categoryFilterRequested.emit(category, is_active)

    def _on_category_selected(self, category):
        self.category_carousel.set_active_values({category})
        self.categoryFilterRequested.emit(category, True)


    def set_search_text(self, text: str):
        text = text or ""
        if self.edit_search.text() == text:
            return
        self.edit_search.blockSignals(True)
        self.edit_search.setText(text)
        self.edit_search.blockSignals(False)
        self._refresh_search_button_state()
        self.searchChanged.emit(text)

    def _refresh_search_button_state(self):
        self.btn_save_search.setEnabled(self.edit_search.isEnabled() and bool(self.edit_search.text().strip()))


    def set_filters(self, options: list[tuple[str, str]]):
        """Populates the filter carousel with (display_name, query) tuples."""
        self.filter_carousel.set_options(options)

    def set_active_saved_filters(self, queries: set[str]):
        self.filter_carousel.set_active_values(set(queries or set()))

    def set_category_state(self, active_values: set[str]):
        self.category_carousel.set_active_values(set(active_values or set()))


    def set_type_state(self, oneshots: bool, loops: bool, all_files: bool):
       self.type_picker.set_state(oneshots, loops, all_files)

    def set_confidence_range(self, min_val: float, max_val: float):
        self.tree_model.confidence_min = min_val
        self.tree_model.confidence_max = max_val

    def set_vibe_state(self, anchor_text: str, bias: int, visible: bool):
        self._vibe_state = {
            "anchor_text": anchor_text or "",
            "bias": bias,
            "visible": visible,
        }

    def selected_records(self):
        if self._view_mode != "tree":
            return []
        return list(self.view_tree._selected_records() or [])

    def ensure_map_page(self):
        if self.map_page is not None:
            return self.map_page
        from ..widgets.coherence_analyzer import CoherenceAnalyzerPage

        self.map_page = CoherenceAnalyzerPage(self, show_header=False, show_filters=False, show_zoom=False, default_zoom=4)
        self.map_page.setObjectName("DockMapPage")
        self.map_page.map_stage.setObjectName("DockMapStage")
        self.map_page.audioPreviewRequested.connect(self.audioPreviewRequested.emit)
        self.map_page.anchorRequested.connect(self.anchorRequested.emit)
        self.map_page.findRequested.connect(self.findRequested.emit)
        self.map_page.vibeRequested.connect(lambda path: self.similarityRequested.emit(Path(path)))
        self.map_page.status.hide()
        self.map_page.audio_reserve.setMinimumHeight(0)
        self.map_page.audio_reserve.setMaximumHeight(0)
        self.view_stack.addWidget(self.map_page)
        self._apply_docked_map_square()
        if self._adaptive_palette is not None:
            self.apply_adaptive_palette(self._adaptive_palette, animate=False)
        else:
            self._apply_normal_theme()
        return self.map_page

    def set_docked_view_mode(self, mode: str, *, emit: bool = True) -> None:
        mode = "map" if (mode or "").lower() == "map" else "tree"
        if mode == "map" and not self._map_available:
            mode = "tree"
        if mode == "map":
            page = self.ensure_map_page()
            self.view_stack.setCurrentWidget(page)
            self._apply_docked_map_square()
        else:
            self.view_stack.setCurrentWidget(self.view_tree)
            self.view_stack.setMinimumHeight(DOCKED_TREE_PANEL_MIN_HEIGHT)
            self.view_stack.setMaximumHeight(16777215)
        changed = self._view_mode != mode
        self._view_mode = mode
        self.btn_tree_view.setChecked(mode == "tree")
        self.btn_map_view.setChecked(mode == "map")
        self._refresh_view_mode_buttons()
        if changed and emit:
            self.viewModeChanged.emit(mode)

    def set_map_available(self, available: bool) -> None:
        self._map_available = available
        self.btn_map_view.setVisible(self._map_available)
        self.btn_map_view.setEnabled(self._map_available)
        if not self._map_available and self._view_mode == "map":
            self.set_docked_view_mode("tree")

    def refresh_map_from_app(self, app, *, force: bool = False) -> None:
        if self._view_mode != "map":
            return
        page = self.ensure_map_page()
        page.set_loading(True, "Preparing docked map...")
        self._refresh_map_page(page, app, force=force)

    def prewarm_map_from_app(self, app, *, force: bool = False) -> None:
        if not self._map_available:
            return
        page = self.ensure_map_page()
        self._apply_docked_map_square()
        self._refresh_map_page(page, app, force=force)
        if hasattr(page, "prewarm_library_projections"):
            page.prewarm_library_projections()

    def _refresh_map_page(self, page, app, *, force: bool = False) -> None:
        audio_type = self._current_audio_type_filter()
        category = self._current_category_filter()
        page.refresh_from_app(
            app,
            force=force,
            audio_type=audio_type,
            category=category,
        )
        if hasattr(page, "set_library_filters"):
            page.set_library_filters(audio_type, category, self._visible_record_ids_from_app(app))

    def _visible_record_ids_from_app(self, app) -> set[str] | None:
        library_tab = getattr(app, "library_tab", None)
        if library_tab is not None and hasattr(library_tab, "_visible_record_ids_from_proxy"):
            return library_tab._visible_record_ids_from_proxy()
        return None

    def _current_audio_type_filter(self) -> str:
        oneshots, loops, all_files = self.type_picker.get_state()
        if all_files:
            return ""
        if loops and not oneshots:
            return "Loops"
        if oneshots and not loops:
            return "Oneshots"
        return ""

    def _current_category_filter(self) -> str:
        values = set(self.category_carousel.active_values or set())
        if len(values) == 1:
            return str(next(iter(values)))
        return ""

    def _refresh_view_mode_buttons(self) -> None:
        self.btn_tree_view.refresh_theme()
        self.btn_map_view.refresh_theme()

    def _apply_docked_map_square(self) -> None:
        if self.map_page is None:
            return
        side = max(1, self.view_stack.width())
        self.map_page.map_stage.setMinimumHeight(side)
        self.map_page.map_stage.setMaximumHeight(side)
        self.view_stack.setMinimumHeight(side)
        self.view_stack.setMaximumHeight(16777215)

    def preferred_docked_height_for_mode(self, mode: str) -> int:
        if (mode or "").lower() == "map":
            self._apply_docked_map_square()
        else:
            self.view_stack.setMinimumHeight(DOCKED_TREE_PANEL_MIN_HEIGHT)
            self.view_stack.setMaximumHeight(16777215)
        self.scroll_content.adjustSize()
        self.updateGeometry()
        return max(DOCKED_MINIMUM_HEIGHT, self.scroll_content.sizeHint().height())

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "hover_title_strip"):
            self._position_chrome()
        if self._view_mode == "map":
            self._apply_docked_map_square()

    def set_environment_scanning(self, scanning: bool) -> None:
        self.environment_screen_overlay.set_scanning(bool(scanning))

    def set_hover_menus(self, *menus) -> None:
        self.hover_title_strip.set_menus(*menus)

    def set_hover_title_enabled(self, enabled: bool) -> None:
        enabled = bool(enabled)
        self._chrome_enabled = enabled
        window = self.window()
        if enabled and self.hover_title_strip.parentWidget() is not window:
            self.hover_title_strip.setParent(window)
            self.top_resize_edge.setParent(window)
            self.bottom_resize_edge.setParent(window)
            self.left_resize_edge.setParent(window)
            self.right_resize_edge.setParent(window)
        self.top_resize_edge.setVisible(enabled)
        self.bottom_resize_edge.setVisible(enabled)
        self.left_resize_edge.setVisible(enabled)
        self.right_resize_edge.setVisible(enabled)
        self._set_chrome_visible(enabled)
        self._position_chrome()

    def _set_chrome_visible(self, visible: bool) -> None:
        visible = bool(visible and self._chrome_enabled)
        chrome_height = 28 if visible else 0
        self.chrome_spacer.set_extent(chrome_height)
        if visible == self._chrome_visible:
            self.hover_title_strip.setVisible(visible)
            self._position_chrome()
            return
        self._chrome_visible = visible
        self.hover_title_strip.setFixedHeight(28)
        self.hover_title_strip.setVisible(visible)
        self._position_chrome()

    def _position_chrome(self) -> None:
        window = self.window()
        if window is None:
            return
        self.hover_title_strip.setGeometry(QRect(0, 0, window.width(), 28))
        self.top_resize_edge.setGeometry(0, 0, window.width(), 4)
        self.bottom_resize_edge.setGeometry(0, max(0, window.height() - 4), window.width(), 4)
        self.left_resize_edge.setGeometry(0, 28, 4, max(0, window.height() - 32))
        self.right_resize_edge.setGeometry(max(0, window.width() - 4), 28, 4, max(0, window.height() - 32))
        if self.hover_title_strip.isVisible():
            self.hover_title_strip.raise_()
        if self.top_resize_edge.isVisible():
            self.top_resize_edge.raise_()
        if self.bottom_resize_edge.isVisible():
            self.bottom_resize_edge.raise_()
        if self.left_resize_edge.isVisible():
            self.left_resize_edge.raise_()
        if self.right_resize_edge.isVisible():
            self.right_resize_edge.raise_()

    @staticmethod
    def _adaptive_widget_style(palette) -> str:
        return f"""
            /* dock-adaptive */
            QWidget#DockView {{ color: {palette.text}; background: {palette.base}; }}
            QScrollArea#DockScrollArea, QScrollArea#DockScrollArea::viewport,
            QWidget#DockScrollContent, QStackedWidget {{
                color: {palette.text}; background: {palette.base}; border: none;
            }}
            QFrame#DockSearchShell {{ background: {palette.base}; border: none; }}
            QWidget#DockMapPage, QFrame#DockMapStage {{ background: {palette.base}; border: none; }}
            QLabel {{ color: {palette.text}; background: transparent; }}
            QAbstractScrollArea, QAbstractScrollArea::viewport, QTreeView, QListView, QTableView {{
                color: {palette.text}; background: transparent; border-color: {palette.border};
                selection-background-color: {palette.selection}; selection-color: {palette.text};
            }}
            QTreeView::item:hover {{ background: {palette.hover}; color: {palette.text}; }}
            QTreeView::item:selected, QTreeView::item:selected:active,
            QTreeView::item:selected:!active {{ background: {palette.selection}; color: {palette.text}; }}
            QLineEdit, QComboBox, QSpinBox {{
                color: {palette.text}; background: {palette.raised}; border-color: {palette.border};
                selection-background-color: {palette.selection};
            }}
            QMenu, QComboBox QAbstractItemView {{
                color: {palette.text}; background: {palette.raised}; border-color: {palette.border};
                selection-background-color: {palette.selection};
            }}
            QSlider::groove:horizontal {{ background: {palette.border}; }}
            QSlider::handle:horizontal {{ background: {palette.accent}; }}
            QScrollBar:vertical, QScrollBar:horizontal {{ background: {palette.scrollbar}; }}
            QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{ background: {palette.scrollbar_handle}; }}
        """

    @staticmethod
    def _normal_dock_palette() -> DockAdaptivePalette:
        """Expose the active app theme through the dock's canonical palette shape."""
        return DockAdaptivePalette(
            base=ColorPalette.BG_DARK,
            darker=ColorPalette.BG_DARKER,
            panel=ColorPalette.BG_MED,
            raised=ColorPalette.BG_LIGHT,
            hover=ColorPalette.BG_HOVER,
            border=ColorPalette.BORDER,
            accent=ColorPalette.PRIMARY,
            accent_hover=ColorPalette.PRIMARY_HOVER,
            text=ColorPalette.TEXT_MAIN,
            muted=ColorPalette.TEXT_DIM,
            selection=ColorPalette.SELECTION,
            scrollbar=ColorPalette.BG_SCROLLBAR,
            scrollbar_handle=ColorPalette.BG_SCROLLBAR_HANDLE,
            source_theme="app-theme",
        )

    def _apply_canonical_dock_styles(self, palette, color_transform=None) -> None:
        """Apply the shared dock presentation independently of camouflage state."""
        self.setStyleSheet(dock_view_style() + self._adaptive_widget_style(palette))
        widget_palette = QPalette(self.palette())
        widget_palette.setColor(QPalette.Window, QColor(palette.base))
        widget_palette.setColor(QPalette.Base, QColor(palette.base))
        widget_palette.setColor(QPalette.AlternateBase, QColor(palette.raised))
        widget_palette.setColor(QPalette.Button, QColor(palette.raised))
        widget_palette.setColor(QPalette.Text, QColor(palette.text))
        widget_palette.setColor(QPalette.WindowText, QColor(palette.text))
        widget_palette.setColor(QPalette.ButtonText, QColor(palette.text))
        widget_palette.setColor(QPalette.Highlight, QColor(palette.accent))
        self.setPalette(widget_palette)
        for button in (
            self.btn_preview_play,
            self.btn_preview_stop,
            self.btn_preview_export,
        ):
            if hasattr(button, "set_color_transform"):
                button.set_color_transform(color_transform)
        for button in (self.btn_tree_view, self.btn_map_view):
            button.set_color_transform(None)
            button.set_adaptive_colors(
                palette.accent,
                palette.hover,
                palette.text,
                palette.text,
            )
        self._apply_chrome_style(palette.panel, palette.text, palette.border, palette.hover)
        self._set_native_border_color(self._adaptive_outer_border(palette.base))
        self._apply_adaptive_component_styles(palette, color_transform)

    def _apply_adaptive_palette_now(self, palette) -> None:
        self._adaptive_palette = palette
        self._adaptive_tree_delegate.palette = palette
        color_transform = lambda color, current=palette: transfer_palette_color(color, current)
        self._apply_canonical_dock_styles(palette, color_transform)
        self.view_tree.set_branch_color(palette.border)
        self.view_tree.set_branch_color_transform(color_transform)
        if self.map_page is not None:
            self.map_page.map.set_adaptive_palette(palette, color_transform)

    def _apply_adaptive_component_styles(self, palette, color_transform) -> None:
        self.options_section.apply_adaptive_palette(palette)
        for carousel in (self.filter_carousel, self.category_carousel):
            carousel.apply_adaptive_palette(palette, color_transform)
        self.type_picker.apply_adaptive_palette(palette)
        self.btn_save_search.setStyleSheet(
            f"QPushButton {{ color: {palette.text}; background: {palette.accent}; border: none; }}"
            f"QPushButton:hover {{ background: {palette.accent_hover}; }}"
            f"QPushButton:disabled {{ color: {palette.muted}; background: {palette.raised}; }}"
        )
        self.search_shell.setStyleSheet(f"background: {palette.base}; border: none;")
        self.transport_shell.setStyleSheet(f"background: {palette.base}; border: none;")
        self.view_tree.setStyleSheet(
            f"QTreeView {{ color: {palette.text}; background: {palette.base}; border: none; "
            f"selection-background-color: {palette.selection}; selection-color: {palette.text}; }}"
            f"QTreeView::item:hover {{ background: {palette.hover}; color: {palette.text}; }}"
            f"QTreeView::item:selected, QTreeView::item:selected:active, "
            f"QTreeView::item:selected:!active {{ background: {palette.selection}; color: {palette.text}; }}"
        )
        if self.map_page is not None:
            self.map_page.setStyleSheet(f"background: {palette.base}; border: none;")
            self.map_page.map_stage.setStyleSheet(f"background: {palette.base}; border: none;")

    def _apply_default_chrome_style(self) -> None:
        self._apply_chrome_style(
            ColorPalette.BG_MED,
            ColorPalette.TEXT_MAIN,
            ColorPalette.BORDER,
            ColorPalette.BG_HOVER,
        )

    def _apply_chrome_style(self, panel: str, text: str, border: str, hover: str) -> None:
        self.hover_title_strip.set_background_colors(panel, border)
        self.hover_title_strip.set_foreground_color(text)
        chrome_palette = QPalette(self.hover_title_strip.palette())
        chrome_palette.setColor(QPalette.Window, QColor(panel))
        chrome_palette.setColor(QPalette.WindowText, QColor(text))
        self.hover_title_strip.setPalette(chrome_palette)
        self.hover_title_strip.setAutoFillBackground(True)
        self.hover_title_strip.setStyleSheet(
            f"QWidget#DockHoverTitleStrip {{ background: transparent; color: {text}; border: none; }}"
            f"QWidget#DockHoverTitleStrip QLabel, QWidget#DockHoverTitleStrip QToolButton {{ "
            f"background: transparent; color: {text}; border: none; }}"
            f"QWidget#DockHoverTitleStrip QToolButton:hover {{ background: {hover}; }}"
        )

    def _dispose_palette_overlay(self) -> None:
        overlay = self._palette_overlay
        animation = self._palette_overlay_animation
        self._palette_overlay = None
        self._palette_overlay_animation = None
        if animation is not None:
            try:
                animation.stop()
            except RuntimeError:
                pass
        if overlay is None:
            return
        try:
            overlay.deleteLater()
        except RuntimeError:
            pass

    def apply_adaptive_palette(self, palette, *, animate: bool = True) -> None:
        snapshot = self.grab() if animate and self.isVisible() else None
        self._apply_adaptive_palette_now(palette)
        if snapshot is None or snapshot.isNull():
            return
        self._dispose_palette_overlay()
        overlay = DockPaletteTransitionOverlay(snapshot, self)
        overlay.setGeometry(self.rect())
        overlay.show()
        overlay.raise_()
        animation = QPropertyAnimation(overlay, b"progress", self)
        animation.setDuration(1200)
        animation.setStartValue(0.0)
        animation.setEndValue(1.0)
        animation.setEasingCurve(QEasingCurve.OutCubic)
        animation.finished.connect(self._dispose_palette_overlay)
        animation.start()
        self._palette_overlay = overlay
        self._palette_overlay_animation = animation

    def clear_adaptive_palette(self) -> None:
        self._dispose_palette_overlay()
        self.set_environment_scanning(False)
        self._adaptive_palette = None
        self._adaptive_tree_delegate.palette = None
        self._set_native_border_color(None)
        self._apply_default_chrome_style()
        for button in (
            self.btn_tree_view,
            self.btn_map_view,
            self.btn_preview_play,
            self.btn_preview_stop,
            self.btn_preview_export,
        ):
            if hasattr(button, "set_color_transform"):
                button.set_color_transform(None)
        if self.map_page is not None:
            self.map_page.map.clear_adaptive_palette()
            self.map_page.refresh_theme()
        self.view_tree.set_branch_color_transform(None)
        self._apply_normal_theme()

    def _apply_normal_theme(self) -> None:
        palette = self._normal_dock_palette()
        self._adaptive_tree_delegate.palette = None
        self.view_tree.refresh_theme()
        if self.map_page is not None:
            self.map_page.map.clear_adaptive_palette()
            self.map_page.refresh_theme()

        # Generic theme refreshes may replace child stylesheets, so install the
        # dock's canonical presentation last in both normal and camouflage modes.
        self._apply_canonical_dock_styles(palette)
        self.view_tree.set_branch_color(ColorPalette.BORDER)
        self.view_tree.set_branch_color_transform(None)
        if self.map_page is not None:
            self.map_page.setStyleSheet(f"background: {palette.base}; border: none;")
            self.map_page.map_stage.setStyleSheet(f"background: {palette.base}; border: none;")

    def _set_native_border_color(self, color: str | None) -> None:
        if os.name != "nt" or os.environ.get("QT_QPA_PLATFORM") == "offscreen":
            return
        try:
            import ctypes

            window = self.window()
            handle = window.windowHandle()
            if handle is None:
                return

            if color is None:
                value = ctypes.c_uint32(0xFFFFFFFF)  # DWMWA_COLOR_DEFAULT
            else:
                parsed = QColor(color)
                value = ctypes.c_uint32(
                    parsed.red() | (parsed.green() << 8) | (parsed.blue() << 16)
                )
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                int(handle.winId()),
                34,  # DWMWA_BORDER_COLOR
                ctypes.byref(value),
                ctypes.sizeof(value),
            )
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
            pass

    @staticmethod
    def _adaptive_outer_border(background: str) -> str:
        base = QColor(background)
        if not base.isValid():
            return background
        target = QColor("#000000" if base.lightnessF() >= 0.5 else "#ffffff")
        blend = 0.10
        return QColor(
            round(base.red() * (1.0 - blend) + target.red() * blend),
            round(base.green() * (1.0 - blend) + target.green() * blend),
            round(base.blue() * (1.0 - blend) + target.blue() * blend),
        ).name()

    def restore_native_border(self) -> None:
        self._set_native_border_color(None)

    def refresh_theme(self) -> None:
        adaptive_palette = self._adaptive_palette
        self._apply_normal_theme()
        if adaptive_palette is not None:
            self.apply_adaptive_palette(adaptive_palette, animate=False)
