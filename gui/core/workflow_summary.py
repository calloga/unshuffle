import os
from collections import Counter
from collections.abc import Iterable
from pathlib import Path

from ..utils.styles import ColorPalette


_HANDOVER_PROTECTED_NAMES = {
    ".unshuffle",
    "DO_NOT_DELETE_unshuffle",
}


def scan_summary_text(stats: dict) -> str:
    total = int(stats.get("total_scanned") or 0)
    added = int(stats.get("added_count") or 0)
    duplicates = int(stats.get("total_dupe_count") or 0)
    lines = [
        f"Scanned {total} file{'s' if total != 1 else ''}.",
        f"Added {added} new file{'s' if added != 1 else ''}.",
    ]
    if duplicates:
        lines.append(f"Skipped {duplicates} duplicate{'s' if duplicates != 1 else ''}.")
    return "\n".join(lines)


def scan_category_counts(records: Iterable) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for record in records or []:
        category = str(getattr(record, "category", "") or "Uncategorized").strip() or "Uncategorized"
        counts[category] += 1
    return dict(counts)


def chart_segments(stats: dict) -> list[tuple[str, int]]:
    category_counts = stats.get("category_counts") or {}
    if isinstance(category_counts, dict) and category_counts:
        ordered = sorted(
            ((str(label), int(count or 0)) for label, count in category_counts.items()),
            key=lambda item: item[1],
            reverse=True,
        )
        ordered = [(label, count) for label, count in ordered if count > 0]
        return ordered

    added = max(0, int(stats.get("added_count") or 0))
    library_duplicates = max(0, int(stats.get("lib_dupe_count") or 0))
    session_duplicates = max(0, int(stats.get("session_dupe_count") or 0))
    return [
        (label, value)
        for label, value in (
            ("Added", added),
            ("Library dupes", library_duplicates),
            ("Session dupes", session_duplicates),
        )
        if value > 0
    ]


def scan_summary_chart_pixmap(stats: dict):
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QColor, QFont, QPainter, QPixmap

    colors = [
        QColor("#d9953f"),
        QColor("#7aa2f7"),
        QColor("#9ece6a"),
        QColor("#e0af68"),
        QColor("#bb9af7"),
        QColor("#f7768e"),
        QColor("#2ac3de"),
        QColor("#c0caf5"),
    ]
    raw_segments = chart_segments(stats)
    if not raw_segments:
        raw_segments = [("Scanned", max(1, int(stats.get("total_scanned") or 1)))]
    total = max(1, sum(value for _label, value in raw_segments))
    segments = [
        (label, value, colors[index % len(colors)])
        for index, (label, value) in enumerate(raw_segments)
    ]

    pixmap = QPixmap(680, 176)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pie_rect = QRectF(18, 26, 124, 124)
    start_angle = 90 * 16
    for _label, value, color in segments:
        span_angle = -round((value / total) * 360 * 16)
        painter.setBrush(color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPie(pie_rect, start_angle, span_angle)
        start_angle += span_angle

    painter.setPen(QColor(ColorPalette.TEXT_MAIN))
    font = QFont()
    font.setPointSize(8)
    painter.setFont(font)
    for index, (label, value, color) in enumerate(segments):
        col_idx = index // 7
        row_idx = index % 7
        x_offset = 154 + col_idx * 250
        y_pos = 20 + row_idx * 22

        painter.setBrush(color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(x_offset, y_pos - 9, 12, 12, 2, 2)
        painter.setPen(QColor(ColorPalette.TEXT_MAIN))
        painter.drawText(x_offset + 20, y_pos + 1, f"{label}: {value}")
    painter.end()
    return pixmap


def show_scan_summary_dialog(parent, stats: dict) -> None:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout
    from gui.utils.styles import button_style, scaled_px

    dialog = QDialog(parent)
    dialog.setWindowTitle("Scan Complete")
    dialog.setModal(True)
    dialog.setMinimumWidth(scaled_px(720))
    dialog.setStyleSheet(
        f"""
        QDialog {{ background: {ColorPalette.BG_DARK}; color: {ColorPalette.TEXT_MAIN}; }}
        QLabel {{ color: {ColorPalette.TEXT_MAIN}; }}
        {button_style("primary", size="normal", min_width=72)}
        """
    )

    root = QVBoxLayout(dialog)
    root.setContentsMargins(scaled_px(20), scaled_px(20), scaled_px(20), scaled_px(20))
    root.setSpacing(scaled_px(12))

    chart = QLabel()
    chart_pixmap = scan_summary_chart_pixmap(stats)
    chart.setPixmap(chart_pixmap)
    chart.setFixedSize(chart_pixmap.size())
    root.addWidget(chart, 0, Qt.AlignmentFlag.AlignCenter)

    root.addSpacing(scaled_px(6))

    bottom_row = QHBoxLayout()
    summary_text = QLabel(scan_summary_text(stats).replace("\n", "   \u2022   "))
    summary_text.setStyleSheet(f"color: {ColorPalette.TEXT_MUTED}; font-weight: 500;")
    bottom_row.addWidget(summary_text, 1, Qt.AlignmentFlag.AlignVCenter)

    bottom_row.addStretch(1)

    ok = QPushButton("OK")
    ok.clicked.connect(dialog.accept)
    bottom_row.addWidget(ok, 0, Qt.AlignmentFlag.AlignVCenter)

    root.addLayout(bottom_row)
    dialog.exec()


def format_bytes(size: int) -> str:
    size = max(0, int(size or 0))
    units = ("B", "KB", "MB", "GB", "TB")
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} B"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{int(value)} B"


def remaining_source_footprint(paths: Iterable[str | Path]) -> tuple[int, int]:
    count = 0
    total_bytes = 0
    for root in paths or []:
        try:
            root_path = Path(root)
        except TypeError:
            continue
        if not root_path.exists():
            continue
        stack = [root_path]
        while stack:
            current = stack.pop()
            try:
                with os.scandir(current) as entries:
                    for entry in entries:
                        if entry.name in _HANDOVER_PROTECTED_NAMES:
                            continue
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                stack.append(Path(entry.path))
                            elif entry.is_file(follow_symlinks=False):
                                count += 1
                                total_bytes += entry.stat(follow_symlinks=False).st_size
                        except OSError:
                            continue
            except OSError:
                continue
    return count, total_bytes
