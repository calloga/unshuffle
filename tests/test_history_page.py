import os
import unittest


def _history_page_app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _session(session_id: str = "session-1") -> dict:
    return {
        "session_id": session_id,
        "timestamp": "2026-06-04T23:10:02",
        "mode": "move",
        "file_count": 11489,
        "source_path": "d:/drum kits - copy",
        "target_root": "d:/music/test/oneshots",
    }


def _action_button(page):
    from PySide6.QtWidgets import QPushButton

    cell = page.table.cellWidget(0, 5)
    if isinstance(cell, QPushButton):
        return cell
    return cell.findChild(QPushButton, "HistoryActionButton") if cell is not None else None


class HistoryPageActionButtonTests(unittest.TestCase):
    def test_mark_undone_keeps_visible_session_as_disabled_status(self):
        app = _history_page_app()
        from PySide6.QtWidgets import QPushButton
        from gui.widgets.history_page import HistoryPage

        page = HistoryPage()
        try:
            page._set_sessions([_session()], "")
            page.mark_undone("session-1")
            app.processEvents()

            button = _action_button(page)

            self.assertIsInstance(button, QPushButton)
            assert isinstance(button, QPushButton)
            self.assertEqual(button.text(), "Undone")
            self.assertEqual(button.property("historyAction"), "undone")
            self.assertFalse(button.isEnabled())
        finally:
            page.close()
            page.deleteLater()
            app.processEvents()

    def test_retryable_session_still_uses_retry_label(self):
        app = _history_page_app()
        from PySide6.QtWidgets import QPushButton
        from gui.widgets.history_page import HistoryPage

        page = HistoryPage()
        try:
            page._set_sessions([_session()], "")
            page.mark_retryable("session-1")
            app.processEvents()

            button = _action_button(page)

            self.assertIsInstance(button, QPushButton)
            assert isinstance(button, QPushButton)
            self.assertEqual(button.text(), "Retry Undo")
            self.assertEqual(button.property("historyAction"), "retry")
        finally:
            page.close()
            page.deleteLater()
            app.processEvents()

    def test_undone_session_double_click_does_not_emit_undo(self):
        app = _history_page_app()
        from gui.widgets.history_page import HistoryPage

        page = HistoryPage()
        emitted = []
        page.undoRequested.connect(lambda session: emitted.append(session))
        try:
            page._set_sessions([{**_session(), "history_state": "undone"}], "")
            page.table.setCurrentCell(0, 0)
            page._emit_selected_undo()
            app.processEvents()

            self.assertEqual(emitted, [])
        finally:
            page.close()
            page.deleteLater()
            app.processEvents()

    def test_action_button_is_padded_inside_cell(self):
        app = _history_page_app()
        from gui.widgets.history_page import HistoryPage

        page = HistoryPage()
        try:
            page._set_sessions([_session()], "")
            app.processEvents()

            cell = page.table.cellWidget(0, 5)

            self.assertIsNotNone(cell)
            assert cell is not None
            self.assertEqual(cell.objectName(), "HistoryActionCell")
            layout = cell.layout()
            self.assertIsNotNone(layout)
            assert layout is not None
            margins = layout.contentsMargins()
            self.assertGreater(margins.left(), 0)
            self.assertGreater(margins.right(), 0)
            button = _action_button(page)
            self.assertIsNotNone(button)
            assert button is not None
            self.assertLessEqual(button.height() + margins.top() + margins.bottom(), page.table.rowHeight(0))
            self.assertGreaterEqual(page.table.verticalHeader().defaultSectionSize(), 48)
        finally:
            page.close()
            page.deleteLater()
            app.processEvents()
