from PySide6.QtCore import QSettings


def create_app_qsettings() -> QSettings:
    return QSettings("UmU", "Unshuffle")
