from __future__ import annotations

from datetime import datetime, timezone

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton, QWidget

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
    assert dialog.copy_button.accessibleDescription() == "将当前复习历史详情复制到剪贴板"
    dialog.history_list.setFocus()
    QTest.keyClick(dialog, Qt.Key.Key_Tab)
    assert QApplication.focusWidget() is dialog.details_view
    dialog.close()


def test_review_history_restores_focus_to_its_invoker() -> None:
    app = QApplication.instance() or QApplication([])
    parent = QWidget()
    invoker = QPushButton("查看完整历史", parent)
    parent.show()
    invoker.setFocus()
    app.processEvents()
    dialog = ReviewHistoryDialog("本周计划", [], parent)

    dialog.done(0)
    app.processEvents()

    assert QApplication.focusWidget() is invoker
    dialog.close()
    parent.close()
