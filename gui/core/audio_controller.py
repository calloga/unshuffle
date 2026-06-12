import logging
from pathlib import Path
from PySide6.QtCore import QObject, Signal, QTimer
from gui.core.audio_player import SoundPreviewPlayer

class AudioController(QObject):
    """
    Manages audio playback, transport bar animations, and similarity search orchestration.
    """
    ANIMATION_DURATION = 250
    AUTO_COLLAPSE_DELAY_MS = 2000

    statusRequested = Signal(str)
    similaritySearchRequested = Signal(str) 
    tabSwitchRequested = Signal(int)

    def _resolve_play_path(self, target, model, proxy_model):
        if hasattr(target, "column") and hasattr(target, "row"):
            if target is None or not target.isValid():
                return None
            source_index = proxy_model.mapToSource(target)
            if not source_index.isValid():
                return None
            row = source_index.row()
            records = getattr(model, "records", [])
            if row < 0 or row >= len(records):
                return None
            return records[row].source_path
        if hasattr(target, "source_path"):
            return target.source_path
        if isinstance(target, (str, Path)):
            return Path(target)
        return None

    def handle_play_request(self, target, model, proxy_model):
        """Resolves target to a path and plays it."""
        resolved_path = self._resolve_play_path(target, model, proxy_model)
        if resolved_path is not None:
            self.play_path(resolved_path)

    def __init__(self, audio_bar, parent=None):
        super().__init__(parent)
        self.audio_bar = audio_bar 
        from PySide6.QtCore import QPropertyAnimation, QEasingCurve
        self.anim = QPropertyAnimation(self.audio_bar, b"minimumHeight")
        self.anim2 = QPropertyAnimation(self.audio_bar, b"maximumHeight")
        self.anim.setEasingCurve(QEasingCurve.OutQuad) 
        self.anim2.setEasingCurve(QEasingCurve.OutQuad)
        
        self.player = SoundPreviewPlayer.instance()
        self._collapse_timer = QTimer(self)
        self._collapse_timer.setSingleShot(True)
        self._collapse_timer.timeout.connect(lambda: self.toggle_audio_bar(False, immediate=True))
        self.player.errorOccurred.connect(self._handle_audio_error)
        self.player.finished.connect(self._schedule_audio_bar_collapse)
        self.player.manuallyStopped.connect(self._collapse_for_docked_view)
        self.anim.finished.connect(self._on_animation_finished)

    def _is_docked_view(self) -> bool:
        app = self.parent()
        return bool(
            app is not None
            and getattr(app, "stack", None) is not None
            and getattr(app, "dock_view", None) is not None
            and app.stack.currentWidget() is app.dock_view
        )

    def play_path(self, file_path):
        self._collapse_timer.stop()
        if self.player.is_playing() and self.player.current_path == file_path:
            self.player.stop()
            self._collapse_for_docked_view()
            return
        else:
            if not self.player.play(file_path):
                return

        self.toggle_audio_bar(True)

    def play_record(self, record):
        self.play_path(record.source_path)

    def handle_tab_change(self, index):
        """Always keep bar visible once session is active."""
        self.toggle_audio_bar(True)

    def _schedule_audio_bar_collapse(self) -> None:
        if self._is_docked_view():
            self._collapse_timer.start(self.AUTO_COLLAPSE_DELAY_MS)

    def _collapse_for_docked_view(self) -> None:
        if self._is_docked_view():
            self.toggle_audio_bar(False, immediate=True)

    def toggle_audio_bar(self, expand: bool, *, immediate: bool = False):
        """Smoothly animates the transport bar height."""

        if expand:
            self._collapse_timer.stop()
        elif immediate:
            self._collapse_timer.stop()
        target = self.audio_bar.TARGET_HEIGHT if expand else 0
        if expand:
            self.audio_bar.setVisible(True)
        if self.audio_bar.height() == target:
            if not expand:
                self.audio_bar.setVisible(False)
            return
        for attr in ("anim", "anim2"):
            old = getattr(self, attr, None)
            if old is not None:
                old.stop()


        self.anim.setDuration(self.ANIMATION_DURATION)
        self.anim.setStartValue(self.audio_bar.height())
        self.anim.setEndValue(target)
        
        self.anim2.setDuration(self.ANIMATION_DURATION)
        self.anim2.setStartValue(self.audio_bar.height())
        self.anim2.setEndValue(target)

        self.anim.start()
        self.anim2.start()

    def _on_animation_finished(self):
        if self.audio_bar.maximumHeight() == 0:
            self.audio_bar.setVisible(False)

    def _handle_audio_error(self, message):
        self.statusRequested.emit(f"Warning: {message}")
