"""FineScrollListWidget wheel behavior: small fixed step per notch."""

from __future__ import annotations

from PySide6.QtCore import QPoint
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QListWidgetItem

from yancuo_win.ui.widgets import FineScrollListWidget


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_wheel_notch_scrolls_small_step_not_page() -> None:
    app = _app()
    host = FineScrollListWidget()
    for index in range(60):
        item = QListWidgetItem(f"题目 {index}")
        item.setSizeHint(item.sizeHint())
        host.addItem(item)
    host.resize(300, 360)
    host.show()
    app.processEvents()
    host.verticalScrollBar().setValue(0)
    app.processEvents()

    before = host.verticalScrollBar().value()
    QTest.wheelEvent(
        host.windowHandle(),
        QPoint(150, 180),
        QPoint(0, -120),
    )
    app.processEvents()
    after = host.verticalScrollBar().value()

    assert after > before
    # 一格只滚动约一行，而不是一页
    assert after - before <= 72 + 1
    host.close()


def test_wheel_up_scrolls_back() -> None:
    app = _app()
    host = FineScrollListWidget()
    for index in range(60):
        item = QListWidgetItem(f"题目 {index}")
        item.setSizeHint(item.sizeHint())
        host.addItem(item)
    host.resize(300, 360)
    host.show()
    app.processEvents()
    bar = host.verticalScrollBar()
    bar.setValue(bar.maximum())
    app.processEvents()
    before = bar.value()

    QTest.wheelEvent(
        host.windowHandle(),
        QPoint(150, 180),
        QPoint(0, 120),
    )
    app.processEvents()
    after = bar.value()
    assert before > after
    assert before - after <= 72 + 1
    host.close()
