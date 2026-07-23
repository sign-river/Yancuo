"""Dedicated problem reading page."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from yancuo_win.data.models import Problem
from yancuo_win.ui.math_content import MathContentView
from yancuo_win.ui.widgets import CardFrame, ghost_button, primary_button


class _DetailImage(QLabel):
    def __init__(self, parent=None) -> None:
        super().__init__("暂无原始图片", parent)
        self._source = QPixmap()
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(QSize(280, 300))
        self.setStyleSheet(
            "background: #F5F7FA; border: 1px solid #E5EAF2; border-radius: 8px;"
        )

    def set_path(self, path: Path | None) -> bool:
        self._source = QPixmap(str(path)) if path and path.is_file() else QPixmap()
        self._render()
        return not self._source.isNull()

    def resizeEvent(self, event) -> None:  # noqa: ANN001, N802
        super().resizeEvent(event)
        self._render()

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

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.problem_id: str | None = None
        self.setObjectName("PageRoot")

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)

        header = QHBoxLayout()
        back = ghost_button("← 返回题库")
        back.clicked.connect(self.back_requested.emit)
        header.addWidget(back)
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
