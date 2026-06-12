from __future__ import annotations

from PySide6.QtCore import Qt, QRect
from PySide6.QtGui import QColor, QPainter

from gui.styles import CATEGORY_IDENTITY_MAP
from unshuffle.core.constants import SUB_TAXONOMY_MAP
from gui.utils.styles import (
    ColorPalette,
    identity_lane_color,
    make_qcolor,
    scaled_px,
)


def _lane_for_category(category: str) -> int | None:
    role = CATEGORY_IDENTITY_MAP.get(category, "identity.neutral")
    if role == "identity.neutral":
        return None
    try:
        return max(0, int(role.rsplit(".", 1)[1]) - 1)
    except (IndexError, ValueError):
        return None


def _sub_values_for_category(category: str) -> list[str]:
    return sorted(
        {
            sub
            for sub in SUB_TAXONOMY_MAP.get((category or ""), {}).values()
            if sub and sub != "no-sub"
        }
    )


def _type_fill(audio_type: str) -> QColor:
    normalized = (audio_type or "").strip().lower()
    if normalized == "loops":
        return make_qcolor(ColorPalette.PRIMARY)
    if normalized == "oneshots":
        return make_qcolor(ColorPalette.BG_LIGHT)
    return make_qcolor(ColorPalette.IDENTITY_SOFT_NEUTRAL)


def _type_pen(audio_type: str) -> QColor:
    normalized = (audio_type or "").strip().lower()
    if normalized == "loops":
        return make_qcolor(ColorPalette.TEXT_INVERSE)
    if normalized == "oneshots":
        return make_qcolor(ColorPalette.TEXT_LIGHT)
    return make_qcolor(ColorPalette.TEXT_MAIN)


def _category_fill(category: str, *, soft_variant: bool = False) -> QColor:
    lane = _lane_for_category(category)
    if lane is None:
        fill = make_qcolor(ColorPalette.IDENTITY_SOFT_NEUTRAL)
    else:
        fill = make_qcolor(identity_lane_color(lane, soft=True))
    if make_qcolor(ColorPalette.BG_LIST).lightness() < 120:
        if lane is None:
            fill = make_qcolor(ColorPalette.IDENTITY_NEUTRAL)
        else:
            fill = make_qcolor(ColorPalette.IDENTITY[lane % len(ColorPalette.IDENTITY)])
        fill.setAlpha(48 if soft_variant else 122)
    elif soft_variant:
        fill.setAlpha(86)
    return fill


def _taxonomy_parts(audio_type: str, category: str, subcategory: str, *, muted: bool = False) -> list[tuple[str, QColor, QColor]]:
    if muted:
        fill = make_qcolor(ColorPalette.IDENTITY_SOFT_NEUTRAL)
        pen = make_qcolor(ColorPalette.TEXT_DIM)
        return [(label, fill, pen) for label in _taxonomy_labels(audio_type, category, subcategory)]
    return _colored_taxonomy_parts(audio_type, category, subcategory)


def _taxonomy_labels(audio_type: str, category: str, subcategory: str) -> list[str]:
    labels: list[str] = []
    compact_type = _compact_audio_type(audio_type)
    if compact_type:
        labels.append(compact_type)
    category = _clean_taxonomy_part(category)
    subcategory = _clean_taxonomy_part(subcategory)
    if category:
        labels.append(category)
    if subcategory:
        labels.append(subcategory)
    return labels


def _clean_taxonomy_part(value: str) -> str:
    text = (value or "").strip()
    if text == "no-sub":
        return ""
    return text


def _colored_taxonomy_parts(audio_type: str, category: str, subcategory: str) -> list[tuple[str, QColor, QColor]]:
    parts: list[tuple[str, QColor, QColor]] = []
    compact_type = _compact_audio_type(audio_type)
    category = _clean_taxonomy_part(category)
    subcategory = _clean_taxonomy_part(subcategory)
    if compact_type:
        parts.append((compact_type, _type_fill(audio_type), _type_pen(audio_type)))
    if category:
        parts.append((category, _category_fill(category), make_qcolor(ColorPalette.TEXT_MAIN)))
    if subcategory:
        parts.append((subcategory, _category_fill(category, soft_variant=True), make_qcolor(ColorPalette.TEXT_MUTED)))
    return parts


