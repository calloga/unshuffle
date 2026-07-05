from unittest import mock

from gui.utils import platform_labels


def test_view_in_file_manager_label_uses_explorer_on_windows():
    with mock.patch.object(platform_labels.sys, "platform", "win32"):
        assert platform_labels.view_in_file_manager_label() == "View in Explorer"


def test_view_in_file_manager_label_uses_finder_on_macos():
    with mock.patch.object(platform_labels.sys, "platform", "darwin"):
        assert platform_labels.view_in_file_manager_label() == "View in Finder"
