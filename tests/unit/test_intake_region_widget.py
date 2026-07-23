"""Interactive AI candidate image-region selection."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from yancuo_win.ui.intake_page import ImagePreviewLabel


def _make_preview() -> tuple[QApplication, ImagePreviewLabel]:
    app = QApplication.instance() or QApplication([])
    preview = ImagePreviewLabel("empty")
    preview.resize(640, 480)
    preview.source = QPixmap(800, 600)
    preview.source.fill(QColor("white"))
    preview.set_editable(True)
    preview._render()
    preview.show()
    app.processEvents()
    return app, preview


def _point(preview: ImagePreviewLabel, x: float, y: float) -> QPoint:
    displayed = preview._displayed_rect()
    return QPoint(
        round(displayed.left() + displayed.width() * x),
        round(displayed.top() + displayed.height() * y),
    )


def test_dragging_preview_emits_normalized_region() -> None:
    app, preview = _make_preview()

    emitted: list[dict[str, float]] = []
    preview.region_drawn.connect(emitted.append)
    start = _point(preview, 0.2, 0.25)
    end = _point(preview, 0.8, 0.75)
    QTest.mousePress(preview, Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(preview, end)
    QTest.mouseRelease(preview, Qt.MouseButton.LeftButton, pos=end)
    app.processEvents()

    assert len(emitted) == 1
    assert emitted[0]["x"] == pytest.approx(0.2, abs=0.01)
    assert emitted[0]["y"] == pytest.approx(0.25, abs=0.01)
    assert emitted[0]["width"] == pytest.approx(0.6, abs=0.01)
    assert emitted[0]["height"] == pytest.approx(0.5, abs=0.01)
    preview.close()


def test_dragging_inside_region_moves_it_without_resizing() -> None:
    app, preview = _make_preview()
    preview.set_region({"x": 0.2, "y": 0.25, "width": 0.5, "height": 0.4})
    emitted: list[dict[str, float]] = []
    preview.region_drawn.connect(emitted.append)

    QTest.mousePress(
        preview, Qt.MouseButton.LeftButton, pos=_point(preview, 0.45, 0.45)
    )
    QTest.mouseMove(preview, _point(preview, 0.55, 0.55))
    QTest.mouseRelease(
        preview, Qt.MouseButton.LeftButton, pos=_point(preview, 0.55, 0.55)
    )
    app.processEvents()

    assert len(emitted) == 1
    assert emitted[0]["x"] == pytest.approx(0.3, abs=0.01)
    assert emitted[0]["y"] == pytest.approx(0.35, abs=0.01)
    assert emitted[0]["width"] == pytest.approx(0.5, abs=0.01)
    assert emitted[0]["height"] == pytest.approx(0.4, abs=0.01)
    preview.close()


def test_dragging_corner_handle_resizes_region() -> None:
    app, preview = _make_preview()
    preview.set_region({"x": 0.2, "y": 0.2, "width": 0.5, "height": 0.5})
    emitted: list[dict[str, float]] = []
    preview.region_drawn.connect(emitted.append)

    QTest.mousePress(
        preview, Qt.MouseButton.LeftButton, pos=_point(preview, 0.7, 0.7)
    )
    QTest.mouseMove(preview, _point(preview, 0.9, 0.85))
    QTest.mouseRelease(
        preview, Qt.MouseButton.LeftButton, pos=_point(preview, 0.9, 0.85)
    )
    app.processEvents()

    assert len(emitted) == 1
    assert emitted[0]["x"] == pytest.approx(0.2, abs=0.01)
    assert emitted[0]["y"] == pytest.approx(0.2, abs=0.01)
    assert emitted[0]["width"] == pytest.approx(0.7, abs=0.01)
    assert emitted[0]["height"] == pytest.approx(0.65, abs=0.01)
    preview.close()
