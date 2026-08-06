"""今日复习对话框。"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QVBoxLayout,
)

from yancuo_win.application.services import AppServices
from yancuo_win.application.question_content import content_blocks_with_images
from yancuo_win.domain.review_rules import REVIEW_GRADES
from yancuo_win.domain.rules import DomainError
from yancuo_win.ui.math_content import MathContentView
from yancuo_win.ui.widgets import (
    CardFrame,
    PageHeader,
    ToastStack,
    default_button,
    primary_button,
)


class TodayReviewDialog(QDialog):
    def __init__(self, services: AppServices, parent=None) -> None:
        super().__init__(parent)
        self.services = services
        self._queue = self.services.list_due_reviews()
        self._index = 0
        self.setWindowTitle("今日复习")
        self.resize(720, 560)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(12)
        layout.addWidget(PageHeader("今日复习", "查看到期题目，独立思考后再显示答案并评分。"))
        self.progress = QLabel("")
        self.progress.setObjectName("PageHint")
        layout.addWidget(self.progress)

        self.hide_answer = QCheckBox("隐藏答案与解析")
        self.hide_answer.setChecked(True)
        self.hide_answer.stateChanged.connect(self._render)
        layout.addWidget(self.hide_answer)

        self.body = MathContentView()
        layout.addWidget(self.body)

        grade_card = CardFrame()
        grade_card.add_title("掌握程度")
        grade_row = QHBoxLayout()
        for grade, label in REVIEW_GRADES.items():
            btn = primary_button(f"{grade} {label}")
            btn.clicked.connect(lambda _=False, g=grade: self._grade(g))
            grade_row.addWidget(btn)
        grade_card.body.addLayout(grade_row)

        nav = QHBoxLayout()
        prev_btn = default_button("上一题")
        prev_btn.clicked.connect(self._prev)
        next_btn = default_button("跳过 / 下一题")
        next_btn.clicked.connect(self._next)
        nav.addWidget(prev_btn)
        nav.addWidget(next_btn)
        grade_card.body.addLayout(nav)
        layout.addWidget(grade_card)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).clicked.connect(self.accept)
        layout.addWidget(buttons)
        self.toast = ToastStack(self)
        self._render()

    def _current(self):
        if not self._queue or self._index >= len(self._queue):
            return None
        return self._queue[self._index]

    def _render(self) -> None:
        total = len(self._queue)
        self.progress.setText(f"进度 {min(self._index + 1, total)}/{total}")
        p = self._current()
        if not p:
            self.body.set_message(
                "今日复习已完成",
                "今日没有待复习题目。\n可将正式库题目“加入复习”后再来。",
            )
            return
        fields = {
            "title": p.title,
            "priority": p.priority,
            "question_markdown": p.question_markdown,
            "question_latex": p.question_latex,
            "user_answer": p.user_answer,
            "correct_answer": p.correct_answer,
            "solution_markdown": p.solution_markdown,
            "error_analysis": p.error_analysis,
            "notes": p.notes,
            "problem_type": p.problem_type,
            "source_book": p.source_book,
            "content_blocks": content_blocks_with_images(
                p.question_content_json,
                p.assets or (),
                getattr(getattr(self.services, "store", None), "resolve", None),
            ),
        }
        self.body.set_problem(
            fields,
            tag_names=[tag.name for tag in (p.tags or [])],
            include_answers=not self.hide_answer.isChecked(),
            show_header=False,
            classic=True,
        )

    def _grade(self, grade: int) -> None:
        p = self._current()
        if not p:
            return
        try:
            result = self.services.record_review(p.id, grade)
            self.toast.show_message(
                f"已记录：{result['label']} · 下次复习 {result['next_review_at'][:10]}"
            )
            # 刷新队列：去掉当前已打分项
            self._queue = self.services.list_due_reviews()
            if self._index >= len(self._queue):
                self._index = max(0, len(self._queue) - 1)
            self._render()
        except DomainError as exc:
            QMessageBox.warning(self, "无法记录", str(exc))

    def _prev(self) -> None:
        if self._index > 0:
            self._index -= 1
            self._render()

    def _next(self) -> None:
        if self._index + 1 < len(self._queue):
            self._index += 1
            self._render()