def _taxonomy_pills_width(metrics, audio_type: str, category: str, subcategory: str, *, muted: bool = False) -> int:
    parts = _taxonomy_parts(audio_type, category, subcategory, muted=muted)
    if not parts:
        return scaled_px(40)
    gap = scaled_px(4)
    padding = scaled_px(14)
    slash_width = metrics.horizontalAdvance("/") + scaled_px(2)
    return (
        sum(metrics.horizontalAdvance(label) + padding for label, _fill, _pen in parts)
        + gap * max(0, len(parts) - 1)
        + slash_width * max(0, len(parts) - 1)
    )


def _pill_widths_for_available(metrics, labels: list[str], available_width: int) -> list[int]:
    gap = scaled_px(4)
    slash_width = metrics.horizontalAdvance("/") + scaled_px(2)
    min_pill_width = max(22, scaled_px(22))
    widths: list[int] = []
    remaining = available_width
    for index, label in enumerate(labels):
        ideal_width = metrics.horizontalAdvance(label) + scaled_px(14)
        future_slots = max(0, len(labels) - index - 1)
        separator_space = (gap + slash_width) * future_slots
        reserved_future_width = min_pill_width * future_slots
        max_width = max(min_pill_width, remaining - separator_space - reserved_future_width)
        width = min(ideal_width, max_width)
        widths.append(width)
        remaining -= width
        if index < len(labels) - 1 and remaining > 0:
            remaining -= slash_width + gap
        if remaining <= 0:
            break
    return widths


def _paint_taxonomy_pills(
    painter: QPainter,
    rect: QRect,
    metrics,
    audio_type: str,
    category: str,
    subcategory: str,
    *,
    muted: bool = False,
) -> None:
    parts = _taxonomy_parts(audio_type, category, subcategory, muted=muted)
    if not parts:
        return
    gap = scaled_px(4)
    pill_height = min(scaled_px(20), max(1, rect.height() - scaled_px(6)))
    left_padding = scaled_px(8)
    available_width = max(1, rect.width() - left_padding - scaled_px(6))
    left = rect.left() + left_padding
    top = rect.top() + max(0, (rect.height() - pill_height) // 2)
    remaining = available_width

    painter.save()
    painter.setRenderHint(QPainter.Antialiasing)
    font = painter.font()
    font.setBold(False)
    painter.setFont(font)
    painter.setPen(Qt.NoPen)
    slash_width = metrics.horizontalAdvance("/") + scaled_px(2)
    widths = _pill_widths_for_available(metrics, [label for label, _fill, _pen in parts], available_width)
    for index, ((label, fill, pen), width) in enumerate(zip(parts, widths)):
        pill = QRect(left, top, width, pill_height)
        painter.setBrush(fill)
        painter.drawRoundedRect(pill, scaled_px(3), scaled_px(3))
        painter.setPen(pen)
        painter.drawText(
            pill.adjusted(scaled_px(7), 0, -scaled_px(7), 0),
            Qt.AlignCenter,
            metrics.elidedText(label, Qt.ElideRight, max(1, width - scaled_px(10))),
        )
        left += width
        remaining -= width
        if index < len(parts) - 1 and remaining > 0:
            painter.setPen(make_qcolor(ColorPalette.TEXT_MUTED))
            slash_rect = QRect(left, top, slash_width, pill_height)
            painter.drawText(slash_rect, Qt.AlignCenter, "/")
            left += slash_width + gap
            remaining -= slash_width + gap
        painter.setPen(Qt.NoPen)
        if remaining <= 0:
            break
    painter.restore()


def _compact_audio_type(audio_type: str) -> str:
    normalized = (audio_type or "").strip().lower()
    if normalized == "loops":
        return "L"
    if normalized == "oneshots":
        return "O"
    return (audio_type or "").strip()
