from __future__ import annotations

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from unshuffle.core.constants import APP_NAME, APP_VERSION

from ..utils.app_icon import APP_ICON_PATH, apply_app_icon
from ..utils.layout_helpers import apply_layout_margins, apply_layout_spacing
from ..utils.styles import ColorPalette, scaled_px


def build_about_dialog(parent: QWidget | None = None) -> QDialog:
    dialog = QDialog(parent)
    dialog.setObjectName("AboutDialog")
    dialog.setWindowTitle(f"About {APP_NAME}")
    dialog.setModal(True)
    dialog.setMinimumSize(scaled_px(360), scaled_px(150))
    dialog.resize(scaled_px(380), scaled_px(158))
    apply_app_icon(dialog)

    root = QVBoxLayout(dialog)
    apply_layout_margins(root, (scaled_px(14), scaled_px(14), scaled_px(14), scaled_px(12)))
    apply_layout_spacing(root, scaled_px(12))

    card = QFrame()
    card.setObjectName("AboutCard")
    card_layout = QVBoxLayout(card)
    apply_layout_margins(card_layout, (scaled_px(16), scaled_px(14), scaled_px(16), scaled_px(14)))
    apply_layout_spacing(card_layout, 0)

    header = QWidget()
    header_layout = QHBoxLayout(header)
    apply_layout_margins(header_layout, (0, 0, 0, 0))
    apply_layout_spacing(header_layout, scaled_px(10))

    logo = QLabel()
    logo.setObjectName("AboutLogo")
    logo.setFixedSize(QSize(scaled_px(48), scaled_px(48)))
    logo.setAlignment(Qt.AlignCenter)
    if APP_ICON_PATH.exists():
        pixmap = QPixmap(str(APP_ICON_PATH))
        if not pixmap.isNull():
            logo.setPixmap(
                pixmap.scaled(
                    logo.size(),
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation,
                )
            )

    title = QLabel(APP_NAME)
    title.setObjectName("AboutTitle")
    subtitle = QLabel(f"V{APP_VERSION}")
    subtitle.setObjectName("AboutSubtitle")
    subtitle.setTextInteractionFlags(Qt.TextSelectableByMouse)

    header_layout.addWidget(logo, 0, Qt.AlignVCenter)
    header_layout.addWidget(title, 0, Qt.AlignVCenter)
    header_layout.addWidget(subtitle, 0, Qt.AlignVCenter)
    header_layout.addStretch(1)
    card_layout.addWidget(header)

    root.addWidget(card)

    buttons = QDialogButtonBox(QDialogButtonBox.Ok)
    buttons.accepted.connect(dialog.accept)
    root.addWidget(buttons)

    dialog.setStyleSheet(
        f"""
        QDialog#AboutDialog {{
            background: {ColorPalette.BG_DARK};
            color: {ColorPalette.TEXT_LIGHT};
        }}
        QFrame#AboutCard {{
            background: {ColorPalette.BG_LIST};
            border: none;
            border-radius: {scaled_px(8)}px;
        }}
        QLabel#AboutLogo {{
            background: transparent;
            border: none;
        }}
        QLabel#AboutTitle {{
            color: {ColorPalette.TEXT_HEADER};
            font-size: {scaled_px(21)}px;
            font-weight: 800;
        }}
        QLabel#AboutSubtitle {{
            color: {ColorPalette.TEXT_LIGHT};
            font-size: {scaled_px(13)}px;
            font-weight: 700;
            padding-top: {scaled_px(3)}px;
        }}
        QDialogButtonBox QPushButton {{
            background: {ColorPalette.PRIMARY};
            color: {ColorPalette.TEXT_INVERSE};
            border: none;
            border-radius: {scaled_px(4)}px;
            padding: 0 {scaled_px(14)}px;
            min-height: {scaled_px(32)}px;
            min-width: {scaled_px(76)}px;
            font-weight: 700;
        }}
        QDialogButtonBox QPushButton:hover {{ background: {ColorPalette.PRIMARY_HOVER}; }}
        """
    )
    return dialog


def show_about(parent: QWidget | None = None) -> None:
    dialog = build_about_dialog(parent)
    dialog.exec()
