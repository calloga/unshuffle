from PySide6.QtWidgets import QFrame, QHBoxLayout, QVBoxLayout, QLabel, QSlider, QWidget, QApplication
from PySide6.QtCore import Qt, QSize, Signal, QPoint, QMimeData, QUrl
from PySide6.QtGui import QIcon, QPixmap, QDrag
from pathlib import Path

from . import AnimatedIconButton, ElidingLabel
from ..utils.styles import (
    apply_style,
    preview_bar_style,
    scaled_px,
)
from ..utils.layout_helpers import apply_layout_margins, apply_layout_spacing
from ..utils.widget_helpers import apply_fixed_height, apply_fixed_size

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from gui.core.audio_player import SoundPreviewPlayer
from ..utils.constants import (
    DIFFERENT_ICON,
    DRAGOUT_ICON,
    NORM_ICON,
    PAUSE_ICON,
    PLAY_ICON,
    PREVIEW_BAR_HEIGHT,
    PREVIEW_BAR_MARGIN_H,
    PREVIEW_BAR_SPACING,
    PREVIEW_VOLUME_KNOB_SIZE,
    SIMILAR_ICON,
    STOP_ICON,
    VOLUME_ICON,
)


class DragOutIconButton(AnimatedIconButton):
    def __init__(self, player: "SoundPreviewPlayer", parent=None):
        super().__init__(DRAGOUT_ICON, QSize(18, 18), parent)
        self.player = player
        self._press_pos: QPoint | None = None
        self.setToolTip("Drag current sample out")

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._press_pos = event.position().toPoint()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if not (event.buttons() & Qt.LeftButton) or self._press_pos is None:
            return super().mouseMoveEvent(event)
        distance = (event.position().toPoint() - self._press_pos).manhattanLength()
        if distance < QApplication.startDragDistance():
            return super().mouseMoveEvent(event)
        if self.start_export_drag():
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._press_pos = None
        super().mouseReleaseEvent(event)

    def start_export_drag(self) -> bool:
        path = getattr(self.player, "current_path", None)
        if not path:
            return False
        source_path = Path(path)
        if not source_path.exists():
            return False

        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(source_path.absolute()))])
        drag = QDrag(self)
        drag.setMimeData(mime)

        if hasattr(drag, "setPixmap"):
            pixmap = QPixmap(1, 1)
            pixmap.fill(Qt.transparent)
            drag.setPixmap(pixmap)

        drag.exec(Qt.CopyAction | Qt.MoveAction, Qt.CopyAction)
        return True


class PreviewControlBar(QFrame):
    """
    Bottom playback bar for auditioning samples.
    
    Provides play/stop, volume, and a "Similarity Spectrum" slider.
    """
    TARGET_HEIGHT = PREVIEW_BAR_HEIGHT

    def __init__(self, parent=None):
        super().__init__(parent)
        apply_fixed_height(self, scaled_px(self.TARGET_HEIGHT))
        apply_style(self, preview_bar_style())
        
        layout = QHBoxLayout(self)
        apply_layout_margins(layout, (PREVIEW_BAR_MARGIN_H, 0, PREVIEW_BAR_MARGIN_H, 0))
        apply_layout_spacing(layout, PREVIEW_BAR_SPACING)

        from gui.core.audio_player import SoundPreviewPlayer
        self.player = SoundPreviewPlayer.instance()

        self.btn_play_pause = AnimatedIconButton(PLAY_ICON, QSize(18, 18))
        self.btn_play_pause.setToolTip("Play/Pause (Space)")
        self.btn_play_pause.clicked.connect(lambda checked=False: self.player.toggle_play_pause())

        self.btn_stop = AnimatedIconButton(STOP_ICON, QSize(16, 16))
        self.btn_stop.setToolTip("Stop (Esc)")
        self.btn_stop.clicked.connect(lambda checked=False: self.player.stop())

        self.btn_dragout = DragOutIconButton(self.player)

        self.btn_mute = AnimatedIconButton(VOLUME_ICON, QSize(18, 18))
        self.btn_mute.setToolTip("Mute/Unmute")

        layout.addWidget(self.btn_play_pause)
        layout.addWidget(self.btn_stop)
        layout.addWidget(self.btn_dragout)
        layout.addStretch()
        
        from . import ModernKnob
        self.vol_slider = ModernKnob()
        self.vol_slider.setSymmetric(False)
        apply_fixed_size(self.vol_slider, PREVIEW_VOLUME_KNOB_SIZE, PREVIEW_VOLUME_KNOB_SIZE)
        self.vol_slider.setRange(0, 100)
        self.vol_slider.setValue(80)
        self.vol_slider.valueChanged.connect(lambda v: self.player.set_volume(v / 100.0))
        
        layout.addWidget(self.vol_slider)
        layout.addWidget(self.btn_mute)

        self.player.stateChanged.connect(self._update_play_pause_icon)
        self.player.positionChanged.connect(self._update_time)
        self.player.durationChanged.connect(self._update_duration)
        self.player.volumeChanged.connect(lambda v: self.vol_slider.setValue(int(v * 100)))

    def _update_play_pause_icon(self, state):
        """Swap icons depending on playback state."""
        from PySide6.QtMultimedia import QMediaPlayer
        is_playing = state == QMediaPlayer.PlayingState or (hasattr(QMediaPlayer, "PlaybackState") and state == QMediaPlayer.PlaybackState.PlayingState)
        
        if is_playing:
            self.btn_play_pause.setToolTip("Pause (Space)")
            self.btn_play_pause.setIcon(QIcon(str(PAUSE_ICON)))
        else:
            self.btn_play_pause.setToolTip("Play (Space)")
            self.btn_play_pause.setIcon(QIcon(str(PLAY_ICON)))

    def _update_time(self, pos):
        """Update playback position tracking (no UI label)."""
        pass

    def _update_duration(self, dur):
        """Update duration tracking (no UI label)."""
        pass

    def _format_ms(self, ms: int) -> str:
        """Format milliseconds as M:SS."""
        s = ms // 1000
        m = s // 60
        s = s % 60
        return f"{m}:{s:02d}"

    def refresh_theme(self) -> None:
        is_collapsed = self.maximumHeight() == 0
        apply_style(self, preview_bar_style())
        apply_fixed_height(self, 0 if is_collapsed else scaled_px(self.TARGET_HEIGHT))
        self.setVisible(not is_collapsed)
        for button in (self.btn_play_pause, self.btn_stop, self.btn_dragout, self.btn_mute):
            if hasattr(button, "refresh_theme"):
                button.refresh_theme()
