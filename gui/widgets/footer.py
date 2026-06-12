from datetime import datetime
from PySide6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel, 
    QPushButton, QProgressBar, QTextEdit, QSizePolicy
)
from PySide6.QtCore import Qt, Signal, QPropertyAnimation, QEasingCurve, QTimer
from ..utils.styles import apply_style, footer_base_style, footer_cta_button_style, footer_draft_label_style, scaled_px
from ..utils.layout_helpers import apply_layout_margins
from ..utils.widget_helpers import apply_fixed_height, apply_fixed_width
from ..utils.constants import (
    FOOTER_CANCEL_BUTTON_WIDTH,
    FOOTER_COLLAPSED_HEIGHT,
    FOOTER_EXPANDED_HEIGHT,
    FOOTER_MARGIN_BOTTOM,
    FOOTER_MARGIN_H,
)
from .labels import ElidingLabel

FOOTER_NOTIFICATION_TIMEOUT_MS = 30000


class ModernFooter(QFrame):
    """
    Application footer containing logs, status, and progress information.
    """
    discardRequested = Signal()
    saveRequested = Signal()
    cancelRequested = Signal()
    reviewCoherenceRequested = Signal()
    buildRequested = Signal()
    viewSummaryRequested = Signal()
    openBuildTargetRequested = Signal()
    openBuildSourceRequested = Signal()
    undoBuildRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ModernFooter")
        self._coherence_review_suppressed = False
        self._docked_presentation = False
        self._docked_suppressed_visibility = {}
        self._status_text = "Ready"
        self._busy = False
        self._draft_visible = False
        self._tagging_visible = False
        self._coherence_label_visible = False
        self._build_handover_label_visible = False
        self._notification_widgets = {}
        self._notification_desired = {}
        self._notification_remaining = {}
        self._notification_timers = {}
        self._timed_notifications = set()
        apply_fixed_height(self, scaled_px(FOOTER_COLLAPSED_HEIGHT))
        apply_style(self, footer_base_style())
        self._setup_ui()
        self._setup_notification_state()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        apply_layout_margins(layout, (FOOTER_MARGIN_H, 4, FOOTER_MARGIN_H, 4))
        
        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setVisible(False)
        layout.addWidget(self.log_output)
        
        status_row = QHBoxLayout()
        self._status_row = status_row
        apply_layout_margins(status_row, (0, 0, 0, 0))
        status_row.setSpacing(10)

        status_group = QHBoxLayout()
        self._status_group = status_group
        apply_layout_margins(status_group, (0, 0, 0, 0))
        status_group.setSpacing(8)

        notification_group = QHBoxLayout()
        self._notification_group = notification_group
        apply_layout_margins(notification_group, (0, 0, 0, 0))
        notification_group.setSpacing(6)

        process_group = QHBoxLayout()
        self._process_group = process_group
        apply_layout_margins(process_group, (0, 0, 0, 0))
        process_group.setSpacing(6)

        self.lbl_status = ElidingLabel("Ready")
        self.lbl_status.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        status_group.addWidget(self.lbl_status, 1)

        self.lbl_reorg_draft = ElidingLabel("")
        apply_style(self.lbl_reorg_draft, footer_draft_label_style())
        self.lbl_reorg_draft.setVisible(False)
        self.lbl_reorg_draft.setMaximumWidth(scaled_px(180))
        self.lbl_tagging_status = ElidingLabel("")
        apply_style(self.lbl_tagging_status, footer_draft_label_style())
        self.lbl_tagging_status.setVisible(False)
        self.lbl_tagging_status.setMaximumWidth(scaled_px(210))
        self.lbl_coherence_status = ElidingLabel("")
        apply_style(self.lbl_coherence_status, footer_draft_label_style())
        self.lbl_coherence_status.setVisible(False)
        self.lbl_coherence_status.setMaximumWidth(scaled_px(230))

        self.btn_review_coherence = QPushButton("Review Outliers")
        self.btn_review_coherence.setProperty("role", "primary")
        apply_style(self.btn_review_coherence, footer_cta_button_style())
        self.btn_review_coherence.setToolTip("Review suggested library cleanup decisions")
        self.btn_review_coherence.setVisible(False)
        self.btn_review_coherence.clicked.connect(self.reviewCoherenceRequested.emit)

        self.btn_build = QPushButton("Build")
        self.btn_build.setProperty("role", "primary")
        apply_style(self.btn_build, footer_cta_button_style())
        self.btn_build.setToolTip("Build the organized library")
        self.btn_build.setVisible(False)
        self.btn_build.clicked.connect(self._on_build_clicked)

        self.btn_view_summary = QPushButton("Scan Summary")
        self.btn_view_summary.setProperty("role", "secondary")
        apply_style(self.btn_view_summary, footer_cta_button_style())
        self.btn_view_summary.setToolTip("View detailed statistics for the last scan")
        self.btn_view_summary.setVisible(False)
        self.btn_view_summary.clicked.connect(self._on_view_summary_clicked)

        self.lbl_build_handover = ElidingLabel("")
        apply_style(self.lbl_build_handover, footer_draft_label_style())
        self.lbl_build_handover.setVisible(False)
        self.lbl_build_handover.setMaximumWidth(scaled_px(360))

        self.btn_open_build_target = QPushButton("Open Target")
        self.btn_open_build_target.setProperty("role", "secondary")
        apply_style(self.btn_open_build_target, footer_cta_button_style("secondary"))
        self.btn_open_build_target.setToolTip("Open the built library in Explorer")
        self.btn_open_build_target.setVisible(False)
        self.btn_open_build_target.clicked.connect(self.openBuildTargetRequested.emit)

        self.btn_open_build_source = QPushButton("Open Source")
        self.btn_open_build_source.setProperty("role", "secondary")
        apply_style(self.btn_open_build_source, footer_cta_button_style("secondary"))
        self.btn_open_build_source.setToolTip("Open the source library in Explorer")
        self.btn_open_build_source.setVisible(False)
        self.btn_open_build_source.clicked.connect(self.openBuildSourceRequested.emit)

        self.btn_undo_build = QPushButton("Undo Move")
        self.btn_undo_build.setProperty("role", "secondary")
        apply_style(self.btn_undo_build, footer_cta_button_style("secondary"))
        self.btn_undo_build.setToolTip("Undo this move build")
        self.btn_undo_build.setVisible(False)
        self.btn_undo_build.clicked.connect(self.undoBuildRequested.emit)

        self.btn_reorg_discard = QPushButton("Discard")
        self.btn_reorg_discard.setObjectName("danger")
        apply_style(self.btn_reorg_discard, footer_cta_button_style("danger"))
        self.btn_reorg_discard.setVisible(False)
        self.btn_reorg_discard.clicked.connect(self.discardRequested.emit)
        
        self.btn_reorg_save = QPushButton("Save Changes")
        self.btn_reorg_save.setObjectName("primary")
        apply_style(self.btn_reorg_save, footer_cta_button_style())
        self.btn_reorg_save.setVisible(False)
        self.btn_reorg_save.clicked.connect(self.saveRequested.emit)
        
        self.lbl_count = QLabel("0 files ready")

        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setObjectName("danger")
        apply_fixed_width(self.btn_cancel, FOOTER_CANCEL_BUTTON_WIDTH)
        apply_style(self.btn_cancel, footer_cta_button_style("danger"))
        self.btn_cancel.setVisible(False)
        self.btn_cancel.clicked.connect(self._on_cancel_clicked)

        status_group.addWidget(self.lbl_reorg_draft)
        status_group.addWidget(self.lbl_tagging_status)
        status_group.addWidget(self.lbl_coherence_status)
        status_group.addWidget(self.lbl_build_handover)
        notification_group.addWidget(self.btn_review_coherence)
        notification_group.addWidget(self.btn_build)
        notification_group.addWidget(self.btn_view_summary)
        notification_group.addWidget(self.btn_open_build_target)
        notification_group.addWidget(self.btn_open_build_source)
        notification_group.addWidget(self.btn_undo_build)
        process_group.addWidget(self.btn_reorg_discard)
        process_group.addWidget(self.btn_reorg_save)
        process_group.addWidget(self.btn_cancel)
        status_row.addLayout(status_group, 1)
        status_row.addLayout(notification_group, 0)
        status_row.addWidget(self.lbl_count, 0)
        status_row.addLayout(process_group, 0)
        layout.addLayout(status_row)
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        
        h_progress = QHBoxLayout()
        self._progress_row = h_progress
        apply_layout_margins(h_progress, (0, 0, 0, 0))
        h_progress.addWidget(self.progress_bar, 1)
        layout.addLayout(h_progress)

    def set_status(self, text):
        self._status_text = str(text or "Ready")
        self.lbl_status.setText("Ready" if self._docked_presentation else self._status_text)

    def log(self, text, html=True):
        if html:
            self.log_output.append(text)
        else:
            self.log_output.append(f"[{datetime.now().strftime('%H:%M:%S')}] {text}")

    def set_progress(self, current, total):
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(current)

    def set_count(self, text):
        self.lbl_count.setText(text)

    def set_reorg_draft_state(self, text, visible, can_save=False):
        self._draft_visible = bool(visible)
        self.lbl_reorg_draft.setText(text)
        self.btn_reorg_save.setEnabled(can_save)
        self._refresh_footer_presentation()

    def set_tagging_state(self, text, visible, can_filter=False):
        self._tagging_visible = bool(visible)
        self.lbl_tagging_status.setText(text)
        self._refresh_footer_presentation()

    def set_coherence_state(self, text, visible, can_review=False, can_build=False):
        self.lbl_coherence_status.setText(text)
        self._coherence_label_visible = bool(visible and not can_review and not can_build)
        self._set_notification_visible(
            "review",
            bool(visible and can_review and not self._coherence_review_suppressed),
        )
        show_build = bool(visible and can_build and not self._build_handover_label_visible)
        self._set_notification_visible("build", show_build, timed=True)
        self._refresh_footer_presentation()

    def set_coherence_review_suppressed(self, suppressed: bool) -> None:
        self._coherence_review_suppressed = suppressed
        if self._coherence_review_suppressed:
            self._set_notification_visible("review", False)
        else:
            self._refresh_footer_presentation()

    def set_build_handover_state(
        self,
        text,
        visible,
        *,
        can_open_target=False,
        can_open_source=False,
        can_undo=False,
        target_tooltip="",
        source_tooltip="",
    ):
        self.lbl_build_handover.setText(text)
        self.lbl_build_handover.setToolTip(text)
        self._build_handover_label_visible = bool(visible and text)
        if visible:
            self._set_notification_visible("build", False, timed=True)
        self._set_notification_visible("open_target", bool(visible and can_open_target))
        self._set_notification_visible("open_source", bool(visible and can_open_source))
        self._set_notification_visible("undo_build", bool(visible and can_undo))
        if target_tooltip:
            self.btn_open_build_target.setToolTip(target_tooltip)
        if source_tooltip:
            self.btn_open_build_source.setToolTip(source_tooltip)
        self._refresh_footer_presentation()

    def clear_build_handover_state(self):
        self.set_build_handover_state("", False)

    def set_busy_state(self, busy):
        busy = bool(busy)
        self._busy = busy
        self.btn_reorg_discard.setEnabled(not busy)
        self.btn_reorg_save.setEnabled(not busy)
        self.progress_bar.setVisible(busy)
        self.btn_cancel.setEnabled(busy)
        self.btn_cancel.setText("Cancel")
        self._refresh_footer_presentation()
        self.toggle_footer(busy)

    def set_docked_presentation(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if self._docked_presentation == enabled:
            self._apply_docked_presentation()
            return
        if not enabled:
            self._docked_presentation = False
            self._docked_suppressed_visibility.clear()
            self.lbl_status.setText(self._status_text)
            self._refresh_footer_presentation()
            self.toggle_footer(self._busy)
            return
        self._docked_presentation = True
        self._refresh_footer_presentation()
        self.toggle_footer(False)

    def _docked_suppressed_widgets(self):
        return (
            self.lbl_reorg_draft,
            self.lbl_tagging_status,
            self.lbl_coherence_status,
            self.btn_review_coherence,
            self.btn_build,
            self.btn_view_summary,
            self.lbl_build_handover,
            self.btn_open_build_target,
            self.btn_open_build_source,
            self.btn_undo_build,
            self.btn_reorg_discard,
            self.btn_reorg_save,
            self.btn_cancel,
            self.progress_bar,
            self.log_output,
        )

    def _apply_docked_presentation(self) -> None:
        self._refresh_footer_presentation()

    def _setup_notification_state(self) -> None:
        self._notification_widgets = {
            "review": self.btn_review_coherence,
            "build": self.btn_build,
            "summary": self.btn_view_summary,
            "open_target": self.btn_open_build_target,
            "open_source": self.btn_open_build_source,
            "undo_build": self.btn_undo_build,
        }
        self._notification_desired = {name: False for name in self._notification_widgets}
        self._notification_remaining = {name: 0 for name in self._notification_widgets}
        self._notification_timers = {}
        self._timed_notifications = {"build", "summary"}
        for name in self._timed_notifications:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda name=name: self._dismiss_timed_notification(name))
            self._notification_timers[name] = timer

    def _notification_blocked(self) -> bool:
        return bool(self._busy or self._draft_visible or self._docked_presentation)

    def _set_notification_visible(self, name: str, visible: bool, timed: bool = False) -> None:
        if name not in self._notification_widgets:
            return
        if timed:
            self._timed_notifications.add(name)
        was_desired = bool(self._notification_desired.get(name, False))
        self._notification_desired[name] = bool(visible)
        if name in self._timed_notifications:
            if visible and (not was_desired or self._notification_remaining.get(name, 0) <= 0):
                self._notification_remaining[name] = FOOTER_NOTIFICATION_TIMEOUT_MS
            elif not visible:
                self._pause_timed_notification(name)
                self._notification_remaining[name] = 0
        self._refresh_footer_presentation()

    def _pause_timed_notification(self, name: str) -> None:
        timer = self._notification_timers.get(name)
        if timer is None:
            return
        if timer.isActive():
            remaining = timer.remainingTime()
            if remaining < 0:
                remaining = self._notification_remaining.get(name, FOOTER_NOTIFICATION_TIMEOUT_MS)
            self._notification_remaining[name] = max(0, remaining)
            timer.stop()

    def _resume_timed_notifications(self) -> None:
        for name in self._timed_notifications:
            if not self._notification_desired.get(name, False):
                continue
            if self._notification_blocked():
                self._pause_timed_notification(name)
                continue
            remaining = int(self._notification_remaining.get(name, 0))
            if remaining <= 0:
                self._dismiss_timed_notification(name)
                continue
            timer = self._notification_timers.get(name)
            if timer is not None and not timer.isActive():
                timer.start(remaining)

    def _dismiss_timed_notification(self, name: str) -> None:
        self._notification_desired[name] = False
        self._notification_remaining[name] = 0
        timer = self._notification_timers.get(name)
        if timer is not None:
            timer.stop()
        widget = self._notification_widgets.get(name)
        if widget is not None:
            widget.setVisible(False)

    def _refresh_footer_presentation(self) -> None:
        blocked = self._notification_blocked()
        docked = bool(self._docked_presentation)
        self.lbl_status.setText("Ready" if docked else self._status_text)
        self.lbl_reorg_draft.setVisible(self._draft_visible and not docked)
        self.lbl_tagging_status.setVisible(self._tagging_visible and not blocked)
        self.lbl_coherence_status.setVisible(self._coherence_label_visible and not blocked)
        self.lbl_build_handover.setVisible(self._build_handover_label_visible and not blocked)
        self.btn_reorg_discard.setVisible(self._draft_visible and not docked)
        self.btn_reorg_save.setVisible(self._draft_visible and not docked)
        self.btn_cancel.setVisible(self._busy and not docked)
        self.progress_bar.setVisible(self._busy and not docked)
        for name, widget in self._notification_widgets.items():
            desired = bool(self._notification_desired.get(name, False))
            if name in self._timed_notifications and blocked:
                self._pause_timed_notification(name)
            widget.setVisible(desired and not blocked)
        self._resume_timed_notifications()

    def _hide_build_cta(self) -> None:
        self._dismiss_timed_notification("build")

    def _on_build_clicked(self) -> None:
        self._dismiss_timed_notification("build")
        self.buildRequested.emit()

    def _on_cancel_clicked(self) -> None:
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.setText("Stopping")
        self.cancelRequested.emit()

    def toggle_footer(self, expand: bool):
        """Smoothly animates the footer height and manages visibility of logs/progress."""
        target = scaled_px(FOOTER_EXPANDED_HEIGHT if expand else FOOTER_COLLAPSED_HEIGHT)
        if not self.isVisible():
            self.setMinimumHeight(target)
            self.setMaximumHeight(target)
            if expand:
                self.log_output.setVisible(False)
                self.progress_bar.setVisible(self._busy and not self._docked_presentation)
            else:
                self._finish_collapsed_state()
            return

        if self.height() == target:
            if expand:
                self.log_output.setVisible(False)
                self.progress_bar.setVisible(self._busy and not self._docked_presentation)
            if not expand:
                self._finish_collapsed_state()
            return

        import warnings
        for attr in ("_anim", "_anim2"):
            old = getattr(self, attr, None)
            if old is not None:
                old.stop()
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", RuntimeWarning)
                    try:
                        old.finished.disconnect(self._on_collapsed)
                    except (RuntimeError, TypeError):
                        pass

        self._anim = QPropertyAnimation(self, b"minimumHeight")
        self._anim.setDuration(300)
        self._anim.setStartValue(self.height())
        self._anim.setEndValue(target)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        
        self._anim2 = QPropertyAnimation(self, b"maximumHeight")
        self._anim2.setDuration(300)
        self._anim2.setStartValue(self.height())
        self._anim2.setEndValue(target)
        self._anim2.setEasingCurve(QEasingCurve.OutCubic)
        
        if expand:
            self.log_output.setVisible(False)
            self.progress_bar.setVisible(self._busy and not self._docked_presentation)
        else:
            self._anim.finished.connect(self._on_collapsed)

        self._anim.start()
        self._anim2.start()

    def _finish_collapsed_state(self):
        self.log_output.setVisible(False)
        self.progress_bar.setVisible(False)

    def _on_collapsed(self):
        """Ensures logs are hidden when the footer is at its minimum height."""
        self._finish_collapsed_state()
        try:
            self._anim.finished.disconnect(self._on_collapsed)
        except Exception:
            pass

    def refresh_theme(self) -> None:
        apply_style(self, footer_base_style())
        apply_style(self.lbl_reorg_draft, footer_draft_label_style())
        apply_style(self.lbl_tagging_status, footer_draft_label_style())
        apply_style(self.lbl_coherence_status, footer_draft_label_style())
        apply_style(self.btn_review_coherence, footer_cta_button_style())
        apply_style(self.btn_build, footer_cta_button_style())
        apply_style(self.btn_view_summary, footer_cta_button_style())
        apply_style(self.lbl_build_handover, footer_draft_label_style())
        apply_style(self.btn_open_build_target, footer_cta_button_style("secondary"))
        apply_style(self.btn_open_build_source, footer_cta_button_style("secondary"))
        apply_style(self.btn_undo_build, footer_cta_button_style("secondary"))
        apply_style(self.btn_reorg_discard, footer_cta_button_style("danger"))
        apply_style(self.btn_reorg_save, footer_cta_button_style())
        apply_style(self.btn_cancel, footer_cta_button_style("danger"))
        target = scaled_px(FOOTER_EXPANDED_HEIGHT if self._busy else FOOTER_COLLAPSED_HEIGHT)
        self.setMinimumHeight(target)
        self.setMaximumHeight(target)
        self.updateGeometry()

    def _on_view_summary_clicked(self) -> None:
        self._dismiss_timed_notification("summary")
        self.viewSummaryRequested.emit()

    def show_scan_summary_button(self) -> None:
        self._set_notification_visible("summary", True, timed=True)
