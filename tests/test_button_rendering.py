"""Run Qt scaling checks in fresh processes, before QApplication reads its environment."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest


PROBE = r'''
import sys
from pathlib import Path
from types import SimpleNamespace
from PySide6.QtCore import QPoint, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPixmap, QRegion
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QApplication, QWidget
from gui.widgets.buttons import AnimatedIconButton, SidebarIconButton
from gui.widgets.preview_control_bar import PreviewControlBar
from gui.utils.constants import PLAY_ICON

app = QApplication([])
ratio = float(sys.argv[1])
button = AnimatedIconButton(PLAY_ICON, QSize(18, 18))
initial_geometry = button.size()
bar = SimpleNamespace(btn_play_pause=button)
states = [None, QMediaPlayer.PlayingState, QMediaPlayer.PausedState,
          QMediaPlayer.StoppedState, QMediaPlayer.PlayingState]
for state in states:
    if state is not None:
        PreviewControlBar._update_play_pause_icon(bar, state)
    pixmap = button._get_cached_icon()
    assert pixmap is not None and not pixmap.isNull()
    assert pixmap.devicePixelRatio() == ratio
    assert pixmap.deviceIndependentSize().width() == 18
    assert pixmap.deviceIndependentSize().height() == 18
    assert button.size() == initial_geometry

# A second screen must not reuse a pixmap rendered for the previous ratio.
icon = button.icon()
other = AnimatedIconButton(PLAY_ICON, QSize(18, 18))
other.setIcon(icon)
other.devicePixelRatioF = lambda: 1.25
other_pixmap = other._get_cached_icon()
assert other_pixmap.devicePixelRatio() == 1.25
assert button._get_cached_icon().devicePixelRatio() == ratio
button.devicePixelRatioF = lambda: 1.25
assert button._get_cached_icon().cacheKey() == other_pixmap.cacheKey()
button.devicePixelRatioF = lambda: ratio

# Color changes must select a new cache entry without changing resolution.
button.set_color_transform(lambda _: QColor('#ff0000'))
red = button._get_cached_icon()
button.set_color_transform(lambda _: QColor('#00ff00'))
button.refresh_theme()
green = button._get_cached_icon()
assert red.cacheKey() != green.cacheKey()
for pixmap, expected in [(red, '#ff0000'), (green, '#00ff00')]:
    image = pixmap.toImage()
    colors = [image.pixelColor(x, y) for y in range(image.height()) for x in range(image.width())]
    assert any(c.alpha() > 200 and c.name() == expected for c in colors)
    assert pixmap.deviceIndependentSize().width() == 18

# Use a solid rectangular asset to check actual paint bounds and aspect ratio.
source = QPixmap(40, 20)
source.fill(Qt.white)
path = Path(sys.argv[2]) / 'wide.png'
assert source.save(str(path))
for consumer in [AnimatedIconButton, SidebarIconButton]:
    control = consumer(path, QSize(18, 18))
    control.color = QColor('#ff0000')
    if isinstance(control, SidebarIconButton):
        control.set_adaptive_colors(None, None, unchecked_icon='#ff0000')
    control.setAttribute(Qt.WA_TranslucentBackground)
    for dynamic in [False, True]:
        if dynamic:
            control.setIcon(QIcon(str(path)))
        pixmap = control._get_cached_icon()
        size = pixmap.deviceIndependentSize()
        assert abs(size.width() - 18) <= 1 / ratio
        assert abs(size.height() - 9) <= 1 / ratio
        output = QPixmap(control.size() * ratio)
        output.setDevicePixelRatio(ratio)
        output.fill(Qt.transparent)
        control.render(output, QPoint(), QRegion(), QWidget.DrawChildren)
        image = output.toImage()
        points = [(x, y) for y in range(image.height()) for x in range(image.width())
                  if image.pixelColor(x, y).alpha() > 200]
        assert points
        left, right = min(x for x, y in points), max(x for x, y in points)
        top, bottom = min(y for x, y in points), max(y for x, y in points)
        assert abs((left + right + 1) / 2 - image.width() / 2) <= 1
        assert abs((top + bottom + 1) / 2 - image.height() / 2) <= 1
        assert abs((right - left + 1) / ratio - 18) <= 2 / ratio
        assert abs((bottom - top + 1) / ratio - 9) <= 2 / ratio
print('rendering checks passed')
'''


@pytest.mark.parametrize("scale", ["1", "1.5", "2"])
def test_playback_and_shared_buttons_preserve_logical_size(scale, tmp_path):
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(PROBE), scale, str(tmp_path)],
        cwd=Path(__file__).resolve().parents[1],
        env=dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_SCALE_FACTOR=scale),
        capture_output=True,
        text=True,
        timeout=45,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "rendering checks passed" in result.stdout
