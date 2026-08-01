"""Regression coverage for background AI completion notifications."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from yancuo_win.ui.widgets import CompletionNotification, ToastMessage


def test_completion_notification_queues_and_preserves_target_job() -> None:
    app = QApplication.instance() or QApplication([])
    host = QWidget()
    host.resize(800, 600)
    host.show()
    app.processEvents()
    notice = CompletionNotification(host)
    activated: list[str] = []
    notice.activated.connect(activated.append)

    notice.enqueue("job-first", 2, 1000)
    notice.enqueue("job-second", 3, 1000)

    assert notice.isVisible()
    assert "2 道待确认题目" in notice.summary.text()
    notice._activate()
    app.processEvents()
    assert activated == ["job-first"]
    assert notice.isVisible()
    assert "3 道待确认题目" in notice.summary.text()

    notice._advance()
    assert notice.progress.value() < notice.progress.maximum()
    notice.close()


def test_toast_message_slides_in_counts_down_and_supports_click_actions() -> None:
    app = QApplication.instance() or QApplication([])
    host = QWidget()
    host.resize(800, 600)
    host.show()
    app.processEvents()
    toast = ToastMessage(host)
    activated: list[str] = []

    toast.show_message("识别完成，点击查看", 1000, lambda: activated.append("open"))

    assert toast.isVisible()
    assert toast.progress.value() == toast.progress.maximum()
    assert toast.x() >= 0
    toast._advance()
    assert toast.progress.value() < toast.progress.maximum()

    QTest.mouseClick(toast, Qt.MouseButton.LeftButton)
    app.processEvents()
    assert activated == ["open"]
    assert toast.isHidden()
    toast.close()


def test_warning_toast_is_larger_and_uses_warning_tone() -> None:
    app = QApplication.instance() or QApplication([])
    host = QWidget()
    host.resize(900, 600)
    host.show()
    app.processEvents()
    toast = ToastMessage(host)

    toast.show_message("请填写复习计划名称。", tone="warning")

    assert toast.isVisible()
    assert toast.property("tone") == "warning"
    assert toast.content.property("tone") == "warning"
    assert toast.minimumWidth() == 420
    assert toast.content.minimumHeight() == 68
    toast.close()
