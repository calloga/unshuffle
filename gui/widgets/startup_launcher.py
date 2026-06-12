from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from gui.core.settings_controller import (
    DEFAULT_LIBRARY_VIEW_MODES,
    normalize_library_view_modes,
)
from gui.styles import ThemeManager
from unshuffle.core.assets import asset_path
from gui.utils.app_icon import apply_app_icon
from gui.utils.history import load_session_sources
from gui.utils.styles import ColorPalette, make_qcolor, scaled_px, set_zoom_percent, sync_color_palette


@dataclass(frozen=True)
class StartupLaunchRequest:
    mode: str
    target: str = ""
    session_id: str = ""
    roots: tuple[str, ...] = ()
    view_modes: tuple[str, ...] = ("table", "tree")
    show_launcher_next_time: bool = True
    import_path: str = ""

    def to_settings(self) -> dict:
        mode = "restore" if self.mode in {"import_session", "import_csv"} else self.mode
        return {
            "mode": mode,
            "target": self.target if mode == self.mode else "",
            "session_id": self.session_id if mode == self.mode else "",
            "roots": list(self.roots) if mode == self.mode else [],
            "view_modes": list(self.view_modes),
            "import_path": self.import_path if mode == self.mode else "",
        }

    @classmethod
    def from_settings(cls, data: dict, *, fallback_target: str = "") -> "StartupLaunchRequest":
        mode = str(data.get("mode") or "restore")
        roots = tuple(str(root) for root in data.get("roots") or () if str(root).strip())
        return cls(
            mode=mode if mode in {"restore", "refresh", "empty", "import_session", "import_csv"} else "restore",
            target=str(data.get("target") or fallback_target or ""),
            session_id=str(data.get("session_id") or ""),
            roots=roots,
            view_modes=tuple(normalize_library_view_modes(data.get("view_modes") or DEFAULT_LIBRARY_VIEW_MODES)),
            show_launcher_next_time=False,
            import_path=str(data.get("import_path") or ""),
        )


