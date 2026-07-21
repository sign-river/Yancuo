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
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
)

from yancuo_win.application.services import AppServices
from yancuo_win.data.models import Problem
from yancuo_win.domain.rules import DomainError


class ProblemEditorDialog(QDialog):
    def __init__(self, services: AppServices, problem: Problem, parent=None) -> None:
        super().__init__(parent)
        self.services = services
        self.problem_id = problem.id
        self.setWindowTitle(f"编辑题目 · {problem.title or problem.id[:12]}")
        self.resize(720, 640)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.title_edit = QLineEdit(problem.title or "")
        self.priority = QSpinBox()
        self.priority.setRange(1, 5)
        self.priority.setValue(problem.priority or 3)

        self.status = QComboBox()
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
        self.subject.addItem("（未指定）", None)
        for sub in services.list_subjects():
            self.subject.addItem(sub.name, sub.id)
        if problem.subject_id:
            i = self.subject.findData(problem.subject_id)
            if i >= 0:
                self.subject.setCurrentIndex(i)

        form.addRow("标题", self.title_edit)
        form.addRow("优先级", self.priority)
        form.addRow("状态", self.status)
        form.addRow("科目", self.subject)

        self.question = QTextEdit(problem.question_markdown or "")
        self.question.setPlaceholderText("原题 Markdown / 文本")
        self.latex = QTextEdit(problem.question_latex or "")
        self.latex.setMaximumHeight(80)
        self.user_answer = QTextEdit(problem.user_answer or "")
        self.correct = QTextEdit(problem.correct_answer or "")
        self.solution = QTextEdit(problem.solution_markdown or "")
        self.error = QTextEdit(problem.error_analysis or "")
        self.notes = QTextEdit(problem.notes or "")

        layout.addLayout(form)
        layout.addWidget(QLabel("原题"))
        layout.addWidget(self.question)
        layout.addWidget(QLabel("LaTeX"))
        layout.addWidget(self.latex)
        row = QHBoxLayout()
        left = QVBoxLayout()
        left.addWidget(QLabel("我的作答"))
        left.addWidget(self.user_answer)
        right = QVBoxLayout()
        right.addWidget(QLabel("正确答案"))
        right.addWidget(self.correct)
        row.addLayout(left)
        row.addLayout(right)
        layout.addLayout(row)
        layout.addWidget(QLabel("解析"))
        layout.addWidget(self.solution)
        layout.addWidget(QLabel("错因"))
        layout.addWidget(self.error)
        layout.addWidget(QLabel("备注"))
        layout.addWidget(self.notes)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        # 编辑器级撤销：QTextEdit 自带 Undo
        for w in (
            self.question,
            self.latex,
            self.user_answer,
            self.correct,
            self.solution,
            self.error,
            self.notes,
        ):
            w.setUndoRedoEnabled(True)

    def _save(self) -> None:
        try:
            fields = {
                "title": self.title_edit.text().strip() or None,
                "priority": self.priority.value(),
                "subject_id": self.subject.currentData(),
                "question_markdown": self.question.toPlainText(),
                "question_latex": self.latex.toPlainText(),
                "user_answer": self.user_answer.toPlainText(),
                "correct_answer": self.correct.toPlainText(),
                "solution_markdown": self.solution.toPlainText(),
                "error_analysis": self.error.toPlainText(),
                "notes": self.notes.toPlainText(),
            }
            self.services.update_problem(self.problem_id, fields)
            new_status = self.status.currentData()
            current = self.services.get_problem(self.problem_id)
            if current and new_status and current.status != new_status:
                self.services.set_problem_status(self.problem_id, new_status)
            self.accept()
        except DomainError as exc:
            QMessageBox.warning(self, "无法保存", str(exc))
