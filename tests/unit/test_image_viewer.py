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


def test_image_viewer_navigates_an_uploaded_image_batch(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    paths = []
    for index, color in enumerate(("red", "blue")):
        path = tmp_path / f"image-{index}.png"
        pixmap = QPixmap(80 + index * 10, 60)
        pixmap.fill(color)
        assert pixmap.save(str(path))
        paths.append(path)

    dialog = ImageViewerDialog(
        QPixmap(str(paths[0])), image_paths=paths, image_index=0
    )
    assert dialog.image_position_label.text() == "1 / 2"
    assert not dialog.previous_button.isEnabled()
    assert dialog.next_button.isEnabled()

    dialog._move_image(1)
    assert dialog.current_image_path == paths[1]
    assert dialog.image_position_label.text() == "2 / 2"
    assert dialog.previous_button.isEnabled()
    assert not dialog.next_button.isEnabled()
    dialog.close()
    app.processEvents()
