"""Task-oriented problem intake page.

Manual entry and AI-assisted entry live in one persistent page stack.  The
user never needs to navigate through the library, task center, and review
dialog to finish recording a new problem.
"""

from __future__ import annotations

import math
import json
import base64
from pathlib import Path
from time import perf_counter
from typing import Any

from PySide6.QtCore import (
    QBuffer,
    QEvent,
    QIODevice,
    QObject,
    QPointF,
    QRectF,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QIcon,
    QImage,
    QPainter,
    QPen,
    QPixmap,
    QTextCursor,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QBoxLayout,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QInputDialog,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSplitter,
    QSpinBox,
    QStackedWidget,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from yancuo_win.config.settings import (
    load_intake_prompt_templates,
    save_intake_prompt_templates,
)
from yancuo_win.application.intake_service import (
    IntakeCandidate,
    ProblemIntakeService,
    RegionRecognitionProposal,
)
from yancuo_win.ai.base import normalize_content_blocks
from yancuo_win.domain.rules import DomainError
from yancuo_win.tasks.ai_coordinator import AIJobCoordinator
from yancuo_win.tasks.worker import (
    RegionRecognitionWorker,
    UserAnswerRecognitionWorker,
)
from yancuo_win.ui.icons import bind_icon
from yancuo_win.ui.image_viewer import ImageViewerDialog
from yancuo_win.ui.math_content import MathContentView
from yancuo_win.ui.widgets import (
    CardFrame,
    IconButton,
    PageHeader,
    ScrollSafeDoubleSpinBox,
    ScrollSafeSpinBox,
    SoftItemDelegate,
    WorkflowStepBar,
    danger_button,
    describe_field,
    ghost_button,
    primary_button,
    set_tab_order_chain,
)


_PAGE_MANUAL = 0
_PAGE_AI_UPLOAD = 1
_PAGE_AI_PROCESSING = 2
_PAGE_AI_CONFIRM = 3
_PAGE_DONE = 4
_PAGE_AI_ANSWER_CAPTURE = 5

_AI_PROCESSING_HINT = "正在识别并整理题目，请稍候…"


class FocusAwareTextEdit(QTextEdit):
    """文本编辑框只在获得焦点（被点击/选中）时才响应滚轮。

    未聚焦时把滚轮事件透传给父级滚动区域，避免滚动经过容器时
    被文本容器截获、变成容器内滚动。
    """

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        if not self.hasFocus():
            event.ignore()
            return
        if self.verticalScrollBar().maximum() == 0:
            event.ignore()
            return
        super().wheelEvent(event)


class ContentBlocksEditor(QWidget):
    """Ordered editor for AI-recognized text, formula, table, and figure blocks."""

    changed = Signal()
    figure_crop_requested = Signal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._blocks: list[dict[str, Any]] = []
        self._source_images: list[Path] = []
        self._loading = False
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)
        self.block_list = QListWidget()
        self.block_list.setAccessibleName("题目结构化内容块顺序")
        self.block_list.setMinimumHeight(110)
        root.addWidget(self.block_list)
        actions = QGridLayout()
        for column, (label, kind) in enumerate((("添加文本", "text"), ("添加公式", "formula"), ("添加表格", "table"), ("添加题图", "figure"))):
            button = ghost_button(label)
            button.clicked.connect(lambda _checked=False, value=kind: self._add(value))
            actions.addWidget(button, 0, column)
        for column, (label, delta) in enumerate((("上移", -1), ("下移", 1))):
            button = ghost_button(label)
            button.clicked.connect(lambda _checked=False, value=delta: self._move(value))
            actions.addWidget(button, 1, column)
        remove = danger_button("删除块")
        remove.clicked.connect(self._remove)
        actions.addWidget(remove, 1, 2)
        actions.setColumnStretch(3, 1)
        root.addLayout(actions)

        form = QFormLayout()
        self.kind = QComboBox()
        for label, value in (("文本", "text"), ("公式", "formula"), ("表格", "table"), ("题图", "figure")):
            self.kind.addItem(label, value)
        self.content = FocusAwareTextEdit()
        self.content.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.content.setPlaceholderText("文本、公式或题图说明")
        self.table_rows = FocusAwareTextEdit()
        self.table_rows.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.table_rows.setPlaceholderText(
            "每行一行、制表符分隔；合并单元格可直接填写 rows JSON"
        )
        self.source_image_index = ScrollSafeSpinBox()
        self.source_image_index.setRange(0, 999)
        self.region_row = QWidget()
        region_layout = QHBoxLayout(self.region_row)
        region_layout.setContentsMargins(0, 0, 0, 0)
        self.region_values: list[QDoubleSpinBox] = []
        for label, default in (("x", 0.0), ("y", 0.0), ("宽", 1.0), ("高", 1.0)):
            region_layout.addWidget(QLabel(label))
            spin = ScrollSafeDoubleSpinBox()
            spin.setRange(0.0, 1.0)
            spin.setDecimals(4)
            spin.setSingleStep(0.01)
            spin.setValue(default)
            region_layout.addWidget(spin)
            self.region_values.append(spin)
        describe_field(self.kind, "内容块类型", "可将误识别的表格或题图降级为文本")
        describe_field(self.content, "内容块文本或说明")
        describe_field(self.table_rows, "表格单元格内容")
        describe_field(self.source_image_index, "题图来源图片序号")
        for spin, name in zip(self.region_values, ("题图区域横坐标", "题图区域纵坐标", "题图区域宽度", "题图区域高度"), strict=True):
            describe_field(spin, name, "范围 0 到 1")
        form.addRow("类型", self.kind)
        form.addRow("内容 / 说明", self.content)
        form.addRow("表格行列", self.table_rows)
        form.addRow("来源图片序号", self.source_image_index)
        form.addRow("归一化裁剪区域", self.region_row)
        root.addLayout(form)

        self.figure_panel = QFrame()
        self.figure_panel.setObjectName("FigureBlockEditor")
        figure_layout = QVBoxLayout(self.figure_panel)
        figure_layout.setContentsMargins(12, 10, 12, 10)
        self.figure_preview = QLabel("选择来源图片并调整裁剪区域")
        self.figure_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.figure_preview.setMinimumHeight(120)
        self.figure_preview.setObjectName("ImagePreview")
        figure_layout.addWidget(self.figure_preview)
        self.crop_figure_button = ghost_button("在原图上调整裁剪")
        self.crop_figure_button.setAccessibleName("调整当前题图裁剪区域")
        self.crop_figure_button.clicked.connect(self._request_figure_crop)
        figure_layout.addWidget(self.crop_figure_button)
        root.addWidget(self.figure_panel)

        self.block_list.currentRowChanged.connect(self._select)
        self.kind.currentIndexChanged.connect(self._write_current)
        self.content.textChanged.connect(self._write_current)
        self.table_rows.textChanged.connect(self._write_current)
        self.source_image_index.valueChanged.connect(self._write_current)
        for spin in self.region_values:
            spin.valueChanged.connect(self._write_current)
        self._sync_editor_visibility()

    @staticmethod
    def _label(block: dict[str, Any], index: int) -> str:
        labels = {"text": "文本", "formula": "公式", "table": "表格", "figure": "题图"}
        content = str(block.get("content") or "").replace("\n", " ").strip()
        if block.get("type") == "table":
            content = f"{len(block.get('rows') or [])} 行"
        return f"{index + 1}. {labels.get(str(block.get('type')), '内容')} · {content[:36]}".rstrip(" ·")

    def _refresh_list(self, selected: int | None = None) -> None:
        self._loading = True
        self.block_list.clear()
        for index, block in enumerate(self._blocks):
            self.block_list.addItem(self._label(block, index))
        if self._blocks:
            row = min(selected if selected is not None else 0, len(self._blocks) - 1)
            self.block_list.setCurrentRow(max(0, row))
        self._loading = False
        self._select(self.block_list.currentRow())

    def _add(self, kind: str) -> None:
        block: dict[str, Any] = {"type": kind, "content": "", "source_region": {}}
        if kind == "table":
            block["rows"] = [[""]]
        if kind == "figure":
            block.update(source_image_index=0, source_region={"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0})
        row = self.block_list.currentRow()
        insert_at = len(self._blocks) if row < 0 else row + 1
        self._blocks.insert(insert_at, block)
        self._refresh_list(insert_at)
        self.changed.emit()

    def _remove(self) -> None:
        row = self.block_list.currentRow()
        if 0 <= row < len(self._blocks):
            self._blocks.pop(row)
            self._refresh_list(min(row, len(self._blocks) - 1))
            self.changed.emit()

    def _move(self, delta: int) -> None:
        row = self.block_list.currentRow()
        target = row + delta
        if 0 <= row < len(self._blocks) and 0 <= target < len(self._blocks):
            self._blocks[row], self._blocks[target] = self._blocks[target], self._blocks[row]
            self._refresh_list(target)
            self.changed.emit()

    def _select(self, row: int) -> None:
        if self._loading or not 0 <= row < len(self._blocks):
            return
        block = self._blocks[row]
        self._loading = True
        self.kind.setCurrentIndex(max(0, self.kind.findData(block.get("type"))))
        self.content.setPlainText(str(block.get("content") or ""))
        rows = block.get("rows") or []
        if any(isinstance(cell, dict) for cells in rows if isinstance(cells, list) for cell in cells):
            table_text = json.dumps(rows, ensure_ascii=False, indent=2)
        else:
            table_text = "\n".join("\t".join(str(cell) for cell in cells) for cells in rows if isinstance(cells, list))
        self.table_rows.setPlainText(table_text)
        self.source_image_index.setValue(int(block.get("source_image_index", 0)))
        region = block.get("source_region") or {}
        for spin, key, default in zip(self.region_values, ("x", "y", "width", "height"), (0.0, 0.0, 1.0, 1.0), strict=True):
            spin.setValue(float(region.get(key, default)))
        self._loading = False
        self._sync_editor_visibility()

    def _write_current(self, *_args) -> None:
        if self._loading:
            return
        row = self.block_list.currentRow()
        if not 0 <= row < len(self._blocks):
            return
        kind = str(self.kind.currentData())
        block: dict[str, Any] = {"type": kind, "content": self.content.toPlainText(), "source_region": {}}
        if kind == "table":
            raw = self.table_rows.toPlainText().strip()
            try:
                rows = json.loads(raw) if raw.startswith("[") else [line.split("\t") for line in raw.splitlines()]
            except json.JSONDecodeError:
                rows = [[raw]]
            block["rows"] = rows if isinstance(rows, list) else [[raw]]
        if kind == "figure":
            block["source_image_index"] = self.source_image_index.value()
            block["source_region"] = dict(zip(("x", "y", "width", "height"), (spin.value() for spin in self.region_values), strict=True))
        self._blocks[row] = block
        self.block_list.item(row).setText(self._label(block, row))
        self._sync_editor_visibility()
        self.changed.emit()

    def _sync_editor_visibility(self) -> None:
        kind = self.kind.currentData()
        self.table_rows.setVisible(kind == "table")
        self.source_image_index.setVisible(kind == "figure")
        self.region_row.setVisible(kind == "figure")
        self.figure_panel.setVisible(kind == "figure")
        self._refresh_figure_preview()

    def _request_figure_crop(self) -> None:
        row = self.block_list.currentRow()
        if 0 <= row < len(self._blocks):
            self.figure_crop_requested.emit(row)

    def set_source_images(self, paths: list[Path]) -> None:
        self._source_images = [Path(path) for path in paths]
        self.source_image_index.setMaximum(max(0, len(self._source_images) - 1))
        self.crop_figure_button.setEnabled(bool(self._source_images))
        self._refresh_figure_preview()

    def apply_figure_crop(
        self,
        row: int,
        source_image_index: int,
        region: dict[str, float],
    ) -> None:
        if not 0 <= row < len(self._blocks):
            raise DomainError("题图内容块已经不存在")
        block = dict(self._blocks[row])
        if block.get("type") != "figure":
            raise DomainError("当前内容块不是题图")
        block["source_image_index"] = source_image_index
        block["source_region"] = dict(region)
        self._blocks[row] = block
        self._refresh_list(row)
        self.changed.emit()

    def _refresh_figure_preview(self) -> None:
        if not hasattr(self, "figure_preview"):
            return
        row = self.block_list.currentRow()
        if not 0 <= row < len(self._blocks):
            self.figure_preview.setPixmap(QPixmap())
            self.figure_preview.setText("请选择题图内容块")
            return
        block = self._blocks[row]
        index = int(block.get("source_image_index", 0))
        region = block.get("source_region") or {}
        if not 0 <= index < len(self._source_images):
            self.figure_preview.setPixmap(QPixmap())
            self.figure_preview.setText("当前题图没有可用的审核期来源图片")
            return
        image = QImage(str(self._source_images[index]))
        if image.isNull() or not region:
            self.figure_preview.setPixmap(QPixmap())
            self.figure_preview.setText("题图来源或裁剪区域无效")
            return
        rect = QRectF(
            image.width() * float(region.get("x", 0)),
            image.height() * float(region.get("y", 0)),
            image.width() * float(region.get("width", 0)),
            image.height() * float(region.get("height", 0)),
        ).toAlignedRect().intersected(image.rect())
        crop = QPixmap.fromImage(image.copy(rect))
        self.figure_preview.setText("")
        self.figure_preview.setPixmap(
            crop.scaled(
                QSize(640, 220),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def blocks(self) -> list[dict[str, Any]]:
        return normalize_content_blocks(self._blocks)

    def set_blocks(self, blocks: Any) -> None:
        self._blocks = normalize_content_blocks(blocks)
        self._refresh_list()


def _ellipsize_middle(text: str, max_len: int = 30) -> str:
    """Shorten a long file name while keeping both ends, e.g. abc…xyz.jpg."""
    if len(text) <= max_len:
        return text
    keep = max(4, max_len - 1)
    left = keep * 2 // 3
    right = keep - left
    return f"{text[:left]}…{text[-right:]}"

class ImagePreviewLabel(QLabel):
    """Aspect-ratio-preserving preview that follows the available panel size."""

    region_drawn = Signal(dict)

    def __init__(self, empty_text: str, parent=None) -> None:
        super().__init__(empty_text, parent)
        self.empty_text = empty_text
        self.source = QPixmap()
        self.region: dict[str, float] = {}
        self.editable = False
        self._drag_start: QPointF | None = None
        self._drag_mode: str | None = None
        self._region_before_drag: dict[str, float] = {}
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(QSize(360, 260))
        self.padding = 12
        self.setObjectName("ImagePreview")

    def set_path(self, path: Path | None) -> bool:
        self.source = QPixmap(str(path)) if path is not None else QPixmap()
        self._render()
        return not self.source.isNull()

    def set_region(self, region: dict[str, float] | None) -> None:
        self.region = dict(region or {})
        self.update()

    def set_editable(self, editable: bool) -> None:
        self.editable = editable
        self.setCursor(
            Qt.CursorShape.CrossCursor
            if editable
            else Qt.CursorShape.ArrowCursor
        )
        self.setToolTip(
            "拖拽空白处重画区域；拖动蓝框内部可移动；拖动边框控制柄可微调"
            if editable
            else ""
        )

    def clear_preview(self) -> None:
        self.source = QPixmap()
        self._render()

    def resizeEvent(self, event) -> None:  # noqa: ANN001, N802
        super().resizeEvent(event)
        self._render()

    def paintEvent(self, event) -> None:  # noqa: ANN001, N802
        super().paintEvent(event)
        if self.source.isNull() or not self.region:
            return
        displayed = self.pixmap()
        if displayed.isNull():
            return
        left = (self.width() - displayed.width()) / 2
        top = (self.height() - displayed.height()) / 2
        region = QRectF(
            left + displayed.width() * self.region.get("x", 0.0),
            top + displayed.height() * self.region.get("y", 0.0),
            displayed.width() * self.region.get("width", 1.0),
            displayed.height() * self.region.get("height", 1.0),
        )
        image_rect = QRectF(left, top, displayed.width(), displayed.height())
        painter = QPainter(self)
        shade = QColor(15, 23, 42, 105)
        painter.fillRect(
            QRectF(image_rect.left(), image_rect.top(), image_rect.width(), region.top() - image_rect.top()),
            shade,
        )
        painter.fillRect(
            QRectF(image_rect.left(), region.bottom(), image_rect.width(), image_rect.bottom() - region.bottom()),
            shade,
        )
        painter.fillRect(
            QRectF(image_rect.left(), region.top(), region.left() - image_rect.left(), region.height()),
            shade,
        )
        painter.fillRect(
            QRectF(region.right(), region.top(), image_rect.right() - region.right(), region.height()),
            shade,
        )
        painter.setPen(QPen(QColor("#3478F6"), 3))
        painter.drawRect(region)
        if self.editable:
            painter.setPen(QPen(QColor("white"), 1))
            painter.setBrush(QColor("#3478F6"))
            for point in self._handle_points(region).values():
                painter.drawRect(
                    QRectF(point.x() - 4, point.y() - 4, 8, 8)
                )

    def mousePressEvent(self, event) -> None:  # noqa: ANN001, N802
        if (
            not self.editable
            or event.button() != Qt.MouseButton.LeftButton
            or not self._displayed_rect().contains(event.position())
        ):
            super().mousePressEvent(event)
            return
        self._drag_start = event.position()
        self._region_before_drag = dict(self.region)
        self._drag_mode = (
            "draw"
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier
            else self._hit_test(event.position())
        )
        event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: ANN001, N802
        if self._drag_start is None:
            if self.editable:
                self._update_hover_cursor(event.position())
            super().mouseMoveEvent(event)
            return
        if self._drag_mode == "draw":
            self.region = self._normalized_drag_region(
                self._drag_start, event.position()
            )
        else:
            self.region = self._transformed_region(event.position())
        self.update()
        event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: ANN001, N802
        if self._drag_start is None or event.button() != Qt.MouseButton.LeftButton:
            super().mouseReleaseEvent(event)
            return
        displayed = self._displayed_rect()
        region = dict(self.region)
        width_px = region.get("width", 0.0) * displayed.width()
        height_px = region.get("height", 0.0) * displayed.height()
        self._drag_start = None
        self._drag_mode = None
        if width_px < 8 or height_px < 8:
            self.region = self._region_before_drag
            self.update()
            event.accept()
            return
        self.region = region
        self.update()
        if region != self._region_before_drag:
            self.region_drawn.emit(dict(region))
        self._update_hover_cursor(event.position())
        event.accept()

    def _displayed_rect(self) -> QRectF:
        displayed = self.pixmap()
        if displayed.isNull():
            return QRectF()
        return QRectF(
            (self.width() - displayed.width()) / 2,
            (self.height() - displayed.height()) / 2,
            displayed.width(),
            displayed.height(),
        )

    def _normalized_drag_region(
        self, start: QPointF, end: QPointF
    ) -> dict[str, float]:
        displayed = self._displayed_rect()
        if displayed.isEmpty():
            return {}
        x1 = min(displayed.right(), max(displayed.left(), start.x()))
        y1 = min(displayed.bottom(), max(displayed.top(), start.y()))
        x2 = min(displayed.right(), max(displayed.left(), end.x()))
        y2 = min(displayed.bottom(), max(displayed.top(), end.y()))
        return {
            "x": (min(x1, x2) - displayed.left()) / displayed.width(),
            "y": (min(y1, y2) - displayed.top()) / displayed.height(),
            "width": abs(x2 - x1) / displayed.width(),
            "height": abs(y2 - y1) / displayed.height(),
        }

    def _region_rect(self) -> QRectF:
        displayed = self._displayed_rect()
        if displayed.isEmpty() or not self.region:
            return QRectF()
        return QRectF(
            displayed.left() + displayed.width() * self.region.get("x", 0.0),
            displayed.top() + displayed.height() * self.region.get("y", 0.0),
            displayed.width() * self.region.get("width", 1.0),
            displayed.height() * self.region.get("height", 1.0),
        )

    @staticmethod
    def _handle_points(rect: QRectF) -> dict[str, QPointF]:
        return {
            "nw": rect.topLeft(),
            "n": QPointF(rect.center().x(), rect.top()),
            "ne": rect.topRight(),
            "e": QPointF(rect.right(), rect.center().y()),
            "se": rect.bottomRight(),
            "s": QPointF(rect.center().x(), rect.bottom()),
            "sw": rect.bottomLeft(),
            "w": QPointF(rect.left(), rect.center().y()),
        }

    def _hit_test(self, position: QPointF) -> str:
        rect = self._region_rect()
        if rect.isEmpty():
            return "draw"
        for mode, point in self._handle_points(rect).items():
            if abs(position.x() - point.x()) <= 8 and abs(
                position.y() - point.y()
            ) <= 8:
                return mode
        if rect.contains(position):
            return "move"
        return "draw"

    def _update_hover_cursor(self, position: QPointF) -> None:
        cursors = {
            "nw": Qt.CursorShape.SizeFDiagCursor,
            "se": Qt.CursorShape.SizeFDiagCursor,
            "ne": Qt.CursorShape.SizeBDiagCursor,
            "sw": Qt.CursorShape.SizeBDiagCursor,
            "n": Qt.CursorShape.SizeVerCursor,
            "s": Qt.CursorShape.SizeVerCursor,
            "e": Qt.CursorShape.SizeHorCursor,
            "w": Qt.CursorShape.SizeHorCursor,
            "move": Qt.CursorShape.SizeAllCursor,
            "draw": Qt.CursorShape.CrossCursor,
        }
        self.setCursor(cursors[self._hit_test(position)])

    def _transformed_region(self, position: QPointF) -> dict[str, float]:
        displayed = self._displayed_rect()
        if (
            displayed.isEmpty()
            or self._drag_start is None
            or not self._region_before_drag
        ):
            return self._normalized_drag_region(
                self._drag_start or position, position
            )
        mode = self._drag_mode or "move"
        initial = self._region_before_drag
        left = initial["x"]
        top = initial["y"]
        right = left + initial["width"]
        bottom = top + initial["height"]
        dx = (position.x() - self._drag_start.x()) / displayed.width()
        dy = (position.y() - self._drag_start.y()) / displayed.height()
        min_width = 8 / displayed.width()
        min_height = 8 / displayed.height()
        if mode == "move":
            width = initial["width"]
            height = initial["height"]
            return {
                "x": min(1.0 - width, max(0.0, left + dx)),
                "y": min(1.0 - height, max(0.0, top + dy)),
                "width": width,
                "height": height,
            }
        pointer_x = min(
            1.0,
            max(
                0.0,
                (position.x() - displayed.left()) / displayed.width(),
            ),
        )
        pointer_y = min(
            1.0,
            max(
                0.0,
                (position.y() - displayed.top()) / displayed.height(),
            ),
        )
        if "w" in mode:
            left = min(right - min_width, pointer_x)
        if "e" in mode:
            right = max(left + min_width, pointer_x)
        if "n" in mode:
            top = min(bottom - min_height, pointer_y)
        if "s" in mode:
            bottom = max(top + min_height, pointer_y)
        return {
            "x": left,
            "y": top,
            "width": right - left,
            "height": bottom - top,
        }

    def _render(self) -> None:
        if self.source.isNull():
            self.setPixmap(QPixmap())
            self.setText(self.empty_text)
            return
        target = self.size() - QSize(self.padding * 2, self.padding * 2)
        self.setText("")
        self.setPixmap(
            self.source.scaled(
                target,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )


class ClickableImagePreviewLabel(ImagePreviewLabel):
    """Compact image thumbnail that opens its source only on explicit click."""

    clicked = Signal()

    def __init__(self, empty_text: str, parent=None) -> None:
        super().__init__(empty_text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("点击查看大图")

    def mouseReleaseEvent(self, event) -> None:  # noqa: ANN001, N802
        if (
            not self.editable
            and not self.source.isNull()
            and event.button() == Qt.MouseButton.LeftButton
        ):
            self.clicked.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class _RegionRecognitionCompareDialog(QDialog):
    def __init__(
        self,
        proposal: RegionRecognitionProposal,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.apply_new = False
        self.setWindowTitle("比较区域重新识别结果")
        self.resize(1320, 760)
        root = QVBoxLayout(self)
        title = QLabel("区域重新识别完成")
        title.setObjectName("PageTitle")
        root.addWidget(title)
        hint = QLabel(
            "左侧保留重新识别前的内容，右侧是当前蓝框生成的新结果。"
            "只有点击“采用新结果”才会覆盖候选字段。"
        )
        hint.setObjectName("PageHint")
        hint.setWordWrap(True)
        root.addWidget(hint)

        comparison = QHBoxLayout()
        old_card = CardFrame()
        old_card.add_title("原结果")
        old_view = MathContentView()
        self.old_view = old_view
        old_view.set_problem(
            proposal.old_fields,
            tag_names=proposal.old_fields.get("tags", []),
            include_answers=True,
            show_header=False,
            classic=True,
        )
        old_card.body.addWidget(old_view)
        comparison.addWidget(old_card, stretch=1)

        new_card = CardFrame()
        new_card.add_title("区域重新识别结果")
        if proposal.uncertain:
            new_card.add_hint(
                f"AI 报告 {len(proposal.uncertain)} 项不确定内容，请重点核对。"
            )
        new_view = MathContentView()
        self.new_view = new_view
        new_view.set_problem(
            proposal.new_fields,
            tag_names=proposal.new_fields.get("tags", []),
            include_answers=True,
            show_header=False,
            classic=True,
        )
        new_card.body.addWidget(new_view)
        comparison.addWidget(new_card, stretch=1)
        root.addLayout(comparison, stretch=1)

        actions = QHBoxLayout()
        actions.addStretch(1)
        keep_old = QPushButton("保留原结果")
        keep_old.clicked.connect(self.reject)
        adopt = primary_button("采用新结果")
        adopt.clicked.connect(self._adopt)
        actions.addWidget(keep_old)
        actions.addWidget(adopt)
        root.addLayout(actions)
        self._queue_comparison_scale()

    def resizeEvent(self, event) -> None:  # noqa: ANN001, N802
        super().resizeEvent(event)
        self._queue_comparison_scale()

    def _queue_comparison_scale(self) -> None:
        QTimer.singleShot(0, self._apply_comparison_scale)

    def _apply_comparison_scale(self) -> None:
        panel_width = min(self.old_view.width(), self.new_view.width())
        if panel_width <= 1:
            return
        # 680px is the comfortable single-column reading width.  A comparison
        # has half that space, so scale only these two readers down as needed.
        scale = max(0.5, min(0.9, panel_width / 680))
        self.old_view.set_zoom_scale(scale)
        self.new_view.set_zoom_scale(scale)

    def _adopt(self) -> None:
        self.apply_new = True
        self.accept()


class ProblemForm(QWidget):
    """Reusable inline form shared by manual entry and AI confirmation."""

    changed = Signal()
    answer_capture_requested = Signal()

    def __init__(
        self,
        intake: ProblemIntakeService,
        parent=None,
        *,
        clear_user_answer_on_load: bool = False,
        show_render_previews: bool = False,
    ) -> None:
        super().__init__(parent)
        self.intake = intake
        self.clear_user_answer_on_load = clear_user_answer_on_load
        self.show_render_previews = show_render_previews
        self._field_previews: dict[str, tuple[QLabel, MathContentView]] = {}
        self.answer_capture_button: QPushButton | None = None
        self._text_area_resize_timer = QTimer(self)
        self._text_area_resize_timer.setSingleShot(True)
        self._text_area_resize_timer.timeout.connect(
            self._resize_text_areas_to_content
        )
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        self._form_target: QVBoxLayout = root
        self.render_view: MathContentView | None = None
        if self.show_render_previews:
            # 左右分栏：左侧实时渲染，右侧专注文字修改
            self.splitter = QSplitter(Qt.Orientation.Horizontal, self)
            root.addWidget(self.splitter)

            render_panel = QWidget()
            render_panel.setObjectName("ProblemRenderPanel")
            render_layout = QVBoxLayout(render_panel)
            render_layout.setContentsMargins(0, 0, 12, 0)
            render_layout.setSpacing(8)
            self.render_view = MathContentView()
            # 预览框缩放独立于全局“预览缩放”设置，锁定在 100%
            self.render_view.set_fixed_zoom_scale(1.0)
            self.render_view.set_adaptive_content_height(1200)
            render_layout.addWidget(self.render_view)
            self.splitter.addWidget(render_panel)

            editor_panel = QScrollArea()
            editor_panel.setWidgetResizable(True)
            editor_panel.setFrameShape(QScrollArea.Shape.NoFrame)
            editor_host = QWidget()
            self._form_target = QVBoxLayout(editor_host)
            self._form_target.setContentsMargins(0, 0, 0, 0)
            self._form_target.setSpacing(12)
            editor_panel.setWidget(editor_host)
            self.splitter.addWidget(editor_panel)
            # 题目预览与编辑字段横向占比 1:1
            self.splitter.setStretchFactor(0, 1)
            self.splitter.setStretchFactor(1, 1)
            self.splitter.setSizes([540, 540])

        basic = CardFrame()
        basic.add_title("基本归属")
        form = QFormLayout()
        form.setSpacing(10)
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("例如：换元积分中遗漏绝对值")
        self.subject = QComboBox()
        self.chapter = QComboBox()
        describe_field(self.title_edit, "题目标题")
        describe_field(self.subject, "题目科目")
        describe_field(
            self.chapter,
            "题目章节",
            "选择已有章节，或确认 AI 建议的新章节",
        )
        self._taxonomy_proposal: dict[str, Any] | None = None
        self.taxonomy_hint = QLabel()
        self.taxonomy_hint.setObjectName("MutedLabel")
        self.taxonomy_hint.setWordWrap(True)
        self.taxonomy_hint.hide()
        self.problem_type = QLineEdit()
        self.problem_type.setPlaceholderText("例如：选择题 / 计算题")
        self.priority = QSpinBox()
        self.priority.setRange(1, 5)
        self.priority.setValue(3)
        self.source_book = QLineEdit()
        self.source_year = QLineEdit()
        self.original_number = QLineEdit()
        self.tags = QLineEdit()
        self.tags.setPlaceholderText("多个标签用逗号分隔")
        describe_field(self.problem_type, "题型")
        describe_field(self.priority, "题目优先级")
        describe_field(self.source_book, "来源书籍")
        describe_field(self.source_year, "来源年份")
        describe_field(self.original_number, "原题题号")
        describe_field(self.tags, "题目标签", "多个标签使用逗号分隔")
        form.addRow("标题", self.title_edit)
        form.addRow("科目", self.subject)
        form.addRow("章节", self.chapter)
        form.addRow("", self.taxonomy_hint)
        form.addRow("题型", self.problem_type)
        form.addRow("优先级", self.priority)
        form.addRow("来源书籍", self.source_book)
        form.addRow("来源年份", self.source_year)
        form.addRow("原题题号", self.original_number)
        form.addRow("标签", self.tags)
        basic.body.addLayout(form)
        self._form_target.addWidget(basic)

        content = CardFrame()
        content.add_title("题目内容")
        self.question = self._text_area("题干 Markdown / 文本", 150)
        self._question_latex = ""
        describe_field(self.question, "题干")
        content.body.addWidget(QLabel("题干"))
        content.body.addWidget(self.question)
        self.content_blocks = ContentBlocksEditor()
        if not self.show_render_previews:
            self.content_blocks.hide()
        if self.show_render_previews:
            blocks_label = QLabel("结构化内容块（按原题顺序，可编辑表格、题图区域并拖动顺序）")
            blocks_label.setObjectName("MutedLabel")
            content.body.addWidget(blocks_label)
            content.body.addWidget(self.content_blocks)
        self._form_target.addWidget(content)

        answer = CardFrame()
        answer.add_title("作答与解析")
        self.user_answer = self._text_area("可选：填写你的作答；也可留空。", 90)
        self.correct_answer = self._text_area("正确答案", 90)
        self.solution = self._text_area("完整解析", 130)
        self.notes = self._text_area("可选：填写备注；也可留空。", 80)
        describe_field(self.user_answer, "我的作答")
        describe_field(self.correct_answer, "正确答案")
        describe_field(self.solution, "题目解析")
        describe_field(self.notes, "题目备注")
        for label, editor in (
            ("我的作答", self.user_answer),
            ("正确答案", self.correct_answer),
            ("解析", self.solution),
            ("备注", self.notes),
        ):
            if label == "我的作答" and self.show_render_previews:
                answer_label = QHBoxLayout()
                answer_label.addWidget(QLabel(label))
                answer_label.addStretch(1)
                capture = ghost_button("从图片识别作答")
                capture.setObjectName("AnswerCaptureButton")
                capture.setToolTip("从图片识别我的作答")
                capture.setAccessibleName("从图片识别我的作答")
                bind_icon(capture, "camera")
                capture.clicked.connect(self.answer_capture_requested.emit)
                self.answer_capture_button = capture
                answer_label.addWidget(capture)
                answer.body.addLayout(answer_label)
            else:
                answer.body.addWidget(QLabel(label))
            answer.body.addWidget(editor)
        self._form_target.addWidget(answer)
        self._form_target.addStretch(1)

        if self.show_render_previews and self.render_view is not None:
            self._preview_scroll_filters: list[QObject] = []
            for widget, key in (
                (self.title_edit, "question"),
                (self.question, "question"),
                (self.user_answer, "user_answer"),
                (self.correct_answer, "correct_answer"),
                (self.solution, "solution"),
                (self.notes, "notes"),
                (self.tags, "question"),
            ):
                self._connect_preview_scroll(widget, key)

        self.subject.currentIndexChanged.connect(self._reload_chapters)
        self.chapter.currentIndexChanged.connect(
            self._sync_taxonomy_hint_visibility
        )
        self.reload_catalog()
        self._connect_change_signals()
        set_tab_order_chain(
            self.title_edit,
            self.subject,
            self.chapter,
            self.problem_type,
            self.priority,
            self.source_book,
            self.source_year,
            self.original_number,
            self.tags,
            self.question,
            self.answer_capture_button,
            self.user_answer,
            self.correct_answer,
            self.solution,
            self.notes,
        )
        self._update_answer_capture_button_mode()

    def _connect_change_signals(self) -> None:
        def notify(*_args) -> None:
            self.changed.emit()
            self.refresh_render_previews()

        for editor in (
            self.title_edit,
            self.problem_type,
            self.source_book,
            self.source_year,
            self.original_number,
            self.tags,
        ):
            editor.textChanged.connect(notify)
        for editor in (
            self.question,
            self.user_answer,
            self.correct_answer,
            self.solution,
            self.notes,
        ):
            editor.textChanged.connect(notify)
            editor.textChanged.connect(self._queue_text_area_resize)
        self.subject.currentIndexChanged.connect(notify)
        self.chapter.currentIndexChanged.connect(notify)
        self.priority.valueChanged.connect(notify)
        self.content_blocks.changed.connect(notify)
        self.content_blocks.changed.connect(self.refresh_render_previews)

    @staticmethod
    def _text_area(placeholder: str, height: int) -> QTextEdit:
        editor = FocusAwareTextEdit()
        editor.setPlaceholderText(placeholder)
        editor.setProperty("contentMaxHeight", max(150, height * 2))
        editor.setUndoRedoEnabled(True)
        return editor

    def _queue_text_area_resize(self) -> None:
        self._text_area_resize_timer.start(0)

    def _add_field_preview(
        self,
        layout: QVBoxLayout,
        key: str,
        title: str,
    ) -> None:
        label = QLabel(title)
        label.setObjectName("MutedLabel")
        preview = MathContentView()
        preview.set_adaptive_content_height(320)
        label.hide()
        preview.hide()
        self._field_previews[key] = (label, preview)
        layout.addWidget(label)
        layout.addWidget(preview)

    def refresh_render_previews(self) -> None:
        if not self.show_render_previews or self.render_view is None:
            return
        question = self.question.toPlainText()
        latex = self._question_latex.strip()
        if latex:
            question = f"{question}\n\n\\[{latex}\\]".strip()
        blocks = self.content_blocks.blocks
        tags = [tag.strip() for tag in self.tags.text().split(",") if tag.strip()]
        self.render_view.set_problem(
            {
                "title": self.title_edit.text(),
                "question": question,
                "content_blocks": blocks,
                "user_answer": self.user_answer.toPlainText(),
                "correct_answer": self.correct_answer.toPlainText(),
                "solution_markdown": self.solution.toPlainText(),
                "notes": self.notes.toPlainText(),
            },
            tag_names=tags,
            show_header=False,
            classic=True,
        )

    def _resize_text_areas_to_content(self) -> None:
        for editor in (
            self.question,
            self.user_answer,
            self.correct_answer,
            self.solution,
            self.notes,
        ):
            viewport_width = editor.viewport().width()
            if viewport_width <= 1:
                continue
            document = editor.document()
            document.setTextWidth(viewport_width)
            line_height = editor.fontMetrics().lineSpacing()
            content_height = math.ceil(
                document.documentLayout().documentSize().height()
            )
            one_line_height = line_height + 24
            target_height = max(one_line_height, content_height + 24)
            # 文本框随内容长高，由外层面板滚动；不再出现内嵌滚动条
            editor.setFixedHeight(target_height)
            editor.setVerticalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAlwaysOff
            )

    def resizeEvent(self, event) -> None:  # noqa: ANN001, N802
        super().resizeEvent(event)
        self._update_answer_capture_button_mode()
        self._queue_text_area_resize()

    def _update_answer_capture_button_mode(self) -> None:
        button = self.answer_capture_button
        if button is None:
            return
        compact = self.width() < 520
        button.setText("" if compact else "从图片识别作答")
        button.setObjectName("IconButton" if compact else "AnswerCaptureButton")
        if compact:
            button.setFixedSize(32, 32)
        else:
            button.setMinimumSize(0, 0)
            button.setMaximumSize(16777215, 16777215)

    def _connect_preview_scroll(self, widget: QWidget, key: str) -> None:
        """点击编辑框时，把左侧预览滚动到该字段对应的小节。"""
        preview = self.render_view

        class _FocusScrollFilter(QObject):
            def eventFilter(self, obj, event):  # noqa: N802, ANN001
                if event.type() == QEvent.Type.FocusIn:
                    preview.scroll_to_section(key)
                return False

        if preview is not None:
            filter_obj = _FocusScrollFilter(self)
            widget.installEventFilter(filter_obj)
            self._preview_scroll_filters.append(filter_obj)

    def reload_catalog(self) -> None:
        current = self.subject.currentData()
        self.subject.blockSignals(True)
        self.subject.clear()
        self.subject.addItem("（未指定）", None)
        for subject in self.intake.app.list_subjects():
            self.subject.addItem(subject.name, subject.id)
        index = self.subject.findData(current)
        self.subject.setCurrentIndex(index if index >= 0 else 0)
        self.subject.blockSignals(False)
        self._reload_chapters()

    def _reload_chapters(self) -> None:
        current = self.chapter.currentData()
        self._taxonomy_proposal = None
        self.taxonomy_hint.hide()
        self.chapter.clear()
        self.chapter.addItem("（未指定）", None)
        subject_id = self.subject.currentData()
        if subject_id:
            for choice in self.intake.app.list_category_choices():
                if choice.subject_id == subject_id and choice.chapter_id is not None:
                    self.chapter.addItem(
                        " / ".join(choice.chapter_path),
                        choice.chapter_id,
                    )
        index = self.chapter.findData(current)
        self.chapter.setCurrentIndex(index if index >= 0 else 0)

    def _sync_taxonomy_hint_visibility(self) -> None:
        proposal_marker = self.chapter.itemData(
            self.chapter.currentIndex(),
            Qt.ItemDataRole.UserRole + 1,
        )
        self.taxonomy_hint.setVisible(
            bool(proposal_marker and self._taxonomy_proposal is not None)
        )

    @staticmethod
    def _optional(text: str) -> str | None:
        value = text.strip()
        return value or None

    def values(self) -> dict[str, Any]:
        values = {
            "title": self._optional(self.title_edit.text()),
            "subject_id": self.subject.currentData(),
            "chapter_id": self.chapter.currentData(),
            "problem_type": self._optional(self.problem_type.text()),
            "priority": self.priority.value(),
            "source_book": self._optional(self.source_book.text()),
            "source_year": self._optional(self.source_year.text()),
            "original_number": self._optional(self.original_number.text()),
            "question_markdown": self.question.toPlainText(),
            "question_latex": self._question_latex,
            "content_blocks": self.content_blocks.blocks(),
            "user_answer": self.user_answer.toPlainText(),
            "correct_answer": self.correct_answer.toPlainText(),
            "solution_markdown": self.solution.toPlainText(),
            "error_analysis": "",
            "notes": self.notes.toPlainText(),
        }
        proposal_marker = self.chapter.itemData(
            self.chapter.currentIndex(),
            Qt.ItemDataRole.UserRole + 1,
        )
        if proposal_marker and self._taxonomy_proposal is not None:
            values["chapter_id"] = None
            values["taxonomy_proposal"] = dict(self._taxonomy_proposal)
        return values

    def tag_names(self) -> list[str]:
        text = self.tags.text().replace("，", ",")
        return [part.strip() for part in text.split(",") if part.strip()]

    def set_values(self, values: dict[str, Any]) -> None:
        self.reload_catalog()
        self.title_edit.setText(str(values.get("title") or ""))
        subject_id = values.get("subject_id")
        proposal = values.get("taxonomy_proposal")
        proposal_subject = (
            str(proposal.get("subject_name") or "")
            if isinstance(proposal, dict)
            else ""
        )
        subject_name = values.get("subject_name") or proposal_subject
        if not subject_id and subject_name:
            for index in range(self.subject.count()):
                if self.subject.itemText(index) == str(subject_name):
                    subject_id = self.subject.itemData(index)
                    break
        index = self.subject.findData(subject_id)
        self.subject.setCurrentIndex(index if index >= 0 else 0)
        self._reload_chapters()
        chapter_id = values.get("chapter_id")
        if not chapter_id and values.get("chapter_name"):
            expected_name = str(values["chapter_name"])
            for idx in range(self.chapter.count()):
                label = self.chapter.itemText(idx)
                if label == expected_name or label.rsplit(" / ", 1)[-1] == expected_name:
                    chapter_id = self.chapter.itemData(idx)
                    break
        chapter_index = self.chapter.findData(chapter_id)
        if (
            not chapter_id
            and isinstance(proposal, dict)
            and str(proposal.get("chapter_name") or "").strip()
        ):
            if not subject_id and proposal_subject:
                self.subject.insertItem(1, f"AI 建议新建：{proposal_subject}", None)
                self.subject.setItemData(
                    1, True, Qt.ItemDataRole.UserRole + 1
                )
                self.subject.setCurrentIndex(1)
            self._taxonomy_proposal = dict(proposal)
            chapter_name = str(proposal["chapter_name"]).strip()
            self.chapter.insertItem(1, f"AI 建议新建：{chapter_name}", None)
            self.chapter.setItemData(
                1, True, Qt.ItemDataRole.UserRole + 1
            )
            self.chapter.setCurrentIndex(1)
            confidence = proposal.get("confidence")
            confidence_text = (
                f" · 置信度 {float(confidence):.0%}"
                if isinstance(confidence, (int, float))
                else ""
            )
            reason = str(proposal.get("reason") or "请确认后创建")
            target = "科目及该章节" if not subject_id else "该章节"
            self.taxonomy_hint.setText(
                f"章节建议{confidence_text}：{reason}。"
                f"确认入库时会创建{target}；也可以改选现有章节或“未指定”。"
            )
            self._sync_taxonomy_hint_visibility()
        else:
            self.chapter.setCurrentIndex(chapter_index if chapter_index >= 0 else 0)
        self.problem_type.setText(str(values.get("problem_type") or ""))
        try:
            priority = int(values.get("priority") or 3)
        except (TypeError, ValueError):
            priority = 3
        self.priority.setValue(max(1, min(5, priority)))
        self.source_book.setText(str(values.get("source_book") or ""))
        self.source_year.setText(str(values.get("source_year") or ""))
        self.original_number.setText(str(values.get("original_number") or ""))
        self.question.setPlainText(str(values.get("question_markdown") or ""))
        self._question_latex = str(values.get("question_latex") or "")
        self.content_blocks.set_blocks(values.get("content_blocks") or [])
        self.user_answer.setPlainText(
            ""
            if self.clear_user_answer_on_load
            else str(values.get("user_answer") or "")
        )
        self.correct_answer.setPlainText(str(values.get("correct_answer") or ""))
        self.solution.setPlainText(str(values.get("solution_markdown") or ""))
        self.notes.setPlainText(str(values.get("notes") or ""))
        tags = values.get("tags")
        self.tags.setText(", ".join(str(tag) for tag in tags) if isinstance(tags, list) else "")
        self._queue_text_area_resize()

    def clear(self) -> None:
        self.set_values({})


class FigureCropDialog(QDialog):
    """Visual review-only crop editor for one formal figure block."""

    def __init__(
        self,
        source_images: list[Path],
        *,
        source_image_index: int = 0,
        region: dict[str, float] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        if not source_images:
            raise DomainError("当前候选没有可用于裁剪的来源图片")
        self.source_images = [Path(path) for path in source_images]
        self.setWindowTitle("调整题图裁剪")
        self.setMinimumSize(760, 620)
        root = QVBoxLayout(self)
        hint = QLabel(
            "拖动蓝框内部可移动，拖动边缘控制柄可缩放；在框外拖拽可重新绘制。"
        )
        hint.setObjectName("MutedLabel")
        hint.setWordWrap(True)
        root.addWidget(hint)
        source_row = QHBoxLayout()
        source_row.addWidget(QLabel("来源图片"))
        self.source = QComboBox()
        for index, path in enumerate(self.source_images):
            self.source.addItem(f"第 {index + 1} 张 · {path.name}", index)
        source_row.addWidget(self.source, stretch=1)
        root.addLayout(source_row)
        self.preview = ImagePreviewLabel("无法读取来源图片")
        self.preview.set_editable(True)
        root.addWidget(self.preview, stretch=1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("采用此裁剪")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._accept_crop)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self.source.currentIndexChanged.connect(self._source_changed)
        initial = max(0, min(source_image_index, len(self.source_images) - 1))
        self.source.setCurrentIndex(initial)
        self.preview.set_path(self.source_images[initial])
        self.preview.set_region(
            region
            or {"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0}
        )

    def _source_changed(self, index: int) -> None:
        if not 0 <= index < len(self.source_images):
            return
        self.preview.set_path(self.source_images[index])
        self.preview.set_region(
            {"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0}
        )

    def _accept_crop(self) -> None:
        region = self.preview.region
        if not region or region.get("width", 0) <= 0 or region.get("height", 0) <= 0:
            return
        self.accept()

    def result_crop(self) -> tuple[int, dict[str, float]]:
        return self.source.currentIndex(), dict(self.preview.region)


class IntakePage(QWidget):
    problem_committed = Signal(str)
    status_message = Signal(str)
    dashboard_requested = Signal()
    library_requested = Signal()
    open_problem_requested = Signal(str)
    ai_review_ready = Signal(str, int)
    review_queue_requested = Signal()

    def __init__(
        self,
        intake: ProblemIntakeService,
        coordinator: AIJobCoordinator | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.intake = intake
        self.coordinator = coordinator or AIJobCoordinator(intake.ai, self)
        self._prompt_templates = load_intake_prompt_templates(
            self.intake.runtime.paths.root
        )
        self.manual_images: list[Path] = []
        self.ai_files: list[Path] = []
        self.ai_job_id: str | None = None
        self._cancelled_ai_jobs: set[str] = set()
        self._from_task_queue = False
        self._pending_answer_job_id: str | None = None
        self.coordinator.register_handler("user_answer", self._run_user_answer_job)
        self.region_worker: RegionRecognitionWorker | None = None
        self.answer_recognition_worker: UserAnswerRecognitionWorker | None = None
        self.answer_image: Path | None = None
        self.answer_images: list[Path] = []
        self._ai_image_viewer: ImageViewerDialog | None = None
        self._answer_image_viewer: ImageViewerDialog | None = None
        self.ai_candidates: list[IntakeCandidate] = []
        self.candidate_index = 0
        self.last_problem_id: str | None = None
        self._restoring_manual_draft = False
        self._ai_live_stage_label = ""
        self._ai_live_stage_history: list[str] = []

        self.manual_draft_timer = QTimer(self)
        self.manual_draft_timer.setSingleShot(True)
        self.manual_draft_timer.setInterval(700)
        self.manual_draft_timer.timeout.connect(self._save_manual_draft)

        self.preview_timer = QTimer(self)
        self.preview_timer.setSingleShot(True)
        self.preview_timer.setInterval(180)
        self.preview_timer.timeout.connect(self._refresh_ai_preview)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_manual())
        self.stack.addWidget(self._build_ai_upload())
        self.stack.addWidget(self._build_processing())
        self.stack.addWidget(self._build_confirmation())
        self.stack.addWidget(self._build_done())
        self.stack.addWidget(self._build_answer_capture())
        root.addWidget(self.stack)
        self._apply_image_intake_layout()

        self.progress_timer = QTimer(self)
        self.progress_timer.setInterval(500)
        self.progress_timer.timeout.connect(self._poll_progress)
        self._restore_manual_draft()
        self._restore_existing_session()
        self.coordinator.job_progress.connect(self._on_coordinator_progress)
        self.coordinator.job_finished.connect(self._on_ai_done)
        self.coordinator.job_failed.connect(self._on_ai_failed)

    @staticmethod
    def _scroll(widget: QWidget) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(widget)
        return scroll

    def _header(
        self,
        title_text: str,
        hint_text: str,
        back_slot,
        *,
        back_tooltip: str = "返回工作台",
    ) -> PageHeader:
        header = PageHeader(title_text, hint_text)
        back = IconButton("chevron-left", back_tooltip, header)
        back.clicked.connect(back_slot)
        header.add_leading(back)
        return header

    def _page(self) -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        page.setObjectName("PageRoot")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)
        return page, layout

    @staticmethod
    def _ai_step_bar(current_step: int) -> WorkflowStepBar:
        return WorkflowStepBar(("上传与识别", "审核并入库"), current_step)

    def _build_manual(self) -> QWidget:
        page, layout = self._page()
        layout.addWidget(
            self._header(
                "手动录题",
                "内容会自动保存为草稿；只有点击“确认入库”才会创建正式题目。",
                self.dashboard_requested.emit,
            )
        )
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 8, 0)
        self.manual_form = ProblemForm(self.intake)
        self.manual_form.changed.connect(self._queue_manual_draft)
        body_layout.addWidget(self.manual_form)

        assets = CardFrame()
        assets.add_title("原始图片")
        assets.add_hint("可选。原图会按内容寻址保存并保持不可覆盖。")
        self.manual_asset_list = QListWidget()
        self.manual_asset_list.setMaximumHeight(110)
        add_asset = QPushButton("添加图片")
        add_asset.clicked.connect(self._add_manual_images)
        remove_asset = QPushButton("移除选中")
        remove_asset.clicked.connect(self._remove_manual_images)
        assets.body.addWidget(self.manual_asset_list)
        asset_buttons = QHBoxLayout()
        asset_buttons.addWidget(add_asset)
        asset_buttons.addWidget(remove_asset)
        asset_buttons.addStretch(1)
        assets.body.addLayout(asset_buttons)
        body_layout.addWidget(assets)
        layout.addWidget(self._scroll(body), stretch=1)

        actions = QHBoxLayout()
        actions.addWidget(QLabel("草稿自动保存，关闭程序后仍可继续"))
        actions.addStretch(1)
        cancel = QPushButton("清空表单")
        cancel.clicked.connect(self._clear_manual)
        submit = primary_button("确认入库")
        submit.clicked.connect(self._commit_manual)
        actions.addWidget(cancel)
        actions.addWidget(submit)
        layout.addLayout(actions)
        return page

    def _build_ai_upload(self) -> QWidget:
        page, layout = self._page()
        layout.addWidget(
            self._header(
                "AI 录题 · 上传",
                "上传题目图片，可补充定位说明；识别结果将在确认后写入题库。",
                self.dashboard_requested.emit,
            )
        )
        self.ai_upload_steps = self._ai_step_bar(0)
        layout.addWidget(self.ai_upload_steps)
        self.ai_task_surface = QFrame()
        self.ai_task_surface.setObjectName("IntakeStatusSurface")
        task_row = QHBoxLayout(self.ai_task_surface)
        task_row.setContentsMargins(14, 10, 14, 10)
        self.ai_task_status = QLabel("AI 任务正在排队")
        self.ai_task_status.setWordWrap(True)
        self.ai_task_button = primary_button("查看识别进度")
        self.ai_task_button.clicked.connect(self._open_ai_task_button)
        task_row.addWidget(self.ai_task_status, stretch=1)
        task_row.addWidget(self.ai_task_button)
        self.ai_task_surface.hide()
        layout.addWidget(self.ai_task_surface)
        upload = CardFrame()
        upload.setObjectName("IntakePrimarySurface")
        upload.add_title("1. 添加图片")
        upload_content_host = QWidget()
        self.ai_upload_content_host = upload_content_host
        upload_content = QHBoxLayout(upload_content_host)
        self.ai_upload_content_layout = upload_content
        upload_content.setContentsMargins(0, 0, 0, 0)
        upload_content.setSpacing(12)
        file_actions = QVBoxLayout()
        self.ai_upload_file_actions = file_actions
        file_actions.setSpacing(8)
        add = primary_button("选择图片")
        add.setFixedWidth(104)
        add.clicked.connect(self._add_ai_files)
        remove = QPushButton("移除选中")
        remove.setFixedWidth(104)
        remove.clicked.connect(self._remove_ai_files)
        file_actions.addStretch(1)
        file_actions.addWidget(add)
        file_actions.addWidget(remove)
        file_actions.addStretch(1)
        upload_content.addLayout(file_actions)
        self.ai_file_list = QListWidget()
        self.ai_file_list.setObjectName("UploadFileList")
        self.ai_file_list.setAccessibleName("待识别图片")
        self.ai_file_list.setAccessibleDescription("使用方向键浏览已选择的图片")
        self.ai_file_list.setMouseTracking(True)
        self.ai_file_list.setItemDelegate(
            SoftItemDelegate(
                self.ai_file_list,
                radius=10,
                horizontal_margin=4,
                vertical_margin=4,
            )
        )
        self.ai_file_list.setViewMode(QListView.ViewMode.IconMode)
        self.ai_file_list.setFlow(QListView.Flow.LeftToRight)
        self.ai_file_list.setWrapping(False)
        self.ai_file_list.setResizeMode(QListView.ResizeMode.Adjust)
        self.ai_file_list.setMovement(QListView.Movement.Static)
        self.ai_file_list.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.ai_file_list.setIconSize(QSize(144, 104))
        self.ai_file_list.setGridSize(QSize(170, 138))
        self.ai_file_list.setFixedHeight(156)
        self.ai_file_list.setFrameShape(QFrame.Shape.StyledPanel)
        self.ai_file_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.ai_file_list.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.ai_file_list.itemClicked.connect(self._toggle_ai_file_viewer)
        upload_content.addWidget(
            self.ai_file_list,
            stretch=1,
            alignment=Qt.AlignmentFlag.AlignTop,
        )
        upload.body.addWidget(upload_content_host)
        layout.addWidget(upload)

        mode = CardFrame()
        mode.setObjectName("IntakeSecondarySurface")
        mode.add_title("2. 选择识别方式")
        self.ai_recognition_mode = QComboBox()
        describe_field(self.ai_recognition_mode, "AI 识别方式")
        self.ai_recognition_mode.addItem("自动（逐图识别，仅提示结构建议）", "auto")
        self.ai_recognition_mode.addItem("一图一题", "one_to_one")
        self.ai_recognition_mode.addItem("一图多题", "one_to_many")
        self.ai_recognition_mode.addItem("多图一题（按上传顺序）", "many_to_one")
        mode.body.addWidget(self.ai_recognition_mode)
        layout.addWidget(mode)

        prompt = CardFrame()
        prompt.setObjectName("IntakeSecondarySurface")
        prompt.add_title("3. 告诉 AI 如何定位题目")
        self.ai_instruction = QTextEdit()
        describe_field(self.ai_instruction, "AI 题目定位说明")
        self.ai_instruction.setPlaceholderText(
            "例如：画红圈的是目标错题；蓝色手写内容是我的作答；不要提取页脚答案。"
        )
        self.ai_instruction.setMaximumHeight(120)
        prompt.body.addWidget(self.ai_instruction)
        self._template_row = QHBoxLayout()
        self._template_row.setSpacing(8)
        prompt.body.addLayout(self._template_row)
        self._rebuild_prompt_templates()
        manage = QHBoxLayout()
        manage.setSpacing(8)
        self._add_template_button = ghost_button("新增")
        self._add_template_button.setToolTip("添加一条常用提示语，之后可以点击一键追加到输入框")
        self._add_template_button.clicked.connect(
            lambda _checked=False: self._add_prompt_template()
        )
        self._edit_template_button = ghost_button("编辑")
        self._edit_template_button.setToolTip("选择一条提示词并修改内容")
        self._edit_template_button.clicked.connect(
            lambda _checked=False: self._edit_prompt_template()
        )
        self._delete_template_button = ghost_button("删除")
        self._delete_template_button.setToolTip("选择一条提示词并删除")
        self._delete_template_button.clicked.connect(
            lambda _checked=False: self._delete_prompt_template()
        )
        manage.addWidget(self._add_template_button)
        manage.addWidget(self._edit_template_button)
        manage.addWidget(self._delete_template_button)
        manage.addStretch(1)
        prompt.body.addLayout(manage)
        layout.addWidget(prompt)
        layout.addStretch(1)

        start_row = QHBoxLayout()
        self.ai_use_cache = QCheckBox("使用历史识别缓存")
        self.ai_use_cache.setAccessibleDescription(
            "关闭后重新请求 AI，并更新相同图片的识别缓存"
        )
        self.ai_use_cache.setChecked(True)
        self.ai_use_cache.setToolTip("关闭后将重新请求 AI，并用新结果更新同一图片的缓存")
        self.ai_cache_hint = QLabel()
        self.ai_cache_hint.setObjectName("MutedLabel")
        clear_cache = ghost_button("清空识别缓存")
        clear_cache.clicked.connect(self._clear_recognition_cache)
        start_row.addWidget(self.ai_use_cache)
        start_row.addWidget(self.ai_cache_hint)
        start_row.addWidget(clear_cache)
        self.ai_config_hint = QLabel()
        self.ai_config_hint.setObjectName("PageHint")
        start_row.addWidget(self.ai_config_hint)
        start_row.addStretch(1)
        self.ai_start_button = primary_button("开始识别")
        self.ai_start_button.clicked.connect(self._start_ai)
        start_row.addWidget(self.ai_start_button)
        set_tab_order_chain(
            self.ai_file_list,
            self.ai_recognition_mode,
            self.ai_instruction,
            self.ai_use_cache,
            self.ai_start_button,
        )
        layout.addLayout(start_row)
        return page

    def _processing_back(self) -> None:
        """返回：从任务队列进入时直接回任务列表，否则回到上传页。"""
        if self._from_task_queue:
            self._from_task_queue = False
            self.library_requested.emit()
            return
        self.show_ai_upload()

    def _build_processing(self) -> QWidget:
        page, layout = self._page()
        processing_header = self._header(
            "AI 输出实时预览",
            "这里展示模型公开输出和处理阶段；返回上传页不会中断任务。",
            self._processing_back,
            back_tooltip="返回上传页",
        )
        self.processing_back = processing_header.findChild(IconButton)
        layout.addWidget(processing_header)
        self.ai_processing_steps_bar = self._ai_step_bar(0)
        layout.addWidget(self.ai_processing_steps_bar)
        card = CardFrame()
        card.setObjectName("IntakeStatusSurface")
        card.add_title("模型输出")
        self.processing_status = card.add_hint("正在准备任务…")
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        card.body.addWidget(self.progress_bar)
        self.processing_steps = QLabel("图片提交后，AI 输出会在下方实时展示。")
        self.processing_steps.setWordWrap(True)
        card.body.addWidget(self.processing_steps)
        self.processing_preview = QTextEdit()
        self.processing_preview.setObjectName("AIQuickPreview")
        self.processing_preview.setReadOnly(True)
        self.processing_preview.setMinimumHeight(132)
        self.processing_preview.setMaximumHeight(220)
        self.processing_preview.setPlaceholderText("等待 AI 返回第一个内容…")
        card.body.addWidget(self.processing_preview)
        self.processing_error = QLabel("")
        self.processing_error.setWordWrap(True)
        self.processing_error.setObjectName("DangerLabel")
        card.body.addWidget(self.processing_error)
        actions = QHBoxLayout()
        self.processing_cancel_button = danger_button("取消后台任务")
        self.processing_cancel_button.clicked.connect(self._cancel_ai)
        self.processing_retry = primary_button("重新尝试失败项")
        self.processing_retry.clicked.connect(self._retry_failed_ai)
        self.processing_retry.setVisible(False)
        actions.addWidget(self.processing_cancel_button)
        actions.addWidget(self.processing_retry)
        actions.addStretch(1)
        card.body.addLayout(actions)
        layout.addWidget(card)
        layout.addStretch(1)
        return page

    def _build_confirmation(self) -> QWidget:
        page, layout = self._page()
        layout.addWidget(
            self._header(
                "AI 录题 · 确认结果",
                "",
                self.dashboard_requested.emit,
            )
        )
        self.ai_confirmation_steps = self._ai_step_bar(1)
        layout.addWidget(self.ai_confirmation_steps)
        self.ai_result_tabs = QTabWidget()
        self.ai_result_tabs.setObjectName("AIResultTabs")

        preview_host = QWidget()
        preview_layout = QVBoxLayout(preview_host)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        self.ai_result_preview = MathContentView()
        self.ai_result_preview.setMinimumWidth(520)
        self.ai_result_preview.set_adaptive_content_height(520)
        preview_layout.addWidget(self.ai_result_preview, stretch=1)
        self.ai_result_tabs.addTab(preview_host, "阅读预览")

        form_host = QWidget()
        form_layout = QVBoxLayout(form_host)
        form_layout.setContentsMargins(0, 0, 8, 0)
        self.ai_form = ProblemForm(
            self.intake,
            clear_user_answer_on_load=True,
            show_render_previews=True,
        )
        self.ai_form.changed.connect(self._queue_ai_preview)
        self.ai_form.answer_capture_requested.connect(self._open_answer_capture)
        self.ai_form.content_blocks.figure_crop_requested.connect(
            self._edit_figure_crop
        )
        form_layout.addWidget(self.ai_form)
        self.ai_result_tabs.addTab(form_host, "编辑字段")
        self.ai_result_tabs.addTab(
            self._build_image_tools_tab(), "原图范围与重识别"
        )
        self.ai_result_tabs.currentChanged.connect(self._on_ai_result_tab_changed)
        confirmation_surface = QFrame()
        confirmation_surface.setObjectName("IntakeConfirmationSurface")
        confirmation_layout = QVBoxLayout(confirmation_surface)
        confirmation_layout.setContentsMargins(16, 14, 16, 14)
        confirmation_layout.addWidget(self.ai_result_tabs)
        self.ai_confirmation_surface = confirmation_surface
        layout.addWidget(confirmation_surface, stretch=1)

        action_bar = QFrame()
        action_bar.setObjectName("IntakeActionBar")
        actions = QHBoxLayout(action_bar)
        actions.setContentsMargins(16, 10, 16, 10)
        self.ai_previous_button = QPushButton("上一题")
        self.ai_previous_button.clicked.connect(lambda: self._move_candidate(-1))
        self.ai_next_button = QPushButton("下一题")
        self.ai_next_button.clicked.connect(lambda: self._move_candidate(1))
        self.ai_reject_button = danger_button("删除错误候选")
        self.ai_reject_button.clicked.connect(self._reject_candidate)
        self.ai_confirm_button = primary_button("确认入库")
        self.ai_confirm_button.clicked.connect(self._commit_candidate)
        actions.addWidget(self.ai_previous_button)
        actions.addWidget(self.ai_next_button)
        actions.addStretch(1)
        actions.addWidget(self.ai_reject_button)
        actions.addWidget(self.ai_confirm_button)
        self.ai_confirmation_action_bar = action_bar
        layout.addWidget(action_bar)
        set_tab_order_chain(
            self.ai_form.notes,
            self.ai_previous_button,
            self.ai_next_button,
            self.ai_reject_button,
            self.ai_confirm_button,
        )
        return page

    def _build_image_tools_tab(self) -> QScrollArea:
        """Build the dedicated source-image and recognition adjustment tab."""
        image_tools = QWidget()
        root = QVBoxLayout(image_tools)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(10)

        splitter = QSplitter(Qt.Orientation.Horizontal, image_tools)
        root.addWidget(splitter, stretch=1)

        # 左栏：原图与题目选区
        left_panel = QWidget()
        left_panel.setObjectName("ImageToolsPreviewPanel")
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 10, 0)
        left_layout.setSpacing(8)
        self.image_preview = ImagePreviewLabel("无原图预览")
        self.image_preview.padding = 28
        self.image_preview.setMinimumHeight(320)
        self.image_preview.set_editable(True)
        self.image_preview.region_drawn.connect(self._save_drawn_region)
        left_layout.addWidget(self.image_preview, stretch=1)
        splitter.addWidget(left_panel)

        # 右栏：来源图片 / 题目区域 / AI 校对
        right_panel = QWidget()
        right_panel.setObjectName("ImageToolsSidePanel")
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(10, 0, 0, 0)
        right_layout.setSpacing(14)
        right_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        splitter.addWidget(right_panel)
        splitter.setStretchFactor(0, 7)
        splitter.setStretchFactor(1, 3)
        splitter.setSizes([720, 300])
        self._image_tools_splitter = splitter

        # —— 来源图片 ——
        source_section = QVBoxLayout()
        source_section.setSpacing(8)
        source_title = QLabel("来源图片")
        source_title.setObjectName("SectionTitle")
        source_section.addWidget(source_title)
        self.source_image_list = QListWidget()
        self.source_image_list.setObjectName("CandidateSourceImages")
        self.source_image_list.setAccessibleName("候选题来源图片")
        self.source_image_list.setIconSize(QSize(24, 24))
        self.source_image_list.setFixedHeight(104)
        source_section.addWidget(self.source_image_list)
        source_actions = QHBoxLayout()
        source_actions.setSpacing(8)
        source_up = QPushButton("来源图上移")
        source_up.setObjectName("RegionOutlineButton")
        source_up.clicked.connect(lambda: self._move_source_image(-1))
        source_down = QPushButton("来源图下移")
        source_down.setObjectName("RegionOutlineButton")
        source_down.clicked.connect(lambda: self._move_source_image(1))
        source_actions.addWidget(source_up)
        source_actions.addWidget(source_down)
        source_actions.addStretch(1)
        source_section.addLayout(source_actions)
        right_layout.addLayout(source_section)

        # —— 题目区域 ——
        region_section = QVBoxLayout()
        region_section.setSpacing(8)
        region_title = QLabel("题目区域")
        region_title.setObjectName("SectionTitle")
        region_section.addWidget(region_title)
        self.region_label = QLabel("")
        self.region_label.setObjectName("RegionStatusLabel")
        self.region_label.setWordWrap(True)
        region_section.addWidget(self.region_label)
        reset_region = QPushButton("恢复整图")
        reset_region.setObjectName("RegionOutlineButton")
        reset_region.clicked.connect(self._reset_candidate_region)
        self.undo_region_recognition = QPushButton("撤回上次重识别")
        self.undo_region_recognition.setObjectName("RegionOutlineButton")
        self.undo_region_recognition.clicked.connect(
            self._undo_region_rerecognition
        )
        self.rerecognize_region = QPushButton("按当前区域重新识别")
        self.rerecognize_region.setObjectName("RegionAccentButton")
        self.rerecognize_region.clicked.connect(
            self._start_region_rerecognition
        )
        region_section.addWidget(reset_region)
        region_section.addWidget(self.undo_region_recognition)
        region_section.addWidget(self.rerecognize_region)
        right_layout.addLayout(region_section)

        # —— AI 校对（紧凑状态卡）——
        self.uncertain_card = QFrame()
        self.uncertain_card.setObjectName("UncertainCard")
        uncertain_card_layout = QVBoxLayout(self.uncertain_card)
        uncertain_card_layout.setContentsMargins(12, 10, 12, 10)
        uncertain_card_layout.setSpacing(8)
        uncertain_header = QHBoxLayout()
        uncertain_header.setSpacing(8)
        warn_icon = QLabel("⚠")
        warn_icon.setObjectName("UncertainWarnIcon")
        self.uncertain_title = QLabel("AI 核对")
        self.uncertain_title.setObjectName("SectionTitle")
        uncertain_header.addWidget(warn_icon)
        uncertain_header.addWidget(self.uncertain_title)
        uncertain_header.addStretch(1)
        uncertain_card_layout.addLayout(uncertain_header)
        self.uncertain_label = QLabel("")
        self.uncertain_label.setWordWrap(True)
        self.uncertain_label.setObjectName("PageHint")
        uncertain_card_layout.addWidget(self.uncertain_label)
        self.uncertain_actions = QVBoxLayout()
        self.uncertain_actions.setSpacing(6)
        uncertain_card_layout.addLayout(self.uncertain_actions)
        right_layout.addWidget(self.uncertain_card)
        right_layout.addStretch(1)
        return self._scroll(image_tools)

    def _build_answer_capture(self) -> QWidget:
        page, layout = self._page()
        layout.addWidget(
            self._header(
                "识别我的作答",
                "选择作答图片并补充关键词，识别结果可修改后再填入。",
                self._return_to_ai_edit,
                back_tooltip="返回编辑字段",
            )
        )

        source = CardFrame()
        source.add_title("作答图片")
        answer_image_row = QHBoxLayout()
        self.answer_image_row_layout = answer_image_row
        answer_image_row.setSpacing(12)
        answer_actions = QVBoxLayout()
        self.answer_image_actions = answer_actions
        choose = primary_button("选择图片")
        choose.clicked.connect(self._choose_answer_image)
        remove = QPushButton("移除选中")
        remove.clicked.connect(self._remove_answer_images)
        answer_actions.addStretch(1)
        answer_actions.addWidget(choose)
        answer_actions.addWidget(remove)
        answer_actions.addStretch(1)
        answer_image_row.addLayout(answer_actions)
        self.answer_image_list = QListWidget()
        self.answer_image_list.setObjectName("AnswerImageList")
        self.answer_image_list.setAccessibleName("待识别作答图片")
        self.answer_image_list.setViewMode(QListView.ViewMode.IconMode)
        self.answer_image_list.setFlow(QListView.Flow.LeftToRight)
        self.answer_image_list.setWrapping(False)
        self.answer_image_list.setResizeMode(QListView.ResizeMode.Adjust)
        self.answer_image_list.setMovement(QListView.Movement.Static)
        self.answer_image_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.answer_image_list.setIconSize(QSize(144, 104))
        self.answer_image_list.setGridSize(QSize(170, 138))
        self.answer_image_list.setFixedHeight(156)
        self.answer_image_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.answer_image_list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.answer_image_list.itemClicked.connect(self._toggle_answer_image_viewer)
        answer_image_row.addWidget(self.answer_image_list, stretch=1, alignment=Qt.AlignmentFlag.AlignTop)
        # Retain these fields while callers migrate to the ordered image list.
        self.answer_image_preview = None
        self.answer_image_name = QLabel()
        self.answer_image_name.hide()
        source.body.addLayout(answer_image_row)
        layout.addWidget(source)

        instruction = CardFrame()
        instruction.add_title("定位关键词")
        instruction.add_hint("可选。例如：蓝色手写区域、最后一行计算结果。")
        self.answer_keywords = QTextEdit()
        describe_field(self.answer_keywords, "作答图片定位关键词")
        self.answer_keywords.setPlaceholderText("补充图片中需要提取的作答位置或特征")
        self.answer_keywords.setMaximumHeight(88)
        instruction.body.addWidget(self.answer_keywords)
        layout.addWidget(instruction)

        result = CardFrame()
        result.add_title("识别结果")
        self.answer_recognition_status = result.add_hint("选择图片后开始识别。")
        self.answer_recognition_result = QTextEdit()
        describe_field(self.answer_recognition_result, "作答识别结果")
        self.answer_recognition_result.setPlaceholderText("AI 识别结果会显示在这里，可直接修改。")
        self.answer_recognition_result.setMinimumHeight(150)
        result.body.addWidget(self.answer_recognition_result)
        layout.addWidget(result, stretch=1)

        actions = QHBoxLayout()
        actions.addStretch(1)
        self.answer_recognize_button = primary_button("识别作答")
        self.answer_recognize_button.clicked.connect(self._start_answer_recognition)
        self.answer_apply_button = QPushButton("填入我的作答")
        self.answer_apply_button.setEnabled(False)
        self.answer_apply_button.clicked.connect(self._apply_answer_recognition)
        actions.addWidget(self.answer_recognize_button)
        actions.addWidget(self.answer_apply_button)
        layout.addLayout(actions)
        set_tab_order_chain(
            self.answer_keywords,
            self.answer_recognize_button,
            self.answer_recognition_result,
            self.answer_apply_button,
        )
        return page

    def _open_answer_capture(self) -> None:
        self.answer_recognition_status.setText("选择图片后开始识别。")
        self.answer_recognition_result.clear()
        self.answer_apply_button.setEnabled(False)
        self.stack.setCurrentIndex(_PAGE_AI_ANSWER_CAPTURE)

    def resizeEvent(self, event) -> None:  # noqa: ANN001, N802
        super().resizeEvent(event)
        self._apply_image_intake_layout()
        self._update_image_tools_orientation()

    def _update_image_tools_orientation(self) -> None:
        splitter = getattr(self, "_image_tools_splitter", None)
        if splitter is None:
            return
        splitter.setOrientation(
            Qt.Orientation.Vertical
            if self.width() < 900
            else Qt.Orientation.Horizontal
        )

    def _apply_image_intake_layout(self) -> None:
        narrow = self.width() < 860
        direction = (
            QBoxLayout.Direction.TopToBottom
            if narrow
            else QBoxLayout.Direction.LeftToRight
        )
        self.ai_upload_content_layout.setDirection(direction)
        self.answer_image_row_layout.setDirection(direction)

    def _return_to_ai_edit(self) -> None:
        self.stack.setCurrentIndex(_PAGE_AI_CONFIRM)
        self.ai_result_tabs.setCurrentIndex(1)

    def _choose_answer_image(self) -> None:
        selected, _ = QFileDialog.getOpenFileNames(
            self,
            "选择作答图片",
            "",
            "Images (*.png *.jpg *.jpeg *.webp);;All (*.*)",
        )
        for value in selected:
            path = Path(value)
            if path in self.answer_images:
                continue
            pixmap = QPixmap(str(path))
            if pixmap.isNull():
                continue
            self.answer_images.append(path)
            item = QListWidgetItem(
                QIcon(pixmap.scaled(self.answer_image_list.iconSize(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)),
                path.name,
            )
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            item.setToolTip(str(path))
            self.answer_image_list.addItem(item)
        self.answer_image = self.answer_images[0] if self.answer_images else None
        if not self.answer_images:
            return
        self.answer_recognition_status.setText(f"已选择 {len(self.answer_images)} 张图片，可以开始识别。")
        self.answer_recognition_result.clear()
        self.answer_apply_button.setEnabled(False)

    def _run_user_answer_job(
        self, job_id: str, emit_progress, should_cancel
    ) -> dict[str, str]:
        """Background handler for the user_answer domain job."""
        job = self.intake.ai.get_job(job_id)
        if job is None:
            raise DomainError("作答识别任务不存在")
        try:
            config = json.loads(job.config_json or "{}")
        except (ValueError, TypeError):
            config = {}
        paths = [Path(str(value)) for value in config.get("image_paths") or []]
        keywords = str(config.get("keywords") or "")
        self.intake.ai.append_job_event(
            job_id,
            "status",
            text_value="正在识别作答内容…",
            payload={"stage": "started"},
        )
        answer = self.intake.recognize_user_answer_images(
            paths, keywords=keywords
        )
        if should_cancel():
            raise DomainError("作答识别任务已取消")
        self.intake.ai.append_job_event(
            job_id, "text_delta", text_value=answer, append_response=True
        )
        return {"user_answer": answer}

    def _on_answer_job_done(self, job_id: str) -> None:
        self._pending_answer_job_id = None
        self.answer_recognize_button.setEnabled(True)
        self.answer_recognize_button.setText("开始识别")
        job = self.intake.ai.get_job(job_id)
        answer = ""
        if job is not None:
            try:
                result = json.loads(job.result_json or "{}")
                answer = str(result.get("user_answer") or "")
            except (ValueError, TypeError):
                answer = job.response_text or ""
        if answer:
            self.answer_recognition_result.setPlainText(answer)
            self.answer_recognition_status.setText("识别完成，请核对并修改结果。")
        else:
            self.answer_recognition_status.setText("AI 未识别出作答内容，请重试或手动填写。")

    def _on_answer_job_failed(self, job_id: str, error: str) -> None:
        self._pending_answer_job_id = None
        self.answer_recognize_button.setEnabled(True)
        self.answer_recognize_button.setText("开始识别")
        self.answer_recognition_status.setText(f"识别失败：{error}")
        self.status_message.emit(f"作答识别失败：{error}")
    def _start_answer_recognition(self) -> None:
        if not self.answer_images:
            self.answer_recognition_status.setText("请先选择包含作答的图片。")
            self.status_message.emit("请先选择包含作答的图片")
            return
        if self._pending_answer_job_id is not None:
            return
        try:
            job = self.intake.start_user_answer_job(
                self.answer_images,
                keywords=self.answer_keywords.toPlainText(),
            )
        except DomainError as exc:
            self.answer_recognition_status.setText(str(exc))
            return
        self._pending_answer_job_id = job.id
        self.answer_recognize_button.setEnabled(False)
        self.answer_recognize_button.setText("已加入队列")
        self.coordinator.enqueue(job.id)
        self.answer_recognition_status.setText(
            "已加入 AI 队列，正在识别作答内容，可到任务队列查看进度。"
        )

    def _on_answer_recognition_done(self, answer: str) -> None:
        self.answer_recognition_result.setPlainText(answer)
        self.answer_recognition_status.setText("识别完成，请核对并修改结果。")
        self.answer_apply_button.setEnabled(True)

    def _on_answer_recognition_failed(self, error: str) -> None:
        message = f"识别失败，结果未写入：{error}"
        self.answer_recognition_status.setText(message)
        self.status_message.emit(message)

    def _on_answer_recognition_finished(self) -> None:
        worker = self.answer_recognition_worker
        self.answer_recognition_worker = None
        if worker is not None:
            worker.deleteLater()
        self.answer_recognize_button.setEnabled(True)
        self.answer_recognize_button.setText("识别作答")

    def _apply_answer_recognition(self) -> None:
        answer = self.answer_recognition_result.toPlainText().strip()
        if not answer:
            self.answer_recognition_status.setText("请先识别或填写作答内容。")
            self.status_message.emit("没有可填入的作答内容")
            return
        self.ai_form.user_answer.setPlainText(answer)
        self._queue_ai_preview()
        self._return_to_ai_edit()

    def _build_done(self) -> QWidget:
        page, layout = self._page()
        layout.addStretch(1)
        card = CardFrame()
        card.add_title("录题完成")
        self.done_message = card.add_hint("")
        primary_actions = QHBoxLayout()
        ai = primary_button("继续 AI 录题")
        ai.clicked.connect(self._new_ai)
        manual = QPushButton("继续手动录题")
        manual.clicked.connect(self._new_manual)
        home = QPushButton("返回题库")
        home.clicked.connect(self.library_requested.emit)
        primary_actions.addWidget(ai)
        primary_actions.addWidget(manual)
        primary_actions.addWidget(home)
        card.body.addLayout(primary_actions)

        secondary_actions = QHBoxLayout()
        view = QPushButton("查看刚入库的题目")
        view.clicked.connect(self._open_last_problem)
        finish = QPushButton("返回工作台")
        finish.clicked.connect(self.dashboard_requested.emit)
        secondary_actions.addWidget(view)
        secondary_actions.addWidget(finish)
        secondary_actions.addStretch(1)
        card.body.addLayout(secondary_actions)
        layout.addWidget(card)
        layout.addStretch(2)
        return page

    def show_manual(self) -> None:
        self.manual_form.reload_catalog()
        self.stack.setCurrentIndex(_PAGE_MANUAL)

    def show_ai_upload(self) -> None:
        ai = self.intake.runtime.settings.ai
        provider_label = (
            "Faro API（真实识图）"
            if ai.default_provider == "openai_compatible"
            else "Mock（离线测试）"
        )
        self.ai_config_hint.setText(
            f"{provider_label} · {ai.default_vision_model or '未设置模型'} · "
            f"{'已启用' if ai.enabled else '尚未启用'}"
        )
        self._refresh_recognition_cache_hint()
        self.stack.setCurrentIndex(_PAGE_AI_UPLOAD)

    def _refresh_recognition_cache_hint(self) -> None:
        summary = self.intake.ai.recognition_cache_summary()
        size_kb = summary["bytes"] / 1024
        self.ai_cache_hint.setText(
            f"已缓存 {summary['count']} 条 · {size_kb:.1f} KB"
        )

    def _clear_recognition_cache(self) -> None:
        summary = self.intake.ai.recognition_cache_summary()
        if not summary["count"]:
            self._refresh_recognition_cache_hint()
            return
        if (
            QMessageBox.question(
                self,
                "清空识别缓存",
                f"确认删除 {summary['count']} 条 AI 识别缓存？\n"
                "不会删除原图、题目或历史录题任务。",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        deleted = self.intake.ai.clear_recognition_cache()
        self._refresh_recognition_cache_hint()
        self.status_message.emit(f"已清空 {deleted} 条 AI 识别缓存")

    def show_ai(self) -> None:
        if not self.ai_job_id:
            self.ai_job_id = self.intake.latest_resumable_ai_job()
        if self.ai_job_id:
            try:
                self.ai_candidates = [
                    item
                    for item in self.intake.list_candidates(self.ai_job_id)
                    if item.status in {"pending", "conflict"}
                ]
            except DomainError:
                self.ai_job_id = None
                self.ai_candidates = []
        self.show_ai_upload()
        if self.ai_candidates:
            self.ai_task_surface.show()
            self.ai_task_status.setText(
                f"AI 已完成，生成 {len(self.ai_candidates)} 道待审核题目"
            )
            self.ai_task_button.setText("审核结果")
        elif self.ai_job_id:
            if self.ai_job_id in self._resumable_ai_job_ids():
                self.ai_task_surface.show()
                self.ai_task_button.setText("查看识别进度")
                if self.coordinator.active_job_id != self.ai_job_id:
                    self._start_worker(self.ai_job_id)
                else:
                    self.progress_timer.start()
                    self._poll_progress()
            else:
                # 任务已全部处理完（例如已在别处审核完）：不要再展示过期入口。
                self.ai_job_id = None
                self.ai_candidates.clear()
                self.ai_task_surface.hide()
        else:
            self.ai_task_surface.hide()

    def show_ai_review(self, job_id: str) -> bool:
        """Open exactly the review batch named by a completion notification."""
        try:
            candidates = [
                item
                for item in self.intake.list_candidates(job_id)
                if item.status in {"pending", "conflict"}
            ]
        except DomainError:
            return False
        if not candidates:
            return False
        self.ai_job_id = job_id
        self.ai_candidates = candidates
        self.candidate_index = 0
        self._load_candidate()
        self.stack.setCurrentIndex(_PAGE_AI_CONFIRM)
        return True

    def _pending_ai_candidates(self) -> list[IntakeCandidate]:
        """Fresh pending/conflict candidates for the current job."""
        if not self.ai_job_id:
            return []
        try:
            return [
                item
                for item in self.intake.list_candidates(self.ai_job_id)
                if item.status in {"pending", "conflict"}
            ]
        except DomainError:
            return []

    def _resumable_ai_job_ids(self) -> set[str]:
        """Jobs that still need attention (running, failed, or pending review)."""
        try:
            return {
                batch.job_id
                for batch in self.intake.list_resumable_ai_batches()
            }
        except DomainError:
            return set()

    def _open_ai_task_button(self) -> None:
        """Status strip button: review result opens the pending-review queue."""
        if not self.ai_job_id:
            return
        pending = self._pending_ai_candidates()
        if pending:
            self.ai_candidates = pending
            self.review_queue_requested.emit()
            return
        job = self.intake.ai.get_job(self.ai_job_id)
        if job is None or job.status in {"completed", "done", "canceled", "cancelled"}:
            # 任务已全部处理完（例如已在别处审核完）：不要再展示过期入口。
            self.ai_job_id = None
            self.ai_candidates.clear()
            self.ai_task_surface.hide()
            return
        self._open_current_ai_task()

    def _abandon_ai(self) -> None:
        job_id = self.ai_job_id or ""
        if not job_id:
            return
        if (
            QMessageBox.question(
                self,
                "放弃录题批次",
                "确认放弃选中的 AI 录题批次？\n"
                "已入库的题目不会受影响，未确认候选将不再显示。",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        if job_id == self.ai_job_id:
            self.coordinator.cancel(job_id)
        try:
            self.intake.abandon_ai_batch(job_id)
        except DomainError as exc:
            QMessageBox.warning(self, "无法放弃批次", str(exc))
            return
        if job_id == self.ai_job_id:
            self.ai_job_id = None
            self.ai_candidates.clear()
            self.progress_timer.stop()
            self.ai_task_surface.hide()
        self.status_message.emit("已放弃选中的 AI 录题批次")
        self.library_requested.emit()

    def _restore_existing_session(self) -> None:
        job_id = self.intake.latest_resumable_ai_job()
        if not job_id:
            return
        self.ai_job_id = job_id
        try:
            self.ai_candidates = [
                item
                for item in self.intake.list_candidates(job_id)
                if item.status in {"pending", "conflict"}
            ]
        except DomainError:
            self.ai_candidates = []

    def _restore_manual_draft(self) -> None:
        draft = self.intake.load_manual_draft()
        if draft is None:
            return
        self._restoring_manual_draft = True
        values = dict(draft.fields)
        values["tags"] = draft.tag_names
        self.manual_form.set_values(values)
        self.manual_images = list(draft.image_paths)
        self.manual_asset_list.clear()
        for path in self.manual_images:
            self.manual_asset_list.addItem(str(path))
        self._restoring_manual_draft = False

    def _queue_manual_draft(self) -> None:
        if not self._restoring_manual_draft:
            self.manual_draft_timer.start()

    def _save_manual_draft(self) -> None:
        if self._restoring_manual_draft:
            return
        fields = self.manual_form.values()
        tags = self.manual_form.tag_names()
        has_content = bool(
            fields.get("title")
            or str(fields.get("question_markdown") or "").strip()
            or str(fields.get("question_latex") or "").strip()
            or tags
            or self.manual_images
        )
        if not has_content:
            self.intake.clear_manual_draft()
            return
        try:
            self.intake.save_manual_draft(
                fields,
                tag_names=tags,
                image_paths=self.manual_images,
            )
        except (DomainError, OSError) as exc:
            self.status_message.emit(f"手动草稿保存失败：{exc}")

    def _add_manual_images(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "添加原始图片",
            "",
            "Images (*.png *.jpg *.jpeg *.webp);;All (*.*)",
        )
        for value in files:
            path = Path(value)
            if path not in self.manual_images:
                self.manual_images.append(path)
                self.manual_asset_list.addItem(str(path))
        self._queue_manual_draft()

    def _remove_manual_images(self) -> None:
        rows = sorted({self.manual_asset_list.row(item) for item in self.manual_asset_list.selectedItems()}, reverse=True)
        for row in rows:
            self.manual_asset_list.takeItem(row)
            self.manual_images.pop(row)
        self._queue_manual_draft()

    def _clear_manual(self) -> None:
        if (
            QMessageBox.question(self, "清空表单", "清空当前尚未入库的内容？")
            != QMessageBox.StandardButton.Yes
        ):
            return
        self.manual_form.clear()
        self.manual_images.clear()
        self.manual_asset_list.clear()
        self.manual_draft_timer.stop()
        self.intake.clear_manual_draft()

    def _commit_manual(self) -> None:
        try:
            problem = self.intake.commit_manual(
                self.manual_form.values(),
                tag_names=self.manual_form.tag_names(),
                image_paths=self.manual_images,
            )
        except DomainError as exc:
            self.status_message.emit(str(exc))
            QMessageBox.warning(self, "无法入库", str(exc))
            return
        self.last_problem_id = problem.id
        self.manual_form.clear()
        self.manual_images.clear()
        self.manual_asset_list.clear()
        self.manual_draft_timer.stop()
        self.intake.clear_manual_draft()
        self.problem_committed.emit(problem.id)
        self._show_done(f"“{problem.title or '无标题题目'}”已进入正式题库。")

    def _add_ai_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "选择需要 AI 整理的图片",
            "",
            "Images (*.png *.jpg *.jpeg *.webp);;All (*.*)",
        )
        self.add_ai_files([Path(value) for value in files])

    def add_ai_files(self, paths: list[Path]) -> int:
        """Add user-selected sources to the temporary AI intake surface."""

        invalid: list[str] = []
        first_added_row: int | None = None
        added = 0
        for path in paths:
            if path in self.ai_files:
                continue
            pixmap = QPixmap(str(path))
            if pixmap.isNull():
                invalid.append(path.name)
                continue
            self.ai_files.append(path)
            thumbnail = pixmap.scaled(
                self.ai_file_list.iconSize(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            item = QListWidgetItem(QIcon(thumbnail), path.name)
            item.setToolTip(str(path))
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            self.ai_file_list.addItem(item)
            added += 1
            if first_added_row is None:
                first_added_row = self.ai_file_list.count() - 1
        if first_added_row is not None:
            self.ai_file_list.setCurrentRow(first_added_row)
        if invalid:
            QMessageBox.warning(
                self,
                "部分图片无法读取",
                "以下文件不是有效图片或格式不受支持：\n" + "\n".join(invalid),
            )
        return added

    def _remove_ai_files(self) -> None:
        rows = sorted({self.ai_file_list.row(item) for item in self.ai_file_list.selectedItems()}, reverse=True)
        for row in rows:
            self.ai_file_list.takeItem(row)
            self.ai_files.pop(row)
        if self.ai_file_list.count():
            self.ai_file_list.setCurrentRow(
                min(rows[-1] if rows else 0, self.ai_file_list.count() - 1)
            )
        else:
            self._close_ai_image_viewer()

    def _toggle_ai_file_viewer(self, item: QListWidgetItem) -> None:
        row = self.ai_file_list.row(item)
        if not 0 <= row < len(self.ai_files):
            return
        path = self.ai_files[row]
        if (
            self._ai_image_viewer is not None
            and self._ai_image_viewer.isVisible()
            and self._ai_image_viewer.current_image_path == path
        ):
            self._close_ai_image_viewer()
            return
        self._close_ai_image_viewer()
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            return
        viewer = ImageViewerDialog(
            pixmap,
            self,
            image_paths=self.ai_files,
            image_index=row,
        )
        viewer.finished.connect(lambda *_args: setattr(self, "_ai_image_viewer", None))
        self._ai_image_viewer = viewer
        viewer.show()

    def _close_ai_image_viewer(self) -> None:
        if self._ai_image_viewer is not None:
            self._ai_image_viewer.close()
            self._ai_image_viewer = None

    def _remove_answer_images(self) -> None:
        for row in sorted({self.answer_image_list.row(item) for item in self.answer_image_list.selectedItems()}, reverse=True):
            self.answer_image_list.takeItem(row)
            self.answer_images.pop(row)
        self.answer_image = self.answer_images[0] if self.answer_images else None

    def _toggle_answer_image_viewer(self, item: QListWidgetItem) -> None:
        if not self.answer_images:
            return
        if self._answer_image_viewer is not None and self._answer_image_viewer.isVisible():
            self._answer_image_viewer.close()
            self._answer_image_viewer = None
            return
        row = self.answer_image_list.row(item)
        pixmap = QPixmap(str(self.answer_images[row]))
        if pixmap.isNull():
            return
        viewer = ImageViewerDialog(pixmap, self, image_paths=self.answer_images, image_index=row)
        viewer.finished.connect(
            lambda *_args: setattr(self, "_answer_image_viewer", None)
        )
        self._answer_image_viewer = viewer
        viewer.show()

    def _rebuild_prompt_templates(self) -> None:
        while self._template_row.count():
            item = self._template_row.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        if not self._prompt_templates:
            empty = QLabel("暂无默认提示词；点击右侧“新增”添加一条常用提示语")
            empty.setObjectName("MutedLabel")
            self._template_row.addWidget(empty)
        else:
            for text in self._prompt_templates:
                button = ghost_button(text)
                button.setToolTip("点击将该提示词追加到上方输入框")
                button.clicked.connect(
                    lambda _checked=False, value=text: self._append_instruction(value)
                )
                self._template_row.addWidget(button)
        self._template_row.addStretch(1)

    def _add_prompt_template(self, text: str | None = None) -> None:
        if not isinstance(text, str):
            text = None
        if text is None:
            text, accepted = QInputDialog.getText(
                self, "新增默认提示词", "提示词内容："
            )
            if not accepted:
                return
        text = text.strip()
        if not text:
            return
        if text in self._prompt_templates:
            self.status_message.emit("该默认提示词已存在")
            return
        self._prompt_templates.append(text)
        self._save_prompt_templates()

    def _choose_prompt_template(self, title: str, prompt: str) -> int | None:
        """弹出可滚动的提示词列表，返回用户选中项的下标；取消返回 None。"""
        if not self._prompt_templates:
            self.status_message.emit("暂无默认提示词，请先点击“新增”")
            return None
        from PySide6.QtWidgets import QDialog, QDialogButtonBox, QListWidget

        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.setMinimumWidth(420)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel(prompt))
        listing = QListWidget(dialog)
        for value in self._prompt_templates:
            listing.addItem(value)
        listing.setCurrentRow(0)
        listing.setMinimumHeight(160)
        layout.addWidget(listing)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        row = listing.currentRow()
        return row if 0 <= row < len(self._prompt_templates) else None

    def _edit_prompt_template(self, index: int | None = None, text: str | None = None) -> None:
        if not isinstance(index, int) or isinstance(index, bool):
            index = None
        if not isinstance(text, str):
            text = None
        if not self._prompt_templates:
            self.status_message.emit("暂无默认提示词，请先点击“新增”")
            return
        if index is None:
            index = self._choose_prompt_template(
                "编辑默认提示词", "选择要编辑的提示词："
            )
            if index is None:
                return
        if text is None:
            text, accepted = QInputDialog.getText(
                self,
                "编辑默认提示词",
                "新内容：",
                text=self._prompt_templates[index],
            )
            if not accepted:
                return
        text = text.strip()
        if not text:
            return
        self._prompt_templates[index] = text
        self._save_prompt_templates()

    def _delete_prompt_template(self, index: int | None = None) -> None:
        if not isinstance(index, int) or isinstance(index, bool):
            index = None
        if not self._prompt_templates:
            self.status_message.emit("暂无默认提示词，请先点击“新增”")
            return
        if index is None:
            index = self._choose_prompt_template(
                "删除默认提示词", "选择要删除的提示词："
            )
            if index is None:
                return
        self._prompt_templates.pop(index)
        self._save_prompt_templates()

    def _save_prompt_templates(self) -> None:
        try:
            save_intake_prompt_templates(
                self.intake.runtime.paths.root, self._prompt_templates
            )
        except Exception:  # noqa: BLE001
            self.status_message.emit("默认提示词保存失败")
            return
        self._rebuild_prompt_templates()
        self.status_message.emit("默认提示词已更新")

    def _append_instruction(self, text: str) -> None:
        current = self.ai_instruction.toPlainText().strip()
        self.ai_instruction.setPlainText(f"{current}\n{text}".strip())

    def _start_ai(self) -> None:
        if self.ai_job_id and self.ai_job_id == self.coordinator.active_job_id:
            self._open_current_ai_task()
            return
        try:
            started = self.intake.start_ai(
                self.ai_files,
                user_instruction=self.ai_instruction.toPlainText(),
                recognition_mode=str(self.ai_recognition_mode.currentData()),
                use_recognition_cache=self.ai_use_cache.isChecked(),
            )
        except DomainError as exc:
            QMessageBox.warning(self, "无法开始识别", str(exc))
            return
        self.ai_job_id = started.job_id
        self._cancelled_ai_jobs.discard(started.job_id)
        self.ai_candidates.clear()
        self._ai_live_stage_label = ""
        self._ai_live_stage_history.clear()
        self.processing_preview.clear()
        self.processing_preview.show()
        self.processing_error.clear()
        self.processing_retry.setVisible(False)
        self.processing_back.setVisible(True)
        self.processing_cancel_button.setEnabled(True)
        self.ai_task_surface.show()
        self.ai_task_status.setText("AI 任务已提交，正在后台排队")
        self.ai_task_button.setText("查看识别进度")
        self._start_worker(started.job_id)
        self.ai_files.clear()
        self.ai_file_list.clear()
        self.ai_instruction.clear()
        self.status_message.emit("AI 录题任务已提交，可以继续上传下一批图片")

    def _open_current_ai_task(self) -> None:
        if not self.ai_job_id:
            return
        if self.show_ai_review(self.ai_job_id):
            return
        job = self.intake.ai.get_job(self.ai_job_id)
        self.processing_preview.setPlainText(job.response_text if job else "")
        self.stack.setCurrentIndex(_PAGE_AI_PROCESSING)
        self.progress_timer.start()
        self._poll_progress()

    def _start_worker(self, job_id: str) -> None:
        self.coordinator.enqueue(job_id)
        self.progress_timer.start()
        self._poll_progress()

    def _on_coordinator_progress(self, job_id: str, event: object) -> None:
        if job_id == self.ai_job_id:
            self._on_ai_progress(event)

    def _poll_progress(self) -> None:
        if not self.ai_job_id:
            return
        try:
            progress = self.intake.progress(self.ai_job_id)
        except DomainError as exc:
            self.processing_error.setText(str(exc))
            self.progress_timer.stop()
            return
        self.progress_bar.setRange(0, max(1, progress.total))
        self.progress_bar.setValue(progress.done + progress.failed)
        self.processing_status.setText(
            f"已完成 {progress.done}/{progress.total} · 失败 {progress.failed}"
        )
        self.ai_task_surface.show()
        self.ai_task_status.setText(
            f"已完成 {progress.done}/{progress.total} · 失败 {progress.failed}"
        )
        timing_labels = (
            ("queue_wait", "任务排队"),
            ("preflight", "本地预检查"),
            ("cache_lookup", "缓存查找"),
            ("image_encode", "图片读取与编码"),
            ("quick_request", "首轮题干与答案识别"),
            ("enrichment_request", "解析与分类补全"),
            ("request", "AI 请求与等待"),
            ("response_parse", "响应 JSON 解析"),
            ("validation", "字段校验"),
            ("candidate_write", "候选写入"),
            ("classification_match", "分类目录匹配"),
            ("ui_wait", "界面信号等待"),
            ("provider_total", "AI 提供商总计"),
            ("total", "单图总计"),
        )
        measured = []
        granular_provider = "request" in progress.timings_ms
        for key, label in timing_labels:
            if key == "provider_total" and granular_provider:
                continue
            value = progress.timings_ms.get(key)
            if value is None:
                continue
            measured.append(
                f"{label} {value / 1000:.2f} 秒"
                if value >= 1000
                else f"{label} {value:.0f} 毫秒"
            )
        if measured:
            retry_text = (
                f" · 自动重试 {progress.retry_count} 次"
                if progress.retry_count
                else ""
            )
            cache_text = (
                f" · 本地缓存命中 {progress.cache_hits} 次"
                if progress.cache_hits
                else ""
            )
            timing_text = (
                f"已完成 {progress.timing_samples} 张的实测平均："
                + " · ".join(measured)
                + retry_text
                + cache_text
            )
            if self.processing_steps.text() != timing_text:
                self.processing_steps.setText(timing_text)
        else:
            if self.processing_steps.text() != _AI_PROCESSING_HINT:
                self.processing_steps.setText(_AI_PROCESSING_HINT)
        if progress.status == "cancelled":
            self.progress_timer.stop()
            self._cancelled_ai_jobs.add(progress.job_id)
            if progress.job_id == self.ai_job_id:
                self.ai_job_id = None
                self.ai_candidates.clear()
                self.ai_task_surface.hide()
                self.processing_status.setText("任务已取消")
                self.stack.setCurrentIndex(_PAGE_AI_UPLOAD)
                QTimer.singleShot(0, self.show_ai_upload)
            return
        if progress.status == "completed":
            self.progress_timer.stop()

    def _on_ai_progress(self, event: object) -> None:
        if not isinstance(event, dict):
            return
        label = str(event.get("label") or "").strip()
        if label:
            self._ai_live_stage_label = label
            if not self._ai_live_stage_history or self._ai_live_stage_history[-1] != label:
                self._ai_live_stage_history.append(label)
                self._ai_live_stage_history = self._ai_live_stage_history[-4:]
            if self.processing_steps.text() != _AI_PROCESSING_HINT:
                self.processing_steps.setText(_AI_PROCESSING_HINT)
            self.ai_task_surface.show()
            if self.ai_task_status.text() != label:
                self.ai_task_status.setText(label)
        text_delta = event.get("text_delta")
        if isinstance(text_delta, str) and text_delta:
            cursor = self.processing_preview.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            cursor.insertText(text_delta)
            self.processing_preview.setTextCursor(cursor)
            self.processing_preview.ensureCursorVisible()
        preview = event.get("preview")
        if not isinstance(preview, dict):
            return
        title = str(preview.get("title") or "").strip()
        question = str(preview.get("question_markdown") or preview.get("question_latex") or "").strip()
        answer = str(preview.get("correct_answer") or "").strip()
        lines = ["首轮识别结果（后台仍在补全，请以最终确认页内容为准）"]
        if title:
            lines.extend(["", f"题目：{title}"])
        if question:
            lines.extend(["", "题干：", question])
        if answer:
            lines.extend(["", "答案：", answer])
        if not self.processing_preview.toPlainText().strip():
            self.processing_preview.setPlainText("\n".join(lines))

    def _cancel_ai(self) -> None:
        job_id = self.ai_job_id
        if not job_id:
            return
        self.processing_cancel_button.setEnabled(False)
        self._cancelled_ai_jobs.add(job_id)
        self.coordinator.cancel(job_id)
        try:
            self.intake.abandon_ai_batch(job_id)
        except DomainError as exc:
            self._cancelled_ai_jobs.discard(job_id)
            self.processing_cancel_button.setEnabled(True)
            self.processing_error.setText(str(exc))
            return
        self.progress_timer.stop()
        self.ai_job_id = None
        self.ai_candidates.clear()
        self.processing_error.clear()
        self.processing_status.setText("任务已取消")
        self.status_message.emit("AI 录题任务已取消，不会在下次启动时恢复")
        self.stack.setCurrentIndex(_PAGE_AI_UPLOAD)
        self.show_ai_upload()
        QTimer.singleShot(0, self.show_ai_upload)

    def _retry_failed_ai(self) -> None:
        if not self.ai_job_id:
            return
        if self.coordinator.active_job_id == self.ai_job_id:
            return
        self.processing_error.clear()
        self.processing_retry.setVisible(False)
        self.processing_back.setVisible(False)
        self.processing_status.setText("正在重新连接 AI 服务并重试失败图片…")
        self._start_worker(self.ai_job_id)
        self.status_message.emit("正在重新尝试失败的 AI 录题项")

    def _on_ai_done(self, job_id: str) -> None:
        if job_id == self._pending_answer_job_id:
            self._on_answer_job_done(job_id)
            return
        if job_id in self._cancelled_ai_jobs:
            return
        if job_id != self.ai_job_id:
            return
        self.progress_timer.stop()
        ui_wait_ms = 0.0
        try:
            classification_started = perf_counter()
            self.ai_candidates = [
                item
                for item in self.intake.list_candidates(job_id)
                if item.status in {"pending", "conflict"}
            ]
            classification_match_ms = (
                perf_counter() - classification_started
            ) * 1000
            self.intake.ai.record_ui_delivery_timings(
                job_id,
                ui_wait_ms=ui_wait_ms,
                classification_match_ms=classification_match_ms,
            )
            self._poll_progress()
            failures = self.intake.failed_items(job_id)
        except DomainError as exc:
            self._on_ai_failed(job_id, str(exc))
            return
        if not self.ai_candidates:
            detail = "\n".join(failures[:5]) or "AI 没有生成可确认的题目。"
            self.processing_error.setText(detail)
            self.processing_retry.setVisible(bool(failures))
            self.processing_back.setVisible(True)
            self.status_message.emit("AI 识别未生成候选题")
            return
        self.candidate_index = 0
        self.processing_retry.setVisible(False)
        self._load_candidate()
        self.ai_task_status.setText(
            f"AI 已完成，生成 {len(self.ai_candidates)} 道待审核题目"
        )
        self.ai_task_button.setText("审核结果")
        if self.stack.currentIndex() == _PAGE_AI_PROCESSING:
            self.stack.setCurrentIndex(_PAGE_AI_CONFIRM)
        self.status_message.emit(
            f"AI 已完成，生成 {len(self.ai_candidates)} 道待确认题目"
        )
        self.ai_review_ready.emit(job_id, len(self.ai_candidates))

    def _on_ai_failed(self, job_id: str, error: str) -> None:
        if job_id == self._pending_answer_job_id:
            self._on_answer_job_failed(job_id, error)
            return
        if job_id in self._cancelled_ai_jobs:
            return
        if job_id != self.ai_job_id:
            return
        self.progress_timer.stop()
        self.processing_error.setText(error)
        self.processing_retry.setVisible(True)
        self.processing_back.setVisible(True)
        self.stack.setCurrentIndex(_PAGE_AI_PROCESSING)
        self.status_message.emit(f"AI 录题失败：{error}")

    def _load_candidate(self) -> None:
        if not self.ai_candidates:
            return
        self.candidate_index %= len(self.ai_candidates)
        candidate = self.ai_candidates[self.candidate_index]
        self.ai_form.content_blocks.set_source_images(candidate.source_images)
        self.ai_form.set_values(candidate.fields)
        self._refresh_ai_preview()
        self.image_preview.set_path(candidate.original_image)
        self.source_image_list.clear()
        for path in candidate.source_images:
            item = QListWidgetItem(_ellipsize_middle(path.name))
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            item.setToolTip(str(path))
            thumbnail = QPixmap(str(path))
            if not thumbnail.isNull():
                item.setIcon(
                    QIcon(
                        thumbnail.scaled(
                            28,
                            28,
                            Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation,
                        )
                    )
                )
            self.source_image_list.addItem(item)
        if self.source_image_list.count():
            self.source_image_list.setCurrentRow(0)
        self.image_preview.set_region(candidate.region)
        self._show_region_label(candidate.region)
        region = candidate.region
        is_cropped = bool(
            region
            and (
                region.get("x", 0) > 0.001
                or region.get("y", 0) > 0.001
                or region.get("width", 1) < 0.998
                or region.get("height", 1) < 0.998
            )
        )
        self.rerecognize_region.setEnabled(
            is_cropped
            and not (
                self.region_worker
                and self.region_worker.isRunning()
            )
        )
        self.undo_region_recognition.setEnabled(
            self.intake.can_undo_region_rerecognition(
                candidate.review_item_id
            )
        )
        if candidate.uncertain:
            self.uncertain_title.show()
            lines = ["请在“编辑字段”中重点核对以下内容："]
            self._clear_uncertain_actions()
            for item in candidate.uncertain:
                field = str(item.get("field") or item.get("name") or "字段")
                reason = str(item.get("reason") or item.get("message") or "需要确认")
                confidence = item.get("confidence")
                confidence_text = (
                    f"（置信度 {float(confidence):.0%}）"
                    if isinstance(confidence, (int, float))
                    else ""
                )
                lines.append(f"• {field}：{reason}{confidence_text}")
                action = ghost_button(f"核对 {self._uncertain_field_label(field)}")
                action.clicked.connect(
                    lambda _checked=False, target=field: self._focus_uncertain_field(
                        target
                    )
                )
                self.uncertain_actions.addWidget(action)
            self.uncertain_label.setText("\n".join(lines))
            self.uncertain_label.setObjectName("WarningLabel")
            self.uncertain_label.show()
        else:
            self.uncertain_title.hide()
            self.uncertain_label.hide()
            self.uncertain_label.setText("")
            self.uncertain_label.setObjectName("PageHint")
            self._clear_uncertain_actions()
        self.uncertain_label.style().unpolish(self.uncertain_label)
        self.uncertain_label.style().polish(self.uncertain_label)

    def _clear_uncertain_actions(self) -> None:
        while self.uncertain_actions.count():
            item = self.uncertain_actions.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    @staticmethod
    def _uncertain_field_label(field: str) -> str:
        return {
            "title": "题目标题",
            "subject_id": "科目",
            "chapter_id": "章节",
            "question_markdown": "题干",
            "question_latex": "题干公式",
            "user_answer": "我的作答",
            "correct_answer": "正确答案",
            "solution_markdown": "解析",
            "notes": "备注",
            "tags": "标签",
        }.get(field, field)

    def _focus_uncertain_field(self, field: str) -> None:
        editor = {
            "title": self.ai_form.title_edit,
            "subject_id": self.ai_form.subject,
            "chapter_id": self.ai_form.chapter,
            "question_markdown": self.ai_form.question,
            "question_latex": self.ai_form.question,
            "user_answer": self.ai_form.user_answer,
            "correct_answer": self.ai_form.correct_answer,
            "solution_markdown": self.ai_form.solution,
            "notes": self.ai_form.notes,
            "tags": self.ai_form.tags,
        }.get(field)
        self.ai_result_tabs.setCurrentIndex(1)
        if editor is not None:
            editor.setFocus(Qt.FocusReason.OtherFocusReason)

    def _queue_ai_preview(self) -> None:
        self.preview_timer.start()

    def _on_ai_result_tab_changed(self, index: int) -> None:
        if index == 0:
            self._refresh_ai_preview()
        elif index == 1:
            self.ai_form.refresh_render_previews()

    def _refresh_ai_preview(self) -> None:
        if not hasattr(self, "ai_result_preview") or not hasattr(self, "ai_form"):
            return
        fields = self.ai_form.values()
        if self.ai_candidates:
            candidate = self.ai_candidates[self.candidate_index]
            paths = self.intake.candidate_source_images(candidate.review_item_id)
            blocks = normalize_content_blocks(fields.get("content_blocks"))
            for block in blocks:
                if block.get("type") != "figure":
                    continue
                index = int(block.get("source_image_index", 0))
                region = block.get("source_region") or {}
                if not (0 <= index < len(paths) and region):
                    continue
                image = QImage(str(paths[index]))
                if image.isNull():
                    continue
                rect = QRectF(
                    image.width() * float(region.get("x", 0)),
                    image.height() * float(region.get("y", 0)),
                    image.width() * float(region.get("width", 0)),
                    image.height() * float(region.get("height", 0)),
                ).toAlignedRect().intersected(image.rect())
                crop = image.copy(rect)
                payload = QBuffer()
                payload.open(QIODevice.OpenModeFlag.WriteOnly)
                if crop.save(payload, "PNG"):
                    block["image_data_uri"] = (
                        "data:image/png;base64,"
                        + base64.b64encode(bytes(payload.data())).decode("ascii")
                    )
            fields["content_blocks"] = blocks
        if self.ai_form.subject.currentData():
            fields["subject_name"] = self.ai_form.subject.currentText()
        if self.ai_form.chapter.currentData():
            fields["chapter_name"] = self.ai_form.chapter.currentText()
        self.ai_result_preview.set_problem(
            fields,
            tag_names=self.ai_form.tag_names(),
            include_answers=True,
            show_header=False,
            classic=True,
        )
        self.ai_form.refresh_render_previews()

    def _move_candidate(self, delta: int) -> None:
        if not self.ai_candidates:
            return
        self.candidate_index = (self.candidate_index + delta) % len(self.ai_candidates)
        self._load_candidate()

    def _move_source_image(self, delta: int) -> None:
        if not self.ai_candidates:
            return
        row = self.source_image_list.currentRow()
        target = row + delta
        if row < 0 or target < 0 or target >= self.source_image_list.count():
            return
        item = self.source_image_list.takeItem(row)
        self.source_image_list.insertItem(target, item)
        self.source_image_list.setCurrentRow(target)
        paths = [
            Path(str(self.source_image_list.item(index).data(Qt.ItemDataRole.UserRole)))
            for index in range(self.source_image_list.count())
        ]
        candidate = self.ai_candidates[self.candidate_index]
        try:
            self.intake.reorder_candidate_source_images(candidate.review_item_id, paths)
        except DomainError as exc:
            QMessageBox.warning(self, "无法调整来源图", str(exc))
            self._load_candidate()
            return
        self.ai_form.content_blocks.set_source_images(paths)
        blocks = self.ai_form.content_blocks.blocks()
        for block in blocks:
            if block.get("type") != "figure":
                continue
            index = int(block.get("source_image_index", 0))
            if index == row:
                block["source_image_index"] = target
            elif row < target and row < index <= target:
                block["source_image_index"] = index - 1
            elif target < row and target <= index < row:
                block["source_image_index"] = index + 1
        self.ai_form.content_blocks.set_blocks(blocks)
        self._queue_ai_preview()
        self.status_message.emit("来源图片顺序已保存；不会自动重新识别")

    def _edit_figure_crop(self, row: int) -> None:
        if not self.ai_candidates:
            return
        candidate = self.ai_candidates[self.candidate_index]
        sources = self.intake.candidate_source_images(candidate.review_item_id)
        blocks = self.ai_form.content_blocks.blocks()
        if not 0 <= row < len(blocks) or blocks[row].get("type") != "figure":
            self.status_message.emit("当前题图内容块已经变化，请重新选择")
            return
        block = blocks[row]
        try:
            dialog = FigureCropDialog(
                sources,
                source_image_index=int(block.get("source_image_index", 0)),
                region=block.get("source_region") or None,
                parent=self,
            )
        except DomainError as exc:
            self.status_message.emit(str(exc))
            return
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        source_index, region = dialog.result_crop()
        try:
            self.ai_form.content_blocks.apply_figure_crop(
                row, source_index, region
            )
        except DomainError as exc:
            self.status_message.emit(str(exc))
            return
        self._queue_ai_preview()
        self.status_message.emit("题图裁剪已更新，确认入库前仍可继续调整")

    def _show_region_label(self, region: dict[str, float]) -> None:
        if region:
            text = "已选择题目区域"
            detail = (
                f"x {region['x']:.1%} · y {region['y']:.1%} · "
                f"宽 {region['width']:.1%} · 高 {region['height']:.1%}"
            )
        else:
            text = "当前使用整张原图"
            detail = "未设置局部题目区域"
        self.region_label.setText(text)
        self.region_label.setToolTip(detail)

    def _save_drawn_region(self, region: dict[str, float]) -> None:
        if not self.ai_candidates:
            return
        candidate = self.ai_candidates[self.candidate_index]
        try:
            normalized = self.intake.update_ai_candidate_region(
                candidate.review_item_id, region
            )
        except DomainError as exc:
            self.image_preview.set_region(candidate.region)
            QMessageBox.warning(self, "无法保存区域", str(exc))
            return
        candidate.region.clear()
        candidate.region.update(normalized)
        self.image_preview.set_region(normalized)
        self._show_region_label(normalized)
        self.rerecognize_region.setEnabled(True)
        self.status_message.emit("当前题目区域已保存")

    def _reset_candidate_region(self) -> None:
        if not self.ai_candidates:
            return
        candidate = self.ai_candidates[self.candidate_index]
        try:
            self.intake.update_ai_candidate_region(candidate.review_item_id, {})
        except DomainError as exc:
            QMessageBox.warning(self, "无法恢复整图", str(exc))
            return
        candidate.region.clear()
        self.image_preview.set_region({})
        self._show_region_label({})
        self.rerecognize_region.setEnabled(False)
        self.status_message.emit("当前候选已恢复使用整张原图")

    def _start_region_rerecognition(self) -> None:
        if not self.ai_candidates:
            return
        if self.region_worker and self.region_worker.isRunning():
            return
        candidate = self.ai_candidates[self.candidate_index]
        if not candidate.region:
            self.region_label.setText(
                "请先在左侧原图上绘制一个小于整图的题目区域，再重新识别。"
            )
            self.status_message.emit("请先框选题目区域")
            return
        self.rerecognize_region.setEnabled(False)
        self.rerecognize_region.setText("正在重新识别…")
        self.status_message.emit("正在按当前蓝框裁切临时图片并重新识别")
        self.region_worker = RegionRecognitionWorker(
            self.intake,
            candidate.review_item_id,
            self.ai_form.values(),
            self.ai_form.tag_names(),
            self,
        )
        self.region_worker.finished_ok.connect(
            self._on_region_rerecognition_done
        )
        self.region_worker.failed.connect(
            self._on_region_rerecognition_failed
        )
        self.region_worker.finished.connect(
            self._on_region_worker_finished
        )
        self.region_worker.start()

    def _on_region_rerecognition_done(
        self,
        proposal: RegionRecognitionProposal,
    ) -> None:
        self.rerecognize_region.setText("按当前区域重新识别")
        dialog = _RegionRecognitionCompareDialog(proposal, self)
        dialog.exec()
        try:
            self.intake.decide_region_rerecognition(
                proposal.proposal_id,
                apply_new=dialog.apply_new,
            )
        except DomainError as exc:
            QMessageBox.warning(self, "无法处理重新识别结果", str(exc))
            self._reload_region_candidate(proposal.candidate_id)
            return
        if dialog.apply_new:
            self.status_message.emit("已采用区域重新识别结果，可继续编辑或撤回")
            self._reload_region_candidate(proposal.candidate_id)
        else:
            self.status_message.emit("已保留原识别结果")
            self._load_candidate()

    def _on_region_rerecognition_failed(self, error: str) -> None:
        self.rerecognize_region.setText("按当前区域重新识别")
        self._load_candidate()
        QMessageBox.warning(self, "区域重新识别失败", error)

    def _on_region_worker_finished(self) -> None:
        worker = self.region_worker
        self.region_worker = None
        if worker is not None:
            worker.deleteLater()
        if self.ai_candidates:
            region = self.ai_candidates[self.candidate_index].region
            self.rerecognize_region.setEnabled(bool(region))

    def _reload_region_candidate(self, candidate_id: str) -> None:
        self.ai_candidates = [
            item
            for item in self.intake.list_candidates(self.ai_job_id or "")
            if item.status in {"pending", "conflict"}
        ]
        self.candidate_index = next(
            (
                index
                for index, item in enumerate(self.ai_candidates)
                if item.review_item_id == candidate_id
            ),
            0,
        )
        if self.ai_candidates:
            self._load_candidate()

    def _undo_region_rerecognition(self) -> None:
        if not self.ai_candidates:
            return
        candidate = self.ai_candidates[self.candidate_index]
        try:
            self.intake.undo_region_rerecognition(
                candidate.review_item_id
            )
        except DomainError as exc:
            QMessageBox.warning(self, "无法撤回", str(exc))
            return
        self._reload_region_candidate(candidate.review_item_id)
        self.status_message.emit("已撤回上一次采用的区域重新识别结果")

    def _commit_candidate(self) -> None:
        if not self.ai_candidates:
            return
        candidate = self.ai_candidates[self.candidate_index]
        try:
            problem = self.intake.commit_ai_candidate(
                candidate.review_item_id,
                self.ai_form.values(),
                tag_names=self.ai_form.tag_names(),
            )
        except DomainError as exc:
            QMessageBox.warning(self, "无法入库", str(exc))
            return
        self.last_problem_id = problem.id
        self.problem_committed.emit(problem.id)
        self.ai_candidates.pop(self.candidate_index)
        if self.ai_candidates:
            self.candidate_index %= len(self.ai_candidates)
            self._load_candidate()
            self.status_message.emit("题目已入库，继续确认下一题")
        else:
            failures = (
                self.intake.failed_items(self.ai_job_id)
                if self.ai_job_id
                else []
            )
            if failures:
                self.processing_error.setText(
                    f"题目已入库，但本批仍有 {len(failures)} 张图片识别失败。"
                )
                self.processing_retry.setVisible(True)
                self.processing_back.setVisible(False)
                self.stack.setCurrentIndex(_PAGE_AI_PROCESSING)
                self.status_message.emit("题目已入库，仍有失败图片可继续重试")
            else:
                self.ai_job_id = None
                self.ai_files.clear()
                self.ai_file_list.clear()
                self._close_ai_image_viewer()
                self.ai_instruction.clear()
                self.ai_task_surface.hide()
                self._show_done(
                    f"“{problem.title or 'AI 识别题目'}”已进入正式题库。"
                )

    def _reject_candidate(self) -> None:
        if not self.ai_candidates:
            return
        if (
            QMessageBox.question(
                self,
                "删除错误候选",
                "确认删除这道错误候选？\n"
                "它的暂存题会移入回收站，不影响同图的其他候选。",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        candidate = self.ai_candidates[self.candidate_index]
        try:
            self.intake.reject_ai_candidate(candidate.review_item_id)
        except DomainError as exc:
            QMessageBox.warning(self, "无法删除", str(exc))
            return
        self.ai_candidates.pop(self.candidate_index)
        if self.ai_candidates:
            self.candidate_index %= len(self.ai_candidates)
            self._load_candidate()
        else:
            failures = (
                self.intake.failed_items(self.ai_job_id)
                if self.ai_job_id
                else []
            )
            if not failures:
                self.ai_job_id = None
                self.ai_task_surface.hide()
            self.processing_error.setText(
                (
                    f"候选题已处理，但本批仍有 {len(failures)} 张图片识别失败。"
                    if failures
                    else "本批候选题均已跳过。可以返回并重新上传。"
                )
            )
            self.processing_retry.setVisible(bool(failures))
            self.processing_back.setVisible(not failures)
            self.stack.setCurrentIndex(_PAGE_AI_PROCESSING)

    def _show_done(self, message: str) -> None:
        self.done_message.setText(message)
        self.stack.setCurrentIndex(_PAGE_DONE)

    def _new_manual(self) -> None:
        self.manual_form.clear()
        self.show_manual()

    def _new_ai(self) -> None:
        self.ai_job_id = None
        self.ai_candidates.clear()
        self.ai_task_surface.hide()
        self.ai_files.clear()
        self.ai_file_list.clear()
        self._close_ai_image_viewer()
        self.ai_instruction.clear()
        self.show_ai_upload()

    def _open_last_problem(self) -> None:
        if self.last_problem_id:
            self.open_problem_requested.emit(self.last_problem_id)

    def shutdown(self) -> None:
        """Stop the page-owned worker before the application destroys Qt objects."""

        self.manual_draft_timer.stop()
        self._save_manual_draft()
        self.progress_timer.stop()
        if self.region_worker and self.region_worker.isRunning():
            self.region_worker.wait(300)
        if (
            self.answer_recognition_worker
            and self.answer_recognition_worker.isRunning()
        ):
            self.answer_recognition_worker.wait(300)