class StartupLauncherDialog(QDialog):
    """Small preflight surface for choosing startup work before heavy restore begins."""

    def __init__(self, settings_controller, parent=None, *, force_refresh: bool = False):
        super().__init__(parent)
        self.settings_controller = settings_controller
        self.settings = settings_controller.settings
        self._force_refresh = force_refresh
        self._roots_values: list[str] = []
        self._selected_session_id = str(self.settings.value("last_scan_session_id", "") or "")
        self._initial_roots: tuple[str, ...] = ()
        self._request: StartupLaunchRequest | None = None
        self._new_session_requested = False
        self._theme_is_dark = True
        self.setWindowTitle("Unshuffle Launcher")
        apply_app_icon(self)
        self._apply_theme()
        self.resize(scaled_px(430), scaled_px(420))
        self._setup_ui()
        self._load_initial_state()
        if self._force_refresh and self._selected_session_id:
            self._selected_session_id = ""
            self._refresh_summary()

    def launch_request(self) -> StartupLaunchRequest:
        if self._request is not None:
            return self._request
        return self._build_request()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(scaled_px(16), scaled_px(16), scaled_px(16), scaled_px(12))
        layout.setSpacing(scaled_px(10))
        self.setStyleSheet(self._style_sheet())

        self.roots_container = QWidget()
        self.roots_layout = QVBoxLayout(self.roots_container)
        self.roots_layout.setContentsMargins(0, 0, 0, 0)
        self.roots_layout.setSpacing(2)
        self.btn_add_root = QPushButton("+")
        self.btn_add_root.setObjectName("FullAddButton")
        self.btn_add_root.setProperty("launcherPanelButton", True)
        self.btn_add_root.clicked.connect(self._add_folder)
        self.btn_new_session = QPushButton("New Session")
        self.btn_new_session.setProperty("launcherPanelButton", True)
        self.btn_new_session.clicked.connect(self._new_session)
        directories_panel = self._panel_with("Directories", self.roots_container, footer_widgets=(self.btn_add_root, self.btn_new_session))
        layout.addWidget(directories_panel)

        import_container = QWidget()
        import_layout = QHBoxLayout(import_container)
        import_layout.setContentsMargins(0, 0, 0, 0)
        import_layout.setSpacing(6)
        btn_import_session = QPushButton("Import Session")
        btn_import_session.setProperty("launcherPanelButton", True)
        btn_import_session.clicked.connect(self._import_session_clicked)
        btn_import_csv = QPushButton("Import CSV")
        btn_import_csv.setProperty("launcherPanelButton", True)
        btn_import_csv.clicked.connect(self._import_csv_clicked)
        import_layout.addWidget(btn_import_session, 1)
        import_layout.addWidget(btn_import_csv, 1)
        import_panel = self._panel_with("Import", import_container)
        layout.addWidget(import_panel)

        view_container = QWidget()
        view_row = QHBoxLayout(view_container)
        view_row.setContentsMargins(0, 0, 0, 0)
        view_row.setSpacing(12)
        self.view_checks: dict[str, QCheckBox] = {}
        for mode, label, icon_path in (
            ("table", "Table", "icons/table.png"),
            ("tree", "Tree", "icons/tree.png"),
            ("map", "Map", "icons/map.png"),
        ):
            check = QCheckBox(label)
            check.setIcon(self._tinted_icon(icon_path))
            check.setIconSize(QSize(scaled_px(14), scaled_px(14)))
            check.setChecked(True)
            check.toggled.connect(self._refresh_summary)
            self.view_checks[mode] = check
            view_row.addWidget(check)
        view_row.addStretch(1)
        view_panel = self._panel_with("Views", view_container)
        layout.addWidget(view_panel)

        bottom = QHBoxLayout()
        self.dont_show = QCheckBox("Don't show again")
        bottom.addWidget(self.dont_show)
        bottom.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        bottom.addWidget(cancel)
        launch = QPushButton("Launch")
        launch.setProperty("primary", True)
        launch.clicked.connect(self._accept_launch)
        bottom.addWidget(launch)
        layout.addLayout(bottom)

    def _panel_with(self, title: str, widget, *, footer_widgets: tuple[QWidget, ...] = ()) -> QFrame:
        panel = QFrame()
        panel.setObjectName("Panel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(scaled_px(12), scaled_px(9), scaled_px(12), scaled_px(9))
        layout.setSpacing(scaled_px(7))
        layout.addWidget(self._section_label(title))
        layout.addWidget(widget)
        for footer_widget in footer_widgets:
            layout.addWidget(footer_widget)
        return panel

    @staticmethod
    def _section_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("Section")
        return label

    @staticmethod
    def _tinted_icon(icon_path: str) -> QIcon:
        pixmap = QPixmap(str(asset_path(*icon_path.replace("\\", "/").split("/"))))
        if pixmap.isNull():
            return QIcon()
        pixmap = pixmap.scaled(QSize(scaled_px(14), scaled_px(14)), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        tint = make_qcolor(ColorPalette.TEXT_MAIN)
        painter = QPainter(pixmap)
        try:
            painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
            painter.fillRect(pixmap.rect(), tint)
        finally:
            painter.end()
        return QIcon(pixmap)

    def _apply_theme(self) -> None:
        manager = ThemeManager()
        get_theme_key = getattr(self.settings_controller, "get_theme_key", None)
        theme_key = str(get_theme_key() if callable(get_theme_key) else "")
        manager.set_theme(theme_key)
        app = QApplication.instance()
        manager.sync_system_theme(app if isinstance(app, QApplication) else None)
        get_zoom_percent = getattr(self.settings_controller, "get_zoom_percent", None)
        if callable(get_zoom_percent):
            raw_zoom = get_zoom_percent()
            try:
                zoom_percent = int(raw_zoom) if isinstance(raw_zoom, (str, int, float)) else 100
            except (TypeError, ValueError):
                zoom_percent = 100
            manager.set_zoom(zoom_percent)
            set_zoom_percent(manager.state.zoom_percent)
        sync_color_palette(manager.colors)
        self._theme_is_dark = bool(manager.colors.is_dark)

    @staticmethod
    def _color(value: str, alpha: int | None = None) -> str:
        color = make_qcolor(value)
        if alpha is not None:
            color.setAlpha(max(0, min(255, alpha)))
        return color.name(QColor.HexArgb if color.alpha() < 255 else QColor.HexRgb)

    @staticmethod
    def _lighter(value: str, factor: int = 112) -> str:
        color = make_qcolor(value).lighter(factor)
        return color.name(QColor.HexArgb if color.alpha() < 255 else QColor.HexRgb)

    @staticmethod
    def _darker(value: str, factor: int = 112) -> str:
        color = make_qcolor(value).darker(factor)
        return color.name(QColor.HexArgb if color.alpha() < 255 else QColor.HexRgb)

    @staticmethod
    def _mix(foreground: str, background: str, amount: float) -> str:
        fg = make_qcolor(foreground)
        bg = make_qcolor(background)
        amount = max(0.0, min(1.0, amount))
        mixed = QColor(
            round(fg.red() * amount + bg.red() * (1.0 - amount)),
            round(fg.green() * amount + bg.green() * (1.0 - amount)),
            round(fg.blue() * amount + bg.blue() * (1.0 - amount)),
        )
        return mixed.name(QColor.HexRgb)

    def _style_sheet(self) -> str:
        panel = self._darker(ColorPalette.SURFACE_CARD, 112) if self._theme_is_dark else self._color(ColorPalette.SURFACE_CARD)
        subtle = self._lighter(ColorPalette.ACTION_SECONDARY, 112) if self._theme_is_dark else self._color(ColorPalette.ACTION_SECONDARY)
        button_hover = self._color(ColorPalette.BG_HOVER if self._theme_is_dark else ColorPalette.ACTION_SECONDARY)
        panel_button_bg = subtle if self._theme_is_dark else self._mix(ColorPalette.PRIMARY, ColorPalette.SURFACE_CARD, 0.14)
        panel_button_hover = button_hover if self._theme_is_dark else self._color(ColorPalette.PRIMARY)
        panel_button_hover_text = self._color(ColorPalette.TEXT_MAIN if self._theme_is_dark else ColorPalette.TEXT_INVERSE)
        checkbox_hover = self._color(ColorPalette.TABLE_HOVER)
        checkbox_bg = self._color(ColorPalette.STATUS_INFO_SOFT)
        dialog_bg = self._color(ColorPalette.BG_DARKER)
        return (
            f"QDialog {{ background: {dialog_bg}; color: {self._color(ColorPalette.TEXT_LIGHT)}; }}"
            f"QLabel#Section {{ color: {self._color(ColorPalette.TEXT_LIGHT)}; font-weight: 700; "
            f"font-size: {scaled_px(12)}px; letter-spacing: 0px; }}"
            f"QFrame#Panel {{ background: {panel}; border: none; border-radius: {scaled_px(8)}px; }}"
            f"QComboBox {{ background: {subtle}; border: none; border-radius: {scaled_px(4)}px; "
            f"padding: {scaled_px(5)}px; color: {self._color(ColorPalette.TEXT_MAIN)}; min-height: {scaled_px(30)}px; }}"
            f"QComboBox QAbstractItemView {{ background: {self._color(ColorPalette.BG_DROPDOWN)}; "
            f"color: {self._color(ColorPalette.TEXT_MAIN)}; selection-background-color: {self._color(ColorPalette.PRIMARY)}; "
            f"selection-color: {self._color(ColorPalette.TEXT_INVERSE)}; outline: none; }}"
            f"QLabel#RootLabel {{ color: {self._color(ColorPalette.TEXT_MAIN)}; padding: {scaled_px(6)}px {scaled_px(2)}px; }}"
            f"QPushButton {{ background: {subtle}; border: none; border-radius: {scaled_px(4)}px; "
            f"padding: 0 {scaled_px(14)}px; min-height: {scaled_px(32)}px; color: {self._color(ColorPalette.TEXT_MAIN)}; "
            "font-weight: 700; }"
            f"QPushButton:hover {{ background: {button_hover}; }}"
            f"QPushButton:disabled {{ background: {self._color(ColorPalette.BG_MED)}; color: {self._color(ColorPalette.TEXT_INACTIVE)}; }}"
            "QPushButton#FlatIconButton { background: transparent; padding: 0; min-height: 0; }"
            "QPushButton#FlatIconButton:hover { background: transparent; }"
            f"QPushButton#FullAddButton {{ font-size: {scaled_px(16)}px; font-weight: 700; }}"
            f"QPushButton[launcherPanelButton=\"true\"] {{ background: {panel_button_bg}; }}"
            f"QPushButton[launcherPanelButton=\"true\"]:hover {{ background: {panel_button_hover}; color: {panel_button_hover_text}; }}"
            f"QPushButton[primary=\"true\"] {{ background: {self._color(ColorPalette.PRIMARY)}; "
            f"color: {self._color(ColorPalette.TEXT_INVERSE)}; font-weight: 700; }}"
            f"QPushButton[primary=\"true\"]:hover {{ background: {self._color(ColorPalette.PRIMARY_HOVER)}; }}"
            f"QCheckBox {{ color: {self._color(ColorPalette.TEXT_MAIN)}; spacing: {scaled_px(6)}px; }}"
            f"QCheckBox::indicator {{ width: {scaled_px(15)}px; height: {scaled_px(15)}px; "
            f"border: 1px solid transparent; border-radius: {scaled_px(2)}px; background: {checkbox_bg}; }}"
            f"QCheckBox::indicator:hover {{ background: {checkbox_hover}; }}"
            f"QCheckBox::indicator:checked {{ background: {self._color(ColorPalette.PRIMARY)}; border-color: transparent; }}"
        )

    def _load_initial_state(self) -> None:
        target = self._target()
        view_modes = self.settings_controller.get_library_view_modes()
        for mode, check in self.view_checks.items():
            check.setChecked(mode in view_modes)
        self._set_roots(self._default_roots())
        self._initial_roots = self._roots()
        self._refresh_summary()

    def _target(self) -> str:
        return str(
            self.settings.value("last_library_target", "")
            or self.settings.value("last_scan_source", "")
            or self.settings.value("last_target", "")
            or ""
        )

    def _default_roots(self) -> list[str]:
        target = self._target()
        session_id = self._selected_session_id
        roots = load_session_sources(target, session_id) if target and session_id else []
        if roots:
            return roots
        last_source = str(self.settings.value("last_scan_source", "") or "")
        return [last_source] if last_source else []

    def _set_roots(self, roots: list[str]) -> None:
        self._roots_values = [root for root in roots if root.strip()]
        self._render_roots()
        self._refresh_summary()

    def _roots(self) -> tuple[str, ...]:
        return tuple(self._roots_values)

    def _render_roots(self) -> None:
        while self.roots_layout.count():
            item = self.roots_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        for root in self._roots_values:
            self.roots_layout.addWidget(self._root_row(root))

    def _root_row(self, root: str) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        label = QLabel(Path(root).name or root)
        label.setObjectName("RootLabel")
        label.setToolTip(root)
        layout.addWidget(label, 1)
        button = QPushButton("")
        button.setObjectName("FlatIconButton")
        pixmap = QPixmap(str(asset_path("icons", "close.png")))
        if not pixmap.isNull():
            tint = make_qcolor(ColorPalette.TEXT_MAIN)
            tint.setAlpha(150)
            painter = QPainter(pixmap)
            painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
            painter.fillRect(pixmap.rect(), tint)
            painter.end()
            button.setIcon(QIcon(pixmap))
        button.setFixedSize(22, 22)
        button.setIconSize(button.size())
        button.clicked.connect(lambda _checked=False, value=root: self._remove_root(value))
        layout.addWidget(button)
        return row

    def _selected_view_modes(self) -> tuple[str, ...]:
        modes = [mode for mode, check in self.view_checks.items() if check.isChecked()]
        return tuple(normalize_library_view_modes(modes))

    def _add_folder(self) -> None:
        start = self._roots()[0] if self._roots() else str(self.settings.value("last_scan_source", "") or "")
        folder = QFileDialog.getExistingDirectory(self, "Add Folder", start)
        if folder and folder not in self._roots():
            self._roots_values.append(folder)
            self._render_roots()
            self._refresh_summary()

    def _remove_root(self, root: str) -> None:
        self._roots_values = [value for value in self._roots_values if value != root]
        self._render_roots()
        self._refresh_summary()

    def _new_session(self) -> None:
        self._mark_new_session()
        self._add_folder()

    def _mark_new_session(self) -> None:
        self._new_session_requested = True
        self._selected_session_id = ""
        self._initial_roots = ()
        self._set_roots([])

    def _refresh_summary(self) -> None:
        self.setWindowTitle("Unshuffle Launcher")

    def _build_request(self) -> StartupLaunchRequest:
        roots = self._roots()
        view_modes = self._selected_view_modes()
        target = (roots[0] if self._new_session_requested and roots else "") or self._target() or (roots[0] if roots else "")
        if not roots:
            mode = "empty"
        elif roots == self._initial_roots and self._selected_session_id:
            mode = "restore"
        else:
            mode = "refresh"
        return StartupLaunchRequest(
            mode=mode,
            target=target,
            session_id=self._selected_session_id if mode == "restore" else "",
            roots=roots,
            view_modes=view_modes,
            show_launcher_next_time=not self.dont_show.isChecked(),
        )

    def _accept_launch(self) -> None:
        self._request = self._build_request()
        self.accept()

    def _import_session_clicked(self) -> None:
        from pathlib import Path
        last_tgt = self.settings.value("last_target", "")
        default_dir = str(Path(last_tgt).parent) if last_tgt and Path(last_tgt).exists() else str(Path.home())
        
        path, _ = QFileDialog.getOpenFileName(self, "Select Staging Session File to Import", default_dir, "Unshuffle Session Files (*.unshuffle);;All Files (*)")
        if path:
            self._request = StartupLaunchRequest(
                mode="import_session",
                import_path=path,
                show_launcher_next_time=not self.dont_show.isChecked(),
            )
            self.accept()

    def _import_csv_clicked(self) -> None:
        from pathlib import Path
        last_tgt = self.settings.value("last_target", "")
        default_dir = str(Path(last_tgt).parent) if last_tgt and Path(last_tgt).exists() else str(Path.home())
        
        path, _ = QFileDialog.getOpenFileName(self, "Select CSV to Import", default_dir, "CSV Files (*.csv)")
        if path:
            self._request = StartupLaunchRequest(
                mode="import_csv",
                import_path=path,
                show_launcher_next_time=not self.dont_show.isChecked(),
            )
            self.accept()
