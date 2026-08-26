import json

from PySide6.QtGui import QColor

from gui.core.dock_appearance import (
    DockAdaptivePalette,
    _is_blank_host_capture,
    _sample_image_colors,
    palette_from_colors,
    transfer_palette_color,
)
from PySide6.QtGui import QImage


def test_palette_from_colors_transfers_the_nearest_theme_onto_dominant_background():
    colors = [QColor("#202830")] * 20 + [QColor("#37c7b2")] * 3

    palette = palette_from_colors(colors)

    assert palette is not None
    assert palette.base == "#202830"
    assert palette.source_theme == "ash"
    assert palette.panel != palette.base
    assert palette.accent != "#37c7b2"
    assert QColor(palette.text).lightness() > QColor(palette.base).lightness()


def test_palette_from_colors_returns_none_without_opaque_samples():
    assert palette_from_colors([]) is None
    assert palette_from_colors([QColor(0, 0, 0, 0)]) is None


def test_cached_palette_parser_rejects_incomplete_values():
    palette = DockAdaptivePalette(
        base="#101010",
        darker="#080808",
        panel="#181818",
        raised="#202020",
        hover="#282828",
        border="#303030",
        accent="#40a0b0",
        accent_hover="#50b0c0",
        text="#ffffff",
        muted="#a0ffffff",
        selection="#305060",
        scrollbar="#121212",
        scrollbar_handle="#404040",
        source_theme="ash",
    )

    assert DockAdaptivePalette.from_json(json.dumps(palette.__dict__)) == palette
    assert DockAdaptivePalette.from_json('{"base":"#101010"}') is None
    assert DockAdaptivePalette.from_json("not-json") is None


def test_cached_palette_parser_upgrades_the_previous_six_color_format():
    palette = DockAdaptivePalette.from_json(json.dumps({
        "base": "#101010",
        "raised": "#202020",
        "border": "#303030",
        "accent": "#40a0b0",
        "text": "#ffffff",
        "muted": "#a0ffffff",
    }))

    assert palette is not None
    assert palette.source_theme == "legacy"
    assert palette.panel == "#202020"
    assert palette.selection == "#40a0b0"


def test_transfer_palette_color_preserves_alpha_and_moves_theme_color():
    palette = DockAdaptivePalette(
        base="#202830",
        darker="#182028",
        panel="#28323a",
        raised="#303c44",
        hover="#38464e",
        border="#46545c",
        accent="#4aa6b3",
        accent_hover="#58b4c1",
        text="#f4f6f8",
        muted="#a0f4f6f8",
        selection="#305e68",
        scrollbar="#202830",
        scrollbar_handle="#46545c",
        source_theme="ash",
    )
    source = QColor(80, 120, 160, 99)

    shifted = transfer_palette_color(source, palette)

    assert shifted != source
    assert shifted.alpha() == 99


def test_image_sampling_uses_pixels_across_the_captured_host_region():
    image = QImage(48, 192, QImage.Format_RGB32)
    image.fill(QColor("#24323c"))

    colors = _sample_image_colors(image)

    assert colors
    assert {color.name() for color in colors} == {"#24323c"}


def test_blank_gpu_host_capture_is_rejected_for_adjacent_sampling():
    assert _is_blank_host_capture([QColor("#000000")] * 100)
    assert not _is_blank_host_capture([QColor("#000000")] * 97 + [QColor("#ffffff")] * 3)
