from __future__ import annotations

from datetime import datetime, timezone

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from yancuo_win.ui.review_history_dialog import ReviewHistoryDialog, ReviewHistoryEntry


def test_review_history_dialog_exposes_copyable_details() -> None:
    app = QApplication.instance() or QApplication([])
    dialog = ReviewHistoryDialog(
        "本周计划",
        [
            ReviewHistoryEntry(
                datetime(2026, 7, 31, tzinfo=timezone.utc),
                "completed · 2 题",
                "已评分：2 题",
            )
        ],
    )
    dialog.show()
    app.processEvents()

    assert dialog.accessibleName() == "本周计划的复习历史"
    assert dialog.history_list.accessibleName() == "复习历史记录"
    assert dialog.details_view.toPlainText() == "已评分：2 题"
    dialog.copy_button.click()
    assert QApplication.clipboard().text() == "已评分：2 题"
    dialog.history_list.setFocus()
    QTest.keyClick(dialog, Qt.Key.Key_Tab)
    assert QApplication.focusWidget() is dialog.details_view
    dialog.close()
