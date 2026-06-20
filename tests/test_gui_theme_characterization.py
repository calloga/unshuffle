import os
import unittest
import uuid
from pathlib import Path
from unittest import mock

from PySide6.QtCore import QObject, Qt, QSettings
from PySide6.QtGui import QColor

from gui.core.settings_controller import SettingsController
from gui.styles import (
    ASH_THEME_KEY,
    OCEAN_THEME_KEY,
    PEARL_THEME_KEY,
    SUNSET_THEME_KEY,
    SYSTEM_THEME_KEY,
    ThemeManager,
    DEFAULT_THEME_KEY,
    normalize_theme_key,
    resolve_system_theme_key,
)


class ThemeNormalizationTests(unittest.TestCase):
    def test_supported_theme_keys_round_trip(self):
        self.assertEqual(normalize_theme_key(OCEAN_THEME_KEY), OCEAN_THEME_KEY)
        self.assertEqual(normalize_theme_key(ASH_THEME_KEY), ASH_THEME_KEY)

    def test_unknown_theme_key_falls_back_to_default_theme(self):
        self.assertEqual(normalize_theme_key("unknown-theme"), DEFAULT_THEME_KEY)

    def test_system_theme_mapping_matches_spec(self):
        self.assertEqual(resolve_system_theme_key(Qt.ColorScheme.Light), OCEAN_THEME_KEY)
        self.assertEqual(resolve_system_theme_key(Qt.ColorScheme.Dark), ASH_THEME_KEY)
        self.assertEqual(resolve_system_theme_key(None), ASH_THEME_KEY)


class ThemeManagerTests(unittest.TestCase):
    def test_theme_manager_tracks_requested_and_effective_theme_keys(self):
        manager = ThemeManager()

        manager.set_theme(SUNSET_THEME_KEY)
        self.assertEqual(manager.requested_theme_key, SUNSET_THEME_KEY)
        self.assertEqual(manager.state.effective_key, SUNSET_THEME_KEY)

        manager.set_theme(SYSTEM_THEME_KEY)
        manager.sync_system_theme(None)
        self.assertEqual(manager.requested_theme_key, SYSTEM_THEME_KEY)
        self.assertEqual(manager.state.effective_key, ASH_THEME_KEY)

    def test_dialog_buttons_use_dialog_specific_sizing_and_theme_tokens(self):
        manager = ThemeManager()
        qss = manager.build_qss()

        self.assertIn("QMessageBox QPushButton", qss)
        self.assertIn("QDialogButtonBox QPushButton", qss)
        self.assertIn("min-width: 76px", qss)
        self.assertIn("min-height: 30px", qss)
        self.assertIn(f"background: {manager.colors.primary};", qss)

    def test_tooltips_are_theme_bound(self):
        manager = ThemeManager()
        qss = manager.build_qss()

        self.assertIn("QToolTip", qss)
        self.assertIn(f"background-color: {manager.colors.bg_dropdown};", qss)
        self.assertIn(f"color: {manager.colors.text_main};", qss)
        self.assertIn(f"border: 1px solid {manager.colors.border_accent};", qss)

    def test_table_selection_tints_are_explicitly_visible(self):
        from gui.styles.tokens_semantic import THEMES
        from gui.utils.color_helpers import make_qcolor

        for theme_key in (DEFAULT_THEME_KEY, ASH_THEME_KEY, SUNSET_THEME_KEY):
            manager = ThemeManager()
            manager.set_theme(theme_key)
            table_bg = make_qcolor(THEMES[theme_key].bg_list)
            selected_bg = make_qcolor(THEMES[theme_key].table_select)
            self.assertTrue(selected_bg.isValid())
            self.assertGreater(selected_bg.alpha(), 0)
            self.assertGreater(abs(selected_bg.lightness() - table_bg.lightness()), 18)
            qss = manager.build_qss()
            self.assertIn(f"selection-background-color: {manager.colors.table_select};", qss)
            self.assertIn(
                f"QTableView::item:selected:active, QTableView::item:selected:!active {{ background: {manager.colors.table_select};",
                qss,
            )

    def test_make_qcolor_parses_fractional_rgba_alpha(self):
        from gui.utils.color_helpers import make_qcolor

        color = make_qcolor("rgba(36, 108, 252, 0.28)")

        self.assertTrue(color.isValid())
        self.assertGreater(color.alpha(), 0)
        self.assertLess(color.alpha(), 255)

    def test_workspace_table_check_indicators_have_visible_unchecked_state(self):
        from gui.utils.styles import ColorPalette, workspace_table_widget_style

        qss = workspace_table_widget_style()

        self.assertIn("QTableWidget::indicator", qss)
        self.assertIn(f"border: 1px solid {ColorPalette.BORDER_ACCENT};", qss)
        self.assertIn(f"QTableWidget::indicator:checked {{ background: {ColorPalette.PRIMARY};", qss)

    def test_tree_organization_help_banner_uses_workspace_banner_treatment(self):
        from gui.widgets.tree_organization.styles import editor_style

        qss = editor_style()

        self.assertIn("QLabel#TreeEditorHelpBanner", qss)
        self.assertIn("border-radius: 5px", qss)

    def test_build_page_scrollbar_rounds_outer_right_edge(self):
        from gui.utils.styles import build_page_style

        qss = build_page_style()

        self.assertIn("border-top-left-radius: 0; border-bottom-left-radius: 0;", qss)
        self.assertIn("border-top-right-radius: 4px; border-bottom-right-radius: 4px;", qss)


