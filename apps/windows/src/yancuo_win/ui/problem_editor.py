"""题目编辑对话框。"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QScrollArea,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from yancuo_win.application.services import AppServices
from yancuo_win.data.models import Problem
from yancuo_win.domain.rules import DomainError
from yancuo_win.ui.widgets import (
    CardFrame,
    PageHeader,
    describe_field,
    set_tab_order_chain,
)


class ProblemEditorDialog(QDialog):
    def __init__(self, services: AppServices, problem: Problem, parent=None) -> None:
        super().__init__(parent)
        self.services = services
        self.problem_id = problem.id
        self.setWindowTitle(f"编辑题目 · {problem.title or problem.id[:12]}")
        self.resize(720, 640)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(12)
        layout.addWidget(
            PageHeader("编辑题目", "修改题目内容、分类和复习状态；保存后立即同步到题库。")
        )
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 8, 0)
        body_layout.setSpacing(12)
        form = QFormLayout()

        self.title_edit = QLineEdit(problem.title or "")
        self.priority = QSpinBox()
        describe_field(self.title_edit, "题目标题")
        describe_field(self.priority, "题目优先级")
        self.priority.setRange(1, 5)
        self.priority.setValue(problem.priority or 3)

        self.status = QComboBox()
        describe_field(self.status, "题目状态")
        for st, label in (
            ("inbox", "收件箱"),
            ("active", "正式题库"),
            ("archived", "归档"),
            ("trashed", "回收站"),
        ):
            self.status.addItem(label, st)
        idx = self.status.findData(problem.status)
        if idx >= 0:
            self.status.setCurrentIndex(idx)

        self.subject = QComboBox()
        describe_field(self.subject, "题目科目")
        self.subject.addItem("（未指定）", None)
        for sub in services.list_subjects():
            self.subject.addItem(sub.name, sub.id)
        if problem.subject_id:
            i = self.subject.findData(problem.subject_id)
            if i >= 0:
                self.subject.setCurrentIndex(i)
        self.chapter = QComboBox()
        describe_field(self.chapter, "题目章节")
        self.subject.currentIndexChanged.connect(self._reload_chapters)
        self._reload_chapters()
        if problem.chapter_id:
            i = self.chapter.findData(problem.chapter_id)
            if i >= 0:
                self.chapter.setCurrentIndex(i)

        form.addRow("标题", self.title_edit)
        form.addRow("优先级", self.priority)
        form.addRow("状态", self.status)
        form.addRow("科目", self.subject)
        form.addRow("章节", self.chapter)

        self.question = QTextEdit(problem.question_markdown or "")
        self.question.setPlaceholderText("原题 Markdown / 文本")
        self.latex = QTextEdit(problem.question_latex or "")
        self.latex.setMaximumHeight(80)
        self.user_answer = QTextEdit(problem.user_answer or "")
        self.correct = QTextEdit(problem.correct_answer or "")
        self.solution = QTextEdit(problem.solution_markdown or "")
        self.notes = QTextEdit(problem.notes or "")
        describe_field(self.question, "题干")
        describe_field(self.latex, "题干 LaTeX")
        describe_field(self.user_answer, "我的作答")
        describe_field(self.correct, "正确答案")
        describe_field(self.solution, "题目解析")
        describe_field(self.notes, "题目备注")

        basic = CardFrame()
        basic.add_title("基本信息")
        basic.body.addLayout(form)
        body_layout.addWidget(basic)
        content = CardFrame()
        content.add_title("题干")
        content.body.addWidget(self.question)
        content.body.addWidget(QLabel("LaTeX（可选）"))
        content.body.addWidget(self.latex)
        body_layout.addWidget(content)
        row = QHBoxLayout()
        answers = CardFrame()
        answers.add_title("作答与答案")
        left = QVBoxLayout()
        left.addWidget(QLabel("我的作答"))
        left.addWidget(self.user_answer)
        right = QVBoxLayout()
        right.addWidget(QLabel("正确答案"))
        right.addWidget(self.correct)
        row.addLayout(left)
        row.addLayout(right)
        answers.body.addLayout(row)
        body_layout.addWidget(answers)
        analysis = CardFrame()
        analysis.add_title("解析与备注")
        analysis.body.addWidget(QLabel("解析"))
        analysis.body.addWidget(self.solution)
        analysis.body.addWidget(QLabel("备注"))
        analysis.body.addWidget(self.notes)
        body_layout.addWidget(analysis)
        body_layout.addStretch(1)
        scroll.setWidget(body)
        layout.addWidget(scroll, stretch=1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        save_button = buttons.button(QDialogButtonBox.StandardButton.Save)
        cancel_button = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        set_tab_order_chain(
            self.title_edit,
            self.priority,
            self.status,
            self.subject,
            self.chapter,
            self.question,
            self.latex,
            self.user_answer,
            self.correct,
            self.solution,
            self.notes,
            save_button,
            cancel_button,
        )

        # 编辑器级撤销：QTextEdit 自带 Undo
        for w in (
            self.question,
            self.latex,
            self.user_answer,
            self.correct,
            self.solution,
            self.notes,
        ):
            w.setUndoRedoEnabled(True)

    def _save(self) -> None:
        try:
            fields = {
                "title": self.title_edit.text().strip() or None,
                "priority": self.priority.value(),
                "question_markdown": self.question.toPlainText(),
                "question_latex": self.latex.toPlainText(),
                "user_answer": self.user_answer.toPlainText(),
                "correct_answer": self.correct.toPlainText(),
                "solution_markdown": self.solution.toPlainText(),
                "notes": self.notes.toPlainText(),
            }
            self.services.update_problem(self.problem_id, fields)
            self.services.move_problems_to_category(
                [self.problem_id],
                subject_id=self.subject.currentData(),
                chapter_id=self.chapter.currentData(),
            )
            new_status = self.status.currentData()
            current = self.services.get_problem(self.problem_id)
            if current and new_status and current.status != new_status:
                self.services.set_problem_status(self.problem_id, new_status)
            self.accept()
        except DomainError as exc:
            QMessageBox.warning(self, "无法保存", str(exc))

    def _reload_chapters(self) -> None:
        current = self.chapter.currentData() if hasattr(self, "chapter") else None
        self.chapter.clear()
        self.chapter.addItem("（未分类）", None)
        subject_id = self.subject.currentData()
        if subject_id:
            for choice in self.services.list_category_choices():
                if choice.subject_id == subject_id and choice.chapter_id is not None:
                    self.chapter.addItem(" / ".join(choice.chapter_path), choice.chapter_id)
        index = self.chapter.findData(current)
        self.chapter.setCurrentIndex(index if index >= 0 else 0)
