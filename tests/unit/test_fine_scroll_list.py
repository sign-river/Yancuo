"""FineScrollListWidget wheel behavior: smooth pixel scroll, one row per notch."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QSize
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QAbstractItemView, QApplication, QListWidgetItem

from yancuo_win.ui.widgets import FineScrollListWidget

# Matches the in-app inline question row height (main_window._InlineQuestionItem).
_ROW_H = 72


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _filled_host() -> FineScrollListWidget:
    host = FineScrollListWidget()
    for index in range(60):
        item = QListWidgetItem(f"item {index}")
        item.setSizeHint(QSize(0, _ROW_H))
        host.addItem(item)
    host.resize(300, 360)
    host.show()
    return host


def test_scrolls_per_pixel_not_per_item() -> None:
    """The wheel step is in pixels; ScrollPerItem would jump to the bottom."""
    app = _app()
    host = _filled_host()
    app.processEvents()
    assert (
        host.verticalScrollMode()
        == QAbstractItemView.ScrollMode.ScrollPerPixel
    )
    host.close()


def test_wheel_notch_scrolls_small_step_not_page() -> None:
    app = _app()
    host = _filled_host()
    app.processEvents()
    bar = host.verticalScrollBar()
    bar.setValue(0)
    app.processEvents()

    before = bar.value()
    QTest.wheelEvent(host.windowHandle(), QPoint(150, 180), QPoint(0, -120))
    app.processEvents()
    after = bar.value()

    assert after > before
    # One notch scrolls about one row, never a page and never the bottom.
    assert after - before <= FineScrollListWidget._WHEEL_STEP_PX + 1
    assert after < bar.maximum()
    host.close()


def test_wheel_up_scrolls_back() -> None:
    app = _app()
    host = _filled_host()
    app.processEvents()
    bar = host.verticalScrollBar()
    bar.setValue(bar.maximum())
    app.processEvents()
    before = bar.value()

    QTest.wheelEvent(host.windowHandle(), QPoint(150, 180), QPoint(0, 120))
    app.processEvents()
    after = bar.value()

    assert before > after
    assert before - after <= FineScrollListWidget._WHEEL_STEP_PX + 1
    host.close()