class SystemPageBannerTests(unittest.TestCase):
    def test_system_sections_have_distinct_parented_help_banners(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance() or QApplication([])

        from gui.widgets.system_page import SystemPage

        page = SystemPage()
        try:
            banners = [
                page.discovery_help,
                page.additions_help,
                page.corrections_help,
                page.my_anchor_status,
                page.anchor_candidates_help,
            ]

            self.assertEqual(len({id(banner) for banner in banners}), len(banners))
            for banner in banners:
                self.assertIsNotNone(banner.parentWidget())
                self.assertTrue(banner.wordWrap())
        finally:
            page.deleteLater()


class SupportDialogTests(unittest.TestCase):
    def test_help_menu_should_be_exposed(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance() or QApplication([])

        from gui.widgets.menu_bar import ModernMenuBar

        menu = ModernMenuBar()
        try:
            top_level = [action.text() for action in menu.actions() if action.text()]
            self.assertIn("Help", top_level)
            self.assertTrue(hasattr(menu, "menu_help"))
            self.assertFalse(hasattr(menu, "act_help_index")) # dont sure in this change
            self.assertTrue(hasattr(menu, "act_about"))
        finally:
            menu.deleteLater()

    def test_about_dialog_uses_app_logo_and_version_details(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication, QLabel

        app = QApplication.instance() or QApplication([])

        from gui.dialogs.about_dialog import build_about_dialog
        from gui.utils.styles import scaled_px

        dialog = build_about_dialog()
        try:
            self.assertEqual(dialog.windowTitle(), "About Unshuffle")
            logo = dialog.findChild(QLabel, "AboutLogo")
            self.assertIsNotNone(logo)
            self.assertIsNotNone(logo.pixmap())
            self.assertFalse(logo.pixmap().isNull())
            self.assertEqual(logo.width(), scaled_px(48))
            self.assertTrue(
                any(label.objectName() == "AboutTitle" and label.text() == "Unshuffle" for label in dialog.findChildren(QLabel))
            )
            self.assertTrue(
                any(
                    label.objectName() == "AboutSubtitle" and "V1.0.0" in label.text()
                    for label in dialog.findChildren(QLabel)
                )
            )
            self.assertFalse(any(label.objectName() == "AboutSummary" for label in dialog.findChildren(QLabel)))
            self.assertFalse(any(label.objectName() == "AboutDetailName" for label in dialog.findChildren(QLabel)))
        finally:
            dialog.deleteLater()

    def test_workspace_layer_no_longer_uses_taxonomy_names(self):
        gui_root = Path(__file__).resolve().parents[1] / "gui"
        forbidden = (
            "TaxonomyPage",
            "TaxonomyController",
            "taxonomy_page",
            "taxonomy_controller",
            "open_taxonomy_workspace",
            "menu_taxonomy",
            "TaxonomyCard",
        )
        allowed_files = {
            gui_root / "core" / "taxonomy_anchor_records.py",
            gui_root / "widgets" / "refinement_taxonomy.py",
        }
        offenders: list[str] = []
        for path in gui_root.rglob("*.py"):
            if path in allowed_files:
                continue
            text = path.read_text(encoding="utf-8")
            for token in forbidden:
                if token in text:
                    offenders.append(f"{path.relative_to(gui_root)}: {token}")

        self.assertEqual(offenders, [])


class SettingsControllerThemePersistenceTests(unittest.TestCase):
    def test_theme_key_round_trips_explicit_and_system_preferences(self):
        org = "UmUTests"
        app_name = f"Unshuffle-{uuid.uuid4().hex}"
        settings = QSettings(org, app_name)
        settings.clear()
        try:
            class _Parent(QObject):
                def __init__(self):
                    super().__init__()
                    self.engine = None

            controller = SettingsController(settings, _Parent())

            controller.set_theme_key(ASH_THEME_KEY)
            self.assertEqual(controller.get_theme_key(), ASH_THEME_KEY)

            controller.set_theme_key(SYSTEM_THEME_KEY)
            self.assertEqual(controller.get_theme_key(), SYSTEM_THEME_KEY)

            controller.set_theme_key(OCEAN_THEME_KEY)
            self.assertEqual(controller.get_theme_key(), OCEAN_THEME_KEY)
        finally:
            settings.clear()


class ViewThemeMenuTests(unittest.TestCase):
    def test_view_menu_lists_all_supported_theme_choices(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance() or QApplication([])

        from gui.widgets.menu_bar import ModernMenuBar

        bar = ModernMenuBar()
        try:
            self.assertEqual(
                list(bar.theme_actions.keys()),
                [
                    DEFAULT_THEME_KEY,
                    ASH_THEME_KEY,
                    SUNSET_THEME_KEY,
                    OCEAN_THEME_KEY,
                    PEARL_THEME_KEY,
                ],
            )
        finally:
            bar.deleteLater()

    def test_visible_theme_switch_uses_scoped_stylesheets_for_speed(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication, QDialog

        app = QApplication.instance() or QApplication([])

        from gui.main.window import ModernApp

        window = ModernApp(defer_startup_restore=True)
        detached = QDialog()
        hidden = QDialog()

        class FakeApp:
            def __init__(self):
                self.global_styles = []

            def setStyleSheet(self, style):
                self.global_styles.append(style)

            def topLevelWidgets(self):
                return [window, detached, hidden]

        try:
            window.show()
            detached.show()
            app.processEvents()
            fake_app = FakeApp()

            window._apply_theme_stylesheet("QWidget { color: red; }", fake_app)

            self.assertEqual(fake_app.global_styles, [])
            self.assertIn("color: red", window.styleSheet())
            self.assertIn("color: red", detached.styleSheet())
            self.assertEqual(hidden.styleSheet(), "")
        finally:
            detached.deleteLater()
            hidden.deleteLater()
            window.deleteLater()

    def test_main_window_keeps_menu_bar_inside_qt_window(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance() or QApplication([])

        from gui.main.window import ModernApp

        window = ModernApp(defer_startup_restore=True)
        try:
            self.assertFalse(window.custom_menu_bar.isNativeMenuBar())
        finally:
            window.close()
            window.deleteLater()

    def test_view_menu_groups_library_views_and_preferences_without_rewiring_actions(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance() or QApplication([])

        from gui.widgets.menu_bar import ModernMenuBar

        bar = ModernMenuBar()
        try:
            top_level = [action.text() for action in bar.menu_view.actions() if action.text()]
            self.assertEqual(top_level[0], "Preferences")
            self.assertIn("Preferences", top_level)
            self.assertIn("Library Views", top_level)
            preference_actions = [action.text() for action in bar.menu_preferences.actions() if action.text()]
            self.assertLess(preference_actions.index("Theme"), preference_actions.index("Zoom"))

            library_view_actions = [action.text() for action in bar.menu_library_views.actions() if action.text()]
            self.assertIn("Switch View Mode (Table/Tree/Map)", library_view_actions)
            self.assertIn("Set Current View as Default", library_view_actions)
            self.assertIn("Docked Mode", library_view_actions)
            self.assertIn("Table", library_view_actions)
            self.assertIn("Tree", library_view_actions)
            self.assertIn("Map", library_view_actions)

            bar.set_docked_checked(True)
            visible_library_view_actions = [
                action.text()
                for action in bar.menu_library_views.actions()
                if action.text() and action.isVisible()
            ]
            self.assertIn("Switch View Mode (Table/Tree/Map)", visible_library_view_actions)
            self.assertIn("Docked Mode", visible_library_view_actions)
            self.assertNotIn("Set Current View as Default", visible_library_view_actions)
            self.assertFalse(bar.menu_view_table.menuAction().isVisible())
            self.assertFalse(bar.menu_view_tree.menuAction().isVisible())
            self.assertFalse(bar.menu_view_map.menuAction().isVisible())
            bar.set_docked_checked(False)

            preference_actions = [action.text() for action in bar.menu_preferences.actions() if action.text()]
            self.assertIn("Show Startup Launcher", preference_actions)
            self.assertNotIn("Minimize Startup Scans to Tray", preference_actions)
            self.assertIn("Show Non-Audio Assets", preference_actions)
            self.assertIn("Zoom", preference_actions)
            self.assertIn("Theme", preference_actions)

            toggle_requested = mock.Mock()
            docked_requested = mock.Mock()
            non_audio_requested = mock.Mock()
            theme_requested = mock.Mock()
            zoom_requested = mock.Mock()
            bar.toggleViewRequested.connect(toggle_requested)
            bar.toggleDockedRequested.connect(docked_requested)
            bar.showNonAudioAssetsRequested.connect(non_audio_requested)
            bar.themeRequested.connect(theme_requested)
            bar.zoomRequested.connect(zoom_requested)

            bar.act_toggle_view.trigger()
            bar.act_docked.trigger()
            bar.act_show_non_audio.trigger()
            bar.zoom_actions[110].trigger()
            bar.theme_actions[ASH_THEME_KEY].trigger()

            toggle_requested.assert_called_once_with()
            docked_requested.assert_called_once_with(True)
            non_audio_requested.assert_called_once_with(True)
            zoom_requested.assert_called_once_with(110)
            theme_requested.assert_called_once_with(ASH_THEME_KEY)
        finally:
            bar.deleteLater()
