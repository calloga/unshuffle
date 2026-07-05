import os

from PySide6.QtCore import QSettings


DEFAULT_QSETTINGS_ORG = "UmU"
DEFAULT_QSETTINGS_APP = "Unshuffle"
QSETTINGS_ORG_ENV = "UNSHUFFLE_QSETTINGS_ORG"
QSETTINGS_APP_ENV = "UNSHUFFLE_QSETTINGS_APP"


def app_qsettings_identity() -> tuple[str, str]:
    org = os.environ.get(QSETTINGS_ORG_ENV, DEFAULT_QSETTINGS_ORG)
    app = os.environ.get(QSETTINGS_APP_ENV, DEFAULT_QSETTINGS_APP)
    return org, app


def create_app_qsettings() -> QSettings:
    return QSettings(*app_qsettings_identity())
