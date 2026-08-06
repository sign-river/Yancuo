"""AI discussion panel chat components tests."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from yancuo_win.ui.problem_detail import _ChatFlow, _ChatInputEdit


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_chat_flow_creates_bubbles_and_hides_scrollbars() -> None:
    app = _app()
    flow = _ChatFlow()
    flow.resize(520, 400)
    flow.show()
    app.processEvents()
    user = flow.add_message("user", "你好")
    ai = flow.add_message("assistant", "你好，有什么问题？")
    app.processEvents()
    assert user.role == "user"
    assert ai.role == "assistant"
    assert flow.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    assert flow.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    assert user.maximumWidth() == 480
    flow.clear()
    app.processEvents()
    assert flow._layout.count() == 1  # only the trailing stretch remains
    flow.close()


def test_chat_input_enter_submits_but_shift_enter_newlines() -> None:
    app = _app()
    edit = _ChatInputEdit()
    edit.show()
    app.processEvents()
    submitted: list[bool] = []
    edit.submit_requested.connect(lambda: submitted.append(True))
    edit.setPlainText("hello")
    QTest.keyClick(edit, Qt.Key.Key_Return)
    assert submitted == [True]
    edit.setPlainText("a")
    QTest.keyClick(edit, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
    assert submitted == [True]
    edit.setPlainText("b")
    QTest.keyClick(edit, Qt.Key.Key_Enter)
    assert submitted == [True, True]
    edit.close()
