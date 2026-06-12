from PySide6.QtWidgets import QWidget


def apply_style(widget: QWidget, style: str) -> None:
    widget.setStyleSheet(style)
