def test_footer_draft_state_does_not_expand_logs(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from gui.widgets.footer import ModernFooter

    _app = QApplication.instance() or QApplication([])
    footer = ModernFooter()
    calls = []
    original_toggle = footer.toggle_footer

    def record_toggle(expand):
        calls.append(bool(expand))
        original_toggle(expand)

    footer.toggle_footer = record_toggle
    footer.set_reorg_draft_state("Draft changes pending", True, can_save=True)
    footer.set_busy_state(False)

    assert calls[-1] is False
    assert not footer.btn_reorg_discard.isHidden()
    assert not footer.btn_reorg_save.isHidden()
    assert footer.log_output.isHidden()
    assert footer.progress_bar.isHidden()


def test_footer_status_labels_are_bounded_and_ctas_are_short(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from gui.widgets.footer import ModernFooter

    _app = QApplication.instance() or QApplication([])
    footer = ModernFooter()

    assert footer.lbl_tagging_status.maximumWidth() > 0
    assert footer.lbl_coherence_status.maximumWidth() > 0
    assert not hasattr(footer, "btn_filter_duplicates")
    assert footer.btn_review_coherence.text() == "Review Outliers"
    assert footer.btn_build.text() == "Build"


def test_footer_build_handover_state_shows_persistent_ctas(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from gui.widgets.footer import ModernFooter

    _app = QApplication.instance() or QApplication([])
    footer = ModernFooter()

    footer.set_build_handover_state(
        "Move complete. 10 files moved. Source has 1 file / 3 B remaining.",
        True,
        can_open_target=True,
        can_open_source=True,
        can_undo=True,
        target_tooltip="Open target:\nD:/Target",
        source_tooltip="Open source:\nD:/Source",
    )

    assert not footer.lbl_build_handover.isHidden()
    assert not footer.btn_open_build_target.isHidden()
    assert not footer.btn_open_build_source.isHidden()
    assert not footer.btn_undo_build.isHidden()
    assert footer.btn_open_build_target.toolTip() == "Open target:\nD:/Target"

    footer.clear_build_handover_state()

    assert footer.lbl_build_handover.isHidden()
    assert footer.btn_open_build_target.isHidden()
    assert footer.btn_open_build_source.isHidden()
    assert footer.btn_undo_build.isHidden()


def test_footer_build_handover_blocks_stale_coherence_build_cta(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from gui.widgets.footer import ModernFooter

    _app = QApplication.instance() or QApplication([])
    footer = ModernFooter()

    footer.set_build_handover_state(
        "Move complete.",
        True,
        can_open_target=True,
        can_open_source=True,
        can_undo=True,
    )
    footer.set_coherence_state("Coherence looks stable. Library is ready to build.", True, can_build=True)

    assert footer.btn_build.isHidden()
    assert not footer.btn_open_build_target.isHidden()


def test_footer_collapse_resets_log_when_already_collapsed(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from gui.widgets.footer import ModernFooter

    _app = QApplication.instance() or QApplication([])
    footer = ModernFooter()

    footer.log_output.setVisible(True)
    footer.progress_bar.setVisible(True)

    footer.toggle_footer(False)

    assert footer.log_output.isHidden()
    assert footer.progress_bar.isHidden()


def test_footer_coherence_notice_does_not_keep_log_expanded_after_busy(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from gui.widgets.footer import ModernFooter

    _app = QApplication.instance() or QApplication([])
    footer = ModernFooter()
    calls = []
    original_toggle = footer.toggle_footer

    def record_toggle(expand):
        calls.append(bool(expand))
        original_toggle(expand)

    footer.toggle_footer = record_toggle
    footer.set_coherence_state("Coherence: auto-staged 11 refinement(s).", True)
    footer.set_busy_state(False)

    assert calls[-1] is False


def test_footer_review_coherence_hides_redundant_status_label(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from gui.widgets.footer import ModernFooter

    _app = QApplication.instance() or QApplication([])
    footer = ModernFooter()

    footer.set_coherence_state("Coherence: 12 items to review.", True, can_review=True)

    assert not footer.btn_review_coherence.isHidden()
    assert footer.lbl_coherence_status.isHidden()


def test_footer_build_coherence_hides_elided_status_label(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from gui.widgets.footer import FOOTER_NOTIFICATION_TIMEOUT_MS, ModernFooter

    _app = QApplication.instance() or QApplication([])
    footer = ModernFooter()

    footer.set_coherence_state("Coherence looks stable. Library is ready to build.", True, can_build=True)

    assert not footer.btn_build.isHidden()
    assert footer.lbl_coherence_status.isHidden()
    assert footer._notification_timers["build"].isActive()
    assert footer._notification_timers["build"].interval() == FOOTER_NOTIFICATION_TIMEOUT_MS


def test_footer_build_cta_hides_after_timeout(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from gui.widgets.footer import ModernFooter

    _app = QApplication.instance() or QApplication([])
    footer = ModernFooter()

    footer.set_coherence_state("Coherence looks stable. Library is ready to build.", True, can_build=True)
    footer._notification_timers["build"].timeout.emit()

    assert footer.btn_build.isHidden()
    assert not footer._notification_timers["build"].isActive()


def test_footer_build_cta_pauses_when_busy_starts(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from gui.widgets.footer import ModernFooter

    _app = QApplication.instance() or QApplication([])
    footer = ModernFooter()

    footer.set_coherence_state("Coherence looks stable. Library is ready to build.", True, can_build=True)
    footer.set_busy_state(True)

    assert footer.btn_build.isHidden()
    assert not footer._notification_timers["build"].isActive()
    assert footer._notification_remaining["build"] > 0
    assert not footer.btn_cancel.isHidden()
    assert footer.btn_cancel.isEnabled()
    assert footer._progress_row.count() == 1

    footer.set_busy_state(False)

    assert not footer.btn_build.isHidden()
    assert footer._notification_timers["build"].isActive()


def test_footer_docked_presentation_hides_ctas_without_clearing_state(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from gui.widgets.footer import ModernFooter

    _app = QApplication.instance() or QApplication([])
    footer = ModernFooter()

    footer.set_status("Coherence looks stable.")
    footer.set_count("12 files ready")
    footer.set_coherence_state("Coherence looks stable. Library is ready to build.", True, can_build=True)

    assert not footer.btn_build.isHidden()

    footer.set_docked_presentation(True)

    assert footer.lbl_status.text() == "Ready"
    assert footer.lbl_count.text() == "12 files ready"
    assert footer.btn_build.isHidden()
    assert footer.lbl_coherence_status.isHidden()

    footer.set_docked_presentation(False)

    assert footer.lbl_status.text() == "Coherence looks stable."
    assert not footer.btn_build.isHidden()


def test_footer_docked_presentation_does_not_restore_finished_cancel(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from gui.widgets.footer import ModernFooter

    _app = QApplication.instance() or QApplication([])
    footer = ModernFooter()

    footer.set_busy_state(True)
    assert not footer.btn_cancel.isHidden()

    footer.set_docked_presentation(True)
    footer.set_busy_state(False)
    footer.set_docked_presentation(False)

    assert footer.btn_cancel.isHidden()
    assert footer.progress_bar.isHidden()


def test_footer_cancel_button_disables_after_click(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from gui.widgets.footer import ModernFooter

    _app = QApplication.instance() or QApplication([])
    footer = ModernFooter()
    calls = []
    footer.cancelRequested.connect(lambda: calls.append("cancel"))

    footer.set_busy_state(True)

    assert "background" in footer.btn_cancel.styleSheet()
    assert footer.btn_cancel.styleSheet() == footer.btn_reorg_discard.styleSheet()
    footer.btn_cancel.click()

    assert calls == ["cancel"]
    assert not footer.btn_cancel.isEnabled()
    assert footer.btn_cancel.text() == "Stopping"

    footer.set_busy_state(False)

    assert footer.btn_cancel.isHidden()


def test_footer_hides_review_cta_while_busy_and_restores_after(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from gui.widgets.footer import ModernFooter

    _app = QApplication.instance() or QApplication([])
    footer = ModernFooter()

    footer.set_coherence_state("Coherence: 12 items to review.", True, can_review=True)
    assert not footer.btn_review_coherence.isHidden()

    footer.set_busy_state(True)

    assert footer.btn_review_coherence.isHidden()
    assert not footer.btn_cancel.isHidden()

    footer.set_busy_state(False)

    assert not footer.btn_review_coherence.isHidden()
    assert footer.btn_cancel.isHidden()


def test_footer_draft_hides_notifications_and_restores_after(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from gui.widgets.footer import ModernFooter

    _app = QApplication.instance() or QApplication([])
    footer = ModernFooter()

    footer.set_coherence_state("Coherence: 12 items to review.", True, can_review=True)
    footer.show_scan_summary_button()

    assert not footer.btn_review_coherence.isHidden()
    assert not footer.btn_view_summary.isHidden()

    footer.set_reorg_draft_state("Draft changes pending", True, can_save=True)

    assert footer.btn_review_coherence.isHidden()
    assert footer.btn_view_summary.isHidden()
    assert footer.log_output.isHidden()

    footer.set_reorg_draft_state("", False)

    assert not footer.btn_review_coherence.isHidden()
    assert not footer.btn_view_summary.isHidden()


def test_footer_scan_summary_uses_shared_timer(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from gui.widgets.footer import FOOTER_NOTIFICATION_TIMEOUT_MS, ModernFooter

    _app = QApplication.instance() or QApplication([])
    footer = ModernFooter()

    footer.show_scan_summary_button()

    assert not footer.btn_view_summary.isHidden()
    assert footer._notification_timers["summary"].isActive()
    assert footer._notification_timers["summary"].interval() == FOOTER_NOTIFICATION_TIMEOUT_MS


def test_footer_scan_summary_click_dismisses_without_restore(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from gui.widgets.footer import ModernFooter

    _app = QApplication.instance() or QApplication([])
    footer = ModernFooter()
    calls = []
    footer.viewSummaryRequested.connect(lambda: calls.append("summary"))

    footer.show_scan_summary_button()
    footer.btn_view_summary.click()
    footer.set_busy_state(True)
    footer.set_busy_state(False)

    assert calls == ["summary"]
    assert footer.btn_view_summary.isHidden()


def test_footer_build_timeout_does_not_restore_after_busy(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from gui.widgets.footer import ModernFooter

    _app = QApplication.instance() or QApplication([])
    footer = ModernFooter()

    footer.set_coherence_state("Coherence looks stable. Library is ready to build.", True, can_build=True)
    footer._notification_timers["build"].timeout.emit()
    footer.set_busy_state(True)
    footer.set_busy_state(False)

    assert footer.btn_build.isHidden()
