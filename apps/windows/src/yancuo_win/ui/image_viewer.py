"""Reusable, read-only viewer for immutable source images."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
)

from yancuo_win.ui.widgets import PageHeader, default_button


class ImageViewerDialog(QDialog):
    """Show a source image with zoom controls and optional source regions."""

    def __init__(
        self,
        pixmap: QPixmap,
        parent=None,
        *,
        source_regions: Iterable[Mapping[str, float]] = (),
        image_paths: Iterable[Path] = (),
        image_index: int = 0,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("ImageViewerDialog")
        self._source = pixmap
        self._scale = 1.0
        self._source_regions = self._normalize_regions(source_regions)
        self._image_paths = tuple(Path(path) for path in image_paths)
        self._image_index = min(
            max(0, image_index), max(0, len(self._image_paths) - 1)
        )
        self.current_image_path = (
            self._image_paths[self._image_index]
            if self._image_paths
            else None
        )
        self.setWindowTitle("查看原始图片")
        self.resize(1000, 760)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 16)
        root.setSpacing(12)
        root.addWidget(PageHeader("原始图片", "缩放和查看录入时保存的不可变原图。"))
        toolbar = QFrame()
        toolbar.setObjectName("DialogToolbar")
        controls = QHBoxLayout(toolbar)
        controls.setContentsMargins(12, 8, 12, 8)
        controls.setSpacing(8)
        self.previous_button = default_button("上一张")
        self.previous_button.clicked.connect(lambda: self._move_image(-1))
        self.next_button = default_button("下一张")
        self.next_button.clicked.connect(lambda: self._move_image(1))
        self.image_position_label = QLabel()
        zoom_out = default_button("缩小")
        zoom_out.clicked.connect(lambda: self._zoom(0.8))
        reset = default_button("重置缩放")
        reset.clicked.connect(self._reset)
        zoom_in = default_button("放大")
        zoom_in.clicked.connect(lambda: self._zoom(1.25))
        fit = default_button("适应窗口")
        fit.clicked.connect(self._fit)
        self.scale_label = QLabel("")
        for button in (
            self.previous_button,
            self.next_button,
            zoom_out,
            reset,
            zoom_in,
            fit,
        ):
            controls.addWidget(button)
        controls.addWidget(self.image_position_label)
        controls.addWidget(self.scale_label)
        if self._source_regions:
            source_hint = QLabel("蓝框为内容块在原图中的来源区域")
            source_hint.setObjectName("MutedLabel")
            controls.addWidget(source_hint)
        controls.addStretch(1)
        root.addWidget(toolbar)

        self.image = QLabel()
        self.image.setObjectName("SourceImage")
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scroll = QScrollArea()
        self.scroll.setObjectName("ImageViewerCanvas")
        self.scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scroll.setWidget(self.image)
        self.scroll.setWidgetResizable(False)
        root.addWidget(self.scroll, stretch=1)
        self._update_image_navigation()
        self._render()

    def showEvent(self, event) -> None:  # noqa: ANN001, N802
        super().showEvent(event)
        self._fit()

    def _zoom(self, factor: float) -> None:
        self._scale = max(0.1, min(5.0, self._scale * factor))
        self._render()

    def _reset(self) -> None:
        self._scale = 1.0
        self._render()

    def _fit(self) -> None:
        viewport = self.scroll.viewport().size() - QSize(24, 24)
        if self._source.width() and self._source.height():
            self._scale = min(
                viewport.width() / self._source.width(),
                viewport.height() / self._source.height(),
                1.0,
            )
        self._render()

    def _move_image(self, delta: int) -> None:
        if not self._image_paths:
            return
        next_index = self._image_index + delta
        if not 0 <= next_index < len(self._image_paths):
            return
        source = QPixmap(str(self._image_paths[next_index]))
        if source.isNull():
            return
        self._image_index = next_index
        self.current_image_path = self._image_paths[next_index]
        self._source = source
        self._scale = 1.0
        self._update_image_navigation()
        self._fit()

    def _update_image_navigation(self) -> None:
        count = len(self._image_paths)
        self.previous_button.setEnabled(count > 1 and self._image_index > 0)
        self.next_button.setEnabled(count > 1 and self._image_index < count - 1)
        self.image_position_label.setText(f"{self._image_index + 1} / {count}" if count else "")

    def _render(self) -> None:
        size = self._source.size() * self._scale
        rendered = self._source.scaled(
            size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        if self._source_regions:
            rendered = rendered.copy()
            painter = QPainter(rendered)
            painter.setPen(QPen(QColor("#3370FF"), max(2, round(self._scale * 3))))
            painter.setBrush(QColor(51, 112, 255, 36))
            for region in self._source_regions:
                painter.drawRect(
                    QRectF(
                        region["x"] * rendered.width(),
                        region["y"] * rendered.height(),
                        region["width"] * rendered.width(),
                        region["height"] * rendered.height(),
                    )
                )
            painter.end()
        self.image.setPixmap(rendered)
        self.image.resize(rendered.size())
        self.scale_label.setText(f"{round(self._scale * 100)}%")

    @staticmethod
    def _normalize_regions(
        regions: Iterable[Mapping[str, float]],
    ) -> tuple[dict[str, float], ...]:
        normalized: list[dict[str, float]] = []
        for value in regions:
            try:
                x = min(1.0, max(0.0, float(value.get("x", 0))))
                y = min(1.0, max(0.0, float(value.get("y", 0))))
                width = min(1.0 - x, max(0.0, float(value.get("width", 0))))
                height = min(1.0 - y, max(0.0, float(value.get("height", 0))))
            except (AttributeError, TypeError, ValueError):
                continue
            if width > 0 and height > 0:
                normalized.append(
                    {"x": x, "y": y, "width": width, "height": height}
                )
        return tuple(normalized)
