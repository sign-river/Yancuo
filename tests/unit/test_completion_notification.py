"""Regression coverage for background AI completion notifications."""

from __future__ import annotations

from PySide6.QtWidgets import QApplication, QWidget

from yancuo_win.ui.widgets import CompletionNotification


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
