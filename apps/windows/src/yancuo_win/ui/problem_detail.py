"""Dedicated problem reading page."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from yancuo_win.data.models import Problem
from yancuo_win.ui.math_content import MathContentView
from yancuo_win.ui.widgets import CardFrame, ghost_button, primary_button


class _ImageViewerDialog(QDialog):
    def __init__(self, pixmap: QPixmap, parent=None) -> None:
        super().__init__(parent)
        self._source = pixmap
        self._scale = 1.0
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
        self.image.setPixmap(
            self._source.scaled(
                size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        self.image.resize(size)
        self.scale_label.setText(f"{round(self._scale * 100)}%")


class _DetailImage(QLabel):
    def __init__(self, parent=None) -> None:
        super().__init__("暂无原始图片", parent)
        self._source = QPixmap()
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(QSize(280, 300))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("双击放大查看原始图片")
        self.setObjectName("ImagePreview")

    def set_path(self, path: Path | None) -> bool:
        self._source = QPixmap(str(path)) if path and path.is_file() else QPixmap()
        self._render()
        return not self._source.isNull()

    def resizeEvent(self, event) -> None:  # noqa: ANN001, N802
        super().resizeEvent(event)
        self._render()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: ANN001, N802
        if not self._source.isNull():
            _ImageViewerDialog(self._source, self).exec()
        super().mouseDoubleClickEvent(event)

    def _render(self) -> None:
        if self._source.isNull():
            self.setPixmap(QPixmap())
            self.setText("暂无原始图片")
            return
        self.setText("")
        self.setPixmap(
            self._source.scaled(
                self.size() - QSize(24, 24),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )


class ProblemDetailPage(QWidget):
    """A distraction-free reader shown inside the app's persistent shell."""

    back_requested = Signal()
    edit_requested = Signal(str)
    previous_requested = Signal()
    next_requested = Signal()
    schedule_review_requested = Signal(str)
    favorite_requested = Signal(str, bool)
    archive_requested = Signal(str)
    trash_requested = Signal(str)
    restore_requested = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.problem_id: str | None = None
        self.setObjectName("PageRoot")

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)

        header = QHBoxLayout()
        self.back_button = ghost_button("← 返回题库")
        self.back_button.clicked.connect(self.back_requested.emit)
        header.addWidget(self.back_button)
        titles = QVBoxLayout()
        self.title_label = QLabel("题目详情")
        self.title_label.setObjectName("PageTitle")
        self.meta_label = QLabel("")
        self.meta_label.setObjectName("PageHint")
        titles.addWidget(self.title_label)
        titles.addWidget(self.meta_label)
        header.addLayout(titles)
        header.addStretch(1)
        edit = primary_button("编辑题目")
        edit.clicked.connect(self._request_edit)
        header.addWidget(edit)
        root.addLayout(header)

        actions = QHBoxLayout()
        previous = ghost_button("← 上一题")
        previous.clicked.connect(self.previous_requested.emit)
        next_button = ghost_button("下一题 →")
        next_button.clicked.connect(self.next_requested.emit)
        actions.addWidget(previous)
        actions.addWidget(next_button)
        actions.addSpacing(12)
        self.review_button = QPushButton("加入今日复习")
        self.review_button.clicked.connect(self._request_review)
        self.favorite_button = QPushButton("收藏")
        self.favorite_button.clicked.connect(self._request_favorite)
        self.archive_button = QPushButton("归档")
        self.archive_button.clicked.connect(self._request_archive)
        self.trash_button = QPushButton("移入回收站")
        self.trash_button.setObjectName("DangerButton")
        self.trash_button.clicked.connect(self._request_trash)
        self.restore_button = primary_button("恢复到正式题库")
        self.restore_button.clicked.connect(self._request_restore)
        for button in (
            self.review_button,
            self.favorite_button,
            self.archive_button,
            self.trash_button,
            self.restore_button,
        ):
            actions.addWidget(button)
        actions.addStretch(1)
        root.addLayout(actions)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.image_card = CardFrame()
        self.image_card.add_title("原始图片")
        self.image = _DetailImage()
        self.image_card.body.addWidget(self.image, stretch=1)
        self.image_card.setMinimumWidth(300)
        splitter.addWidget(self.image_card)

        self.reader = MathContentView()
        self.reader.setMinimumWidth(520)
        splitter.addWidget(self.reader)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 5)
        splitter.setSizes([360, 820])
        root.addWidget(splitter, stretch=1)

    def set_back_text(self, text: str) -> None:
        self.back_button.setText(text)

    def set_problem(
        self,
        problem: Problem,
        *,
        image_path: Path | None = None,
        subject_name: str | None = None,
        chapter_name: str | None = None,
    ) -> None:
        self.problem_id = problem.id
        self.title_label.setText(problem.title or "无标题题目")
        status = {
            "inbox": "收件箱",
            "active": "正式题库",
            "archived": "归档",
            "trashed": "回收站",
        }.get(problem.status, problem.status)
        self.meta_label.setText(
            f"{status} · 优先级 P{problem.priority} · 已复习 {problem.review_count} 次"
        )
        is_trashed = problem.status == "trashed"
        self.review_button.setVisible(not is_trashed and problem.status != "archived")
        self.favorite_button.setVisible(not is_trashed)
        self.favorite_button.setText("取消收藏" if problem.is_favorite else "收藏")
        self.favorite_button.setProperty("targetFavorite", not problem.is_favorite)
        self.archive_button.setVisible(problem.status in {"active", "inbox"})
        self.trash_button.setVisible(not is_trashed)
        self.restore_button.setVisible(is_trashed)
        fields: dict[str, Any] = {
            column: getattr(problem, column)
            for column in (
                "title",
                "question_markdown",
                "question_latex",
                "user_answer",
                "correct_answer",
                "solution_markdown",
                "error_analysis",
                "notes",
                "problem_type",
                "priority",
                "source_book",
            )
        }
        fields["subject_name"] = subject_name
        fields["chapter_name"] = chapter_name
        self.reader.set_problem(
            fields,
            tag_names=[tag.name for tag in (problem.tags or [])],
            include_answers=True,
            show_header=False,
        )
        self.image_card.setVisible(self.image.set_path(image_path))

    def _request_edit(self) -> None:
        if self.problem_id:
            self.edit_requested.emit(self.problem_id)

    def _request_review(self) -> None:
        if self.problem_id:
            self.schedule_review_requested.emit(self.problem_id)

    def _request_favorite(self) -> None:
        if self.problem_id:
            self.favorite_requested.emit(
                self.problem_id,
                bool(self.favorite_button.property("targetFavorite")),
            )

    def _request_archive(self) -> None:
        if self.problem_id:
            self.archive_requested.emit(self.problem_id)

    def _request_trash(self) -> None:
        if self.problem_id:
            self.trash_requested.emit(self.problem_id)

    def _request_restore(self) -> None:
        if self.problem_id:
            self.restore_requested.emit(self.problem_id)
