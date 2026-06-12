from PySide6.QtWidgets import QLayout


def apply_layout_margins(layout: QLayout, margins: tuple[int, int, int, int]) -> None:
    left, top, right, bottom = margins
    layout.setContentsMargins(left, top, right, bottom)


def apply_layout_spacing(layout: QLayout, spacing: int) -> None:
    layout.setSpacing(spacing)
