"""In-shell review workflow with formula rendering and continuous grading."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from yancuo_win.application.services import AppServices
from yancuo_win.data.models import Problem
from yancuo_win.domain.review_rules import REVIEW_GRADES
from yancuo_win.domain.rules import DomainError
from yancuo_win.ui.math_content import MathContentView
from yancuo_win.ui.widgets import CardFrame, ghost_button, primary_button


class ReviewPage(QWidget):
    """A resumable review session that stays inside the main content area."""

    status_message = Signal(str)
    open_problem_requested = Signal(str)
    queue_changed = Signal()

    def __init__(self, services: AppServices, parent=None) -> None:
        super().__init__(parent)
        self.services = services
        self._queue: list[Problem] = []
        self._index = 0
        self._answer_visible = False
        self._session_grades: Counter[int] = Counter()
        self._session_completed = 0
        self._study_session_id: str | None = None
        self._answer_viewed_at: datetime | None = None
        self._build()
        self.reload_queue(preserve_current=False)

    def _build(self) -> None:
        self.setObjectName("PageRoot")
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)

        header = QHBoxLayout()
        titles = QVBoxLayout()
        title = QLabel("今日复习")
        title.setObjectName("PageTitle")
        self.progress_label = QLabel("")
        self.progress_label.setObjectName("PageHint")
        titles.addWidget(title)
        titles.addWidget(self.progress_label)
        header.addLayout(titles)
        header.addStretch(1)

        self.detail_button = QPushButton("打开题目详情")
        self.detail_button.clicked.connect(self._open_current_detail)
        header.addWidget(self.detail_button)
        root.addLayout(header)

        self.hero = QLabel("今日待复习")
        self.hero.setObjectName("HeroBanner")
        root.addWidget(self.hero)

        self.reader = MathContentView()
        root.addWidget(self.reader, stretch=1)

        self.grade_card = CardFrame()
        self.grade_card.add_title("完成思考后评分")
        self.grade_hint = self.grade_card.add_hint(
            "请先独立思考，再点击“显示答案与解析”；查看答案后才可评分。"
        )
        answer_row = QHBoxLayout()
        self.answer_button = primary_button("显示答案与解析")
        self.answer_button.clicked.connect(self._toggle_answer)
        answer_row.addWidget(self.answer_button)
        answer_row.addStretch(1)
        self.grade_card.body.addLayout(answer_row)
        grade_row = QHBoxLayout()
        self.grade_buttons: list[QPushButton] = []
        for grade, label in REVIEW_GRADES.items():
            button = QPushButton(f"{grade} {label}")
            button.clicked.connect(lambda _checked=False, value=grade: self._grade(value))
            button.setEnabled(False)
            grade_row.addWidget(button)
            self.grade_buttons.append(button)
        self.grade_card.body.addLayout(grade_row)

        tools = QHBoxLayout()
        self.pause_button = QPushButton("暂停当前题")
        self.pause_button.clicked.connect(self._pause_current)
        self.export_csv_button = QPushButton("导出数据表")
        self.export_csv_button.clicked.connect(self._export_csv)
        self.share_button = QPushButton("导出脱敏分享")
        self.share_button.clicked.connect(self._export_share)
        for button in (self.pause_button, self.export_csv_button, self.share_button):
            tools.addWidget(button)
        tools.addStretch(1)
        self.grade_card.body.addLayout(tools)

        nav = QHBoxLayout()
        previous = ghost_button("← 上一题")
        previous.clicked.connect(self._previous)
        skip = QPushButton("暂时跳过 / 下一题")
        skip.clicked.connect(self._skip)
        nav.addWidget(previous)
        nav.addWidget(skip)
        nav.addStretch(1)
        self.grade_card.body.addLayout(nav)
        root.addWidget(self.grade_card)

    def reload_queue(self, *, preserve_current: bool = True) -> None:
        current_id = self.current_problem_id if preserve_current else None
        try:
            self._queue = self.services.list_due_reviews()
        except DomainError as exc:
            self._queue = []
            self.reader.set_message("无法加载复习任务", str(exc))
            self.status_message.emit(str(exc))
            return
        self._index = 0
        if current_id:
            for index, problem in enumerate(self._queue):
                if problem.id == current_id:
                    self._index = index
                    break
        self._answer_visible = False
        self._render()
        self.queue_changed.emit()

    def start_session(self) -> None:
        self._session_grades.clear()
        self._session_completed = 0
        try:
            session, queue = self.services.start_study_session()
        except DomainError as exc:
            self.status_message.emit(str(exc))
            return
        self._study_session_id = session.id
        self._queue = queue
        self._index = 0
        self._answer_visible = False
        self._answer_viewed_at = None
        self._render()
        self.queue_changed.emit()

    @property
    def current_problem_id(self) -> str | None:
        problem = self._current()
        return problem.id if problem else None

    def problem_ids(self) -> list[str]:
        return [problem.id for problem in self._queue]

    def select_problem(self, problem_id: str) -> None:
        for index, problem in enumerate(self._queue):
            if problem.id == problem_id:
                self._index = index
                self._answer_visible = False
                self._render()
                break

    def _current(self) -> Problem | None:
        if not self._queue:
            return None
        self._index %= len(self._queue)
        return self._queue[self._index]

    @staticmethod
    def _fields(problem: Problem) -> dict[str, object]:
        return {
            "title": problem.title,
            "priority": problem.priority,
            "question_markdown": problem.question_markdown,
            "question_latex": problem.question_latex,
            "user_answer": problem.user_answer,
            "correct_answer": problem.correct_answer,
            "solution_markdown": problem.solution_markdown,
            "error_analysis": problem.error_analysis,
            "notes": problem.notes,
            "problem_type": problem.problem_type,
            "source_book": problem.source_book,
        }

    def _render(self) -> None:
        problem = self._current()
        remaining = len(self._queue)
        self.hero.setText(
            f"本轮已完成 {self._session_completed} 题  ·  剩余 {remaining} 题"
        )
        if not problem:
            summary = "今日没有待复习题目。"
            if self._session_completed:
                grade_summary = " · ".join(
                    f"{grade}分 {self._session_grades[grade]}题"
                    for grade in REVIEW_GRADES
                    if self._session_grades[grade]
                )
                summary = f"本轮完成 {self._session_completed} 题。{grade_summary}"
            self.progress_label.setText("今日复习已完成")
            self.reader.set_message("复习完成", summary)
            self.answer_button.setEnabled(False)
            self.grade_hint.setText("当前没有需要评分的题目。")
            self.detail_button.setEnabled(False)
            for button in self.grade_buttons:
                button.setEnabled(False)
            return

        self.progress_label.setText(
            f"当前第 {self._index + 1} / {remaining} 题 · "
            f"已复习 {problem.review_count} 次"
        )
        self.answer_button.setEnabled(True)
        self.answer_button.setText(
            "隐藏答案与解析" if self._answer_visible else "显示答案与解析"
        )
        self.grade_hint.setText(
            "答案与解析已显示，请根据掌握程度选择评分。"
            if self._answer_visible
            else "请先独立思考，再点击“显示答案与解析”；查看答案后才可评分。"
        )
        self.detail_button.setEnabled(True)
        for button in self.grade_buttons:
            button.setEnabled(self._answer_visible)
        self.reader.set_problem(
            self._fields(problem),
            tag_names=[tag.name for tag in (problem.tags or [])],
            include_answers=self._answer_visible,
        )

    def _toggle_answer(self) -> None:
        if not self._current():
            return
        self._answer_visible = not self._answer_visible
        if self._answer_visible:
            self._answer_viewed_at = datetime.now(timezone.utc)
        self._render()

    def _grade(self, grade: int) -> None:
        problem = self._current()
        if not problem or not self._answer_visible:
            return
        try:
            if self._study_session_id:
                result = self.services.record_review(
                    problem.id,
                    grade,
                    study_session_id=self._study_session_id,
                    answer_viewed_at=self._answer_viewed_at,
                )
            else:
                result = self.services.record_review(problem.id, grade)
        except DomainError as exc:
            self.status_message.emit(str(exc))
            return
        self._session_grades[grade] += 1
        self._session_completed += 1
        self._queue.pop(self._index)
        if self._queue:
            self._index %= len(self._queue)
        else:
            self._index = 0
        self._answer_visible = False
        self._answer_viewed_at = None
        self.status_message.emit(
            f"已记录：{result['label']}；下次复习 {result['next_review_at'][:10]}"
        )
        self._render()
        self.queue_changed.emit()

    def _previous(self) -> None:
        if self._queue:
            self._index = (self._index - 1) % len(self._queue)
            self._answer_visible = False
            self._render()

    def _skip(self) -> None:
        if self._queue:
            self._index = (self._index + 1) % len(self._queue)
            self._answer_visible = False
            self._render()

    def _open_current_detail(self) -> None:
        if self.current_problem_id:
            self.open_problem_requested.emit(self.current_problem_id)

    def _pause_current(self) -> None:
        problem = self._current()
        if problem is None:
            return
        try:
            self.services.set_review_enabled(problem.id, False)
        except DomainError as exc:
            self.status_message.emit(str(exc))
            return
        self._queue.pop(self._index)
        if self._queue:
            self._index %= len(self._queue)
        self._answer_visible = False
        self._answer_viewed_at = None
        self.status_message.emit("已暂停当前题的复习")
        self._render()
        self.queue_changed.emit()

    def _export_csv(self) -> None:
        if not self._study_session_id:
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出复习数据表", "review-records.csv", "CSV (*.csv)")
        if path:
            self.services.export_study_session_csv(self._study_session_id, Path(path))
            self.status_message.emit("复习数据表已导出")

    def _export_share(self) -> None:
        if not self._study_session_id:
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出脱敏分享图片", "review-summary.png", "PNG (*.png)")
        if path:
            summary = self.services.finish_study_session(self._study_session_id)
            pixmap = QPixmap(960, 420)
            pixmap.fill(QColor("#f8fafc"))
            painter = QPainter(pixmap)
            painter.setPen(QColor("#172033"))
            painter.setFont(QFont("Microsoft YaHei", 24, QFont.Weight.Bold))
            painter.drawText(48, 72, "错题本 · 复习完成")
            painter.setFont(QFont("Microsoft YaHei", 15))
            painter.drawText(48, 128, f"完成 {summary['completed_count']} / {summary['problem_count']} 题")
            painter.drawText(48, 174, "评分分布")
            x = 48
            for grade in REVIEW_GRADES:
                count = summary["grades"][grade]
                painter.setBrush(QColor("#2463eb"))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawRoundedRect(x, 210, 132, 112, 6, 6)
                painter.setPen(QColor("#ffffff"))
                painter.setFont(QFont("Microsoft YaHei", 17, QFont.Weight.Bold))
                painter.drawText(x + 18, 252, f"{grade} 分")
                painter.drawText(x + 18, 292, f"{count} 题")
                x += 156
            painter.end()
            pixmap.save(path, "PNG")
            self.status_message.emit("脱敏分享图片已导出")

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key == Qt.Key_Space:
            self._toggle_answer()
            event.accept()
            return
        if Qt.Key_1 <= key <= Qt.Key_5 and self._answer_visible:
            self._grade(key - Qt.Key_0)
            event.accept()
            return
        if key == Qt.Key_Right:
            self._skip()
            event.accept()
            return
        if key == Qt.Key_Left:
            self._previous()
            event.accept()
            return
        super().keyPressEvent(event)
