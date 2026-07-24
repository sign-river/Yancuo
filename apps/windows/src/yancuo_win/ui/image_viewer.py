"""Reusable, read-only viewer for immutable source images."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
)


class ImageViewerDialog(QDialog):
    """Show a source image with zoom controls and optional source regions."""

    def __init__(
        self,
        pixmap: QPixmap,
        parent=None,
        *,
        source_regions: Iterable[Mapping[str, float]] = (),
    ) -> None:
        super().__init__(parent)
        self._source = pixmap
        self._scale = 1.0
        self._source_regions = self._normalize_regions(source_regions)
        self.setWindowTitle("查看原始图片")
        self.resize(1000, 760)

        root = QVBoxLayout(self)
        controls = QHBoxLayout()
        zoom_out = QPushButton("－")
        zoom_out.clicked.connect(lambda: self._zoom(0.8))
        reset = QPushButton("100%")
        reset.clicked.connect(self._reset)
        zoom_in = QPushButton("＋")
        zoom_in.clicked.connect(lambda: self._zoom(1.25))
        fit = QPushButton("适应窗口")
        fit.clicked.connect(self._fit)
        self.scale_label = QLabel("")
        for button in (zoom_out, reset, zoom_in, fit):
            controls.addWidget(button)
        controls.addWidget(self.scale_label)
        if self._source_regions:
            source_hint = QLabel("蓝框为内容块在原图中的来源区域")
            source_hint.setObjectName("MutedLabel")
            controls.addWidget(source_hint)
        controls.addStretch(1)
        root.addLayout(controls)

        self.image = QLabel()
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scroll = QScrollArea()
        self.scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scroll.setWidget(self.image)
        self.scroll.setWidgetResizable(False)
        root.addWidget(self.scroll, stretch=1)
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
