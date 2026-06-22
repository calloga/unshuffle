import logging
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaDevices, QMediaPlayer


class SoundPreviewPlayer(QObject):
    """
    Singleton audio player for previewing samples in the staging table.
    Uses QtMultimedia for low-latency playback.
    """

    _instance = None
    stateChanged = Signal(QMediaPlayer.PlaybackState)
    positionChanged = Signal(int)
    durationChanged = Signal(int)
    volumeChanged = Signal(float)
    errorOccurred = Signal(str)
    normalizationChanged = Signal(bool)
    finished = Signal()
    manuallyStopped = Signal()

    def __init__(self):
        if SoundPreviewPlayer._instance is not None:
            raise Exception("This class is a singleton!")
        super().__init__()
        self.player = QMediaPlayer()
        self.audio_output = QAudioOutput(QMediaDevices.defaultAudioOutput())
        self.player.setAudioOutput(self.audio_output)

        self.audio_output.setVolume(0.8)
        self.current_path = None
        self.is_normalized = False

        self.player.playbackStateChanged.connect(self.stateChanged.emit)
        self.player.positionChanged.connect(self.positionChanged.emit)
        self.player.durationChanged.connect(self.durationChanged.emit)
        self.player.mediaStatusChanged.connect(self._on_media_status_changed)
        self.player.errorOccurred.connect(self._handle_error)

    def _on_media_status_changed(self, status):
        if status == QMediaPlayer.EndOfMedia:
            self.finished.emit()

    def _handle_error(self, error, error_string):
        """Captures codec failures and system audio errors."""
        msg = f"Audio Error: {error_string}"
        if "codec" in error_string.lower() or "format" in error_string.lower():
            msg = "Codec Missing: Your system cannot play this audio format natively."
        logging.error(msg)
        self.errorOccurred.emit(msg)

    @classmethod
    def instance(cls):
        if cls._instance is None:
            cls._instance = SoundPreviewPlayer()
        return cls._instance

    def play(self, file_path: Optional[Path] = None):
        """Plays the audio file. If no path is provided, resumes current."""
        if file_path:
            if not file_path.exists():
                msg = f"Preview failed: file not found {file_path}"
                logging.error(msg)
                self.errorOccurred.emit(msg)
                return False

            if self.current_path != file_path:
                self.current_path = file_path
                self.player.setSource(QUrl.fromLocalFile(str(file_path)))
            else:
                self.player.setPosition(0)

        if not self.current_path:
            self.errorOccurred.emit("Preview failed: no audio file selected.")
            return False
        self.player.play()
        if self.current_path:
            logging.debug("Previewing: %s", self.current_path.name)
        return True

    def pause(self):
        self.player.pause()

    def stop(self):
        self.player.stop()
        self.manuallyStopped.emit()

    def release(self):
        self.player.stop()
        self.player.setSource(QUrl())
        self.current_path = None
        self.manuallyStopped.emit()

    def toggle_play_pause(self):
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
        else:
            if (
                self.player.mediaStatus() == QMediaPlayer.EndOfMedia
                or (self.player.duration() > 0 and self.player.position() >= self.player.duration())
            ):
                self.player.setPosition(0)
            self.player.play()

    def set_volume(self, volume_float: float):
        """0.0 to 1.0. If normalized is ON, we boost quiet signals gently."""
        final_vol = volume_float
        if self.is_normalized:
            import math

            final_vol = math.pow(volume_float, 0.7) * 1.2
            final_vol = min(1.0, final_vol)

        self.audio_output.setVolume(final_vol)
        self.volumeChanged.emit(volume_float)

    def set_normalization(self, enabled: bool):
        self.is_normalized = enabled
        self.normalizationChanged.emit(enabled)
        self.set_volume(self.audio_output.volume())

    def is_playing(self):
        return self.player.playbackState() == QMediaPlayer.PlayingState

    def get_state(self):
        return self.player.playbackState()
