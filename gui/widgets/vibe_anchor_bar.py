from PySide6.QtWidgets import QFrame, QVBoxLayout, QHBoxLayout, QLabel
from PySide6.QtCore import QSize, Signal
from ..utils.styles import apply_style, scaled_px, vibe_anchor_bar_style, vibe_anchor_label_style
from .buttons import AnimatedIconButton
from .sliders import ModernKnob
from ..utils.layout_helpers import apply_layout_margins, apply_layout_spacing
from ..utils.widget_helpers import apply_fixed_height, apply_fixed_size
from ..utils.constants import (
    CLOSE_ICON,
    DIFFERENT_ICON,
    SIMILAR_ICON,
    VIBE_ANCHOR_BAR_HEIGHT,
    VIBE_ANCHOR_CLOSE_ICON_SIZE,
    VIBE_ANCHOR_GAP_MEDIUM,
    VIBE_ANCHOR_GAP_SMALL,
    VIBE_ANCHOR_KNOB_SIZE,
    VIBE_ANCHOR_LAYOUT_MARGINS,
    VIBE_ANCHOR_LAYOUT_SPACING,
    VIBE_ANCHOR_SIM_ICON_SIZE,
)

class VibeAnchorBar(QFrame):
    """
    A specialized bar for controlling similarity search bias.
    """
    biasChanged = Signal(int)
    closeRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        apply_fixed_height(self, scaled_px(VIBE_ANCHOR_BAR_HEIGHT))
        apply_style(self, vibe_anchor_bar_style())
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        apply_layout_margins(layout, VIBE_ANCHOR_LAYOUT_MARGINS)
        apply_layout_spacing(layout, VIBE_ANCHOR_LAYOUT_SPACING)

        self.lbl_vibe_anchor = QLabel("Similarity Explorer")
        self.lbl_vibe_anchor.setVisible(False)

        self.btn_close = AnimatedIconButton(
            CLOSE_ICON,
            QSize(VIBE_ANCHOR_CLOSE_ICON_SIZE, VIBE_ANCHOR_CLOSE_ICON_SIZE),
        )
        self.btn_close.setToolTip("Close Similarity Explorer")
        self.btn_close.clicked.connect(self.closeRequested.emit)

        close_row = QHBoxLayout()
        close_row.addStretch(1)
        close_row.addWidget(self.btn_close)
        close_row.addStretch(1)
        layout.addLayout(close_row)

        self.slider = ModernKnob()
        apply_fixed_size(self.slider, VIBE_ANCHOR_KNOB_SIZE, VIBE_ANCHOR_KNOB_SIZE)
        self.slider.valueChanged.connect(self.biasChanged.emit)

        self.btn_sim = AnimatedIconButton(
            SIMILAR_ICON,
            QSize(VIBE_ANCHOR_SIM_ICON_SIZE, VIBE_ANCHOR_SIM_ICON_SIZE),
        )
        self.btn_sim.clicked.connect(lambda: self.slider.setValue(100))
        
        self.btn_diff = AnimatedIconButton(
            DIFFERENT_ICON,
            QSize(VIBE_ANCHOR_SIM_ICON_SIZE, VIBE_ANCHOR_SIM_ICON_SIZE),
        )
        self.btn_diff.clicked.connect(lambda: self.slider.setValue(-100))

        self.lbl_diff = QLabel("D")
        apply_style(self.lbl_diff, vibe_anchor_label_style())
        self.lbl_sim = QLabel("S")
        apply_style(self.lbl_sim, vibe_anchor_label_style())

        controls_row = QHBoxLayout()
        controls_row.addStretch(1)
        controls_row.addWidget(self.btn_diff)
        controls_row.addSpacing(VIBE_ANCHOR_GAP_SMALL)
        controls_row.addWidget(self.lbl_diff)
        controls_row.addSpacing(VIBE_ANCHOR_GAP_MEDIUM)
        controls_row.addWidget(self.slider)
        controls_row.addSpacing(VIBE_ANCHOR_GAP_MEDIUM)
        controls_row.addWidget(self.lbl_sim)
        controls_row.addSpacing(VIBE_ANCHOR_GAP_SMALL)
        controls_row.addWidget(self.btn_sim)
        controls_row.addStretch(1)
        layout.addLayout(controls_row)

    def set_value(self, value, *, emit_signal: bool = True):
        previous = self.slider.value()
        self.slider.blockSignals(True)
        self.slider.setValue(value)
        self.slider.blockSignals(False)
        if emit_signal and self.slider.value() != previous:
            self.biasChanged.emit(self.slider.value())

    def value(self):
        return self.slider.value()

    def set_anchor_text(self, text):
        self.lbl_vibe_anchor.setText(text)

    def anchor_text(self):
        return self.lbl_vibe_anchor.text()

    def refresh_theme(self) -> None:
        apply_style(self, vibe_anchor_bar_style())
        apply_fixed_height(self, scaled_px(VIBE_ANCHOR_BAR_HEIGHT))
        apply_style(self.lbl_diff, vibe_anchor_label_style())
        apply_style(self.lbl_sim, vibe_anchor_label_style())
        for button in (self.btn_close, self.btn_sim, self.btn_diff):
            if hasattr(button, "refresh_theme"):
                button.refresh_theme()
