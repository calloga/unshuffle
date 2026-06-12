from PySide6.QtCore import QSize
from PySide6.QtWidgets import QWidget


def apply_fixed_size(widget: QWidget, width: int, height: int) -> None:
    widget.setFixedSize(width, height)


def apply_fixed_height(widget: QWidget, height: int) -> None:
    widget.setFixedHeight(height)


def apply_fixed_width(widget: QWidget, width: int) -> None:
    widget.setFixedWidth(width)


def apply_minimum_width(widget: QWidget, width: int) -> None:
    widget.setMinimumWidth(width)


def apply_fixed_size_q(widget: QWidget, size: QSize) -> None:
    widget.setFixedSize(size)


def apply_minimum_height(widget: QWidget, height: int) -> None:
    widget.setMinimumHeight(height)
