"""Image viewer visual structure and zoom regression tests."""

from __future__ import annotations

import pytest
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication

from yancuo_win.ui.image_viewer import ImageViewerDialog


def test_image_viewer_keeps_original_zoom_mapping_and_semantic_surfaces() -> None:
    app = QApplication.instance() or QApplication([])
    dialog = ImageViewerDialog(QPixmap(100, 100))

    assert dialog.objectName() == "ImageViewerDialog"
    assert dialog.scroll.objectName() == "ImageViewerCanvas"
    assert dialog.scale_label.text() == "100%"

    dialog._zoom(0.8)
    assert dialog._scale == pytest.approx(0.8)
    assert dialog.scale_label.text() == "80%"

    dialog._zoom(1.25)
    assert dialog._scale == pytest.approx(1.0)
    assert dialog.scale_label.text() == "100%"

    dialog.close()
    app.processEvents()
