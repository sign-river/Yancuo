"""题目编辑页（嵌入主窗口，直接替换当前界面）。"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal

from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from yancuo_win.application.services import AppServices
from yancuo_win.ui.math_content import MathContentView
from yancuo_win.data.models import Problem
from yancuo_win.domain.rules import DomainError
from yancuo_win.ui.widgets import (
    CardFrame,
    IconButton,
    PageHeader,
    describe_field,
    primary_button,
    set_tab_order_chain,
)


class ProblemEditorPage(QWidget):
    """题目编辑页：保存或取消后通过信号通知主窗口返回。"""

    saved = Signal(str)
    cancelled = Signal()

    def __init__(self, services: AppServices, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.services = services
        self.problem_id: str | None = None
        self.setObjectName("PageRoot")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(12)

        self.back_button = IconButton("chevron-left", "返回")
        self.back_button.clicked.connect(self.cancelled.emit)
        self.save_button = primary_button("保存")
        self.save_button.setMinimumHeight(36)
        self.save_button.clicked.connect(self._save)
        self.header = PageHeader(
            "编辑题目",
            "修改题目内容、分类和复习状态；保存后立即同步到题库。",
        )
        self.header.add_leading(self.back_button)
        self.header.add_action(self.save_button)
        layout.addWidget(self.header)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.preview_view = MathContentView()
        # 独立缩放：固定 100% 显示，不随全局预览缩放变化
        self.preview_view.set_fixed_zoom_scale(1.0)
        # 内容高度自适应：完整显示，超过 1200px 时出现纵向滚动条
        self.preview_view.set_adaptive_content_height(1200, reserve_height=False)
        splitter.addWidget(self.preview_view)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 8, 0)
        body_layout.setSpacing(12)
        form = QFormLayout()

        self.title_edit = QLineEdit()
        self.priority = QSpinBox()
        describe_field(self.title_edit, "题目标题")
        describe_field(self.priority, "题目优先级")
        self.priority.setRange(1, 5)
        self.priority.setValue(3)

        self.status = QComboBox()
        describe_field(self.status, "题目状态")
        for st, label in (
            ("inbox", "收件箱"),
            ("active", "正式题库"),
            ("archived", "归档"),
            ("trashed", "回收站"),
        ):
            self.status.addItem(label, st)

        self.subject = QComboBox()
        describe_field(self.subject, "题目科目")
        self.subject.addItem("（未指定）", None)
        for sub in services.list_subjects():
            self.subject.addItem(sub.name, sub.id)
        self.subject.currentIndexChanged.connect(self._reload_chapters)
        self.chapter = QComboBox()
        describe_field(self.chapter, "题目章节")
        self._reload_chapters()

        form.addRow("标题", self.title_edit)
        form.addRow("优先级", self.priority)
        form.addRow("状态", self.status)
        form.addRow("科目", self.subject)
        form.addRow("章节", self.chapter)

        self.question = QTextEdit()
        self.question.setPlaceholderText("原题 Markdown / 文本")
        self.latex = QTextEdit()
        self.latex.setMaximumHeight(80)
        self.user_answer = QTextEdit()
        self.correct = QTextEdit()
        self.solution = QTextEdit()
        self.notes = QTextEdit()
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
        splitter.addWidget(scroll)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([600, 400])
        layout.addWidget(splitter, stretch=1)

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
            self.save_button,
        )

        for w in (
            self.question,
            self.latex,
            self.user_answer,
            self.correct,
            self.solution,
            self.notes,
        ):
            w.setUndoRedoEnabled(True)

        for w in (
            self.title_edit,
            self.question,
            self.latex,
            self.user_answer,
            self.correct,
            self.solution,
            self.notes,
        ):
            signal = w.textChanged if isinstance(w, (QLineEdit, QTextEdit)) else None
            if signal is not None:
                signal.connect(self._refresh_preview)
        for w in (self.priority, self.status, self.subject, self.chapter):
            if isinstance(w, QSpinBox):
                w.valueChanged.connect(self._refresh_preview)
            else:
                w.currentIndexChanged.connect(self._refresh_preview)

    def set_problem(self, problem: Problem) -> None:
        self.problem_id = problem.id
        self.title_edit.setText(problem.title or "")
        self.priority.setValue(problem.priority or 3)
        idx = self.status.findData(problem.status)
        self.status.setCurrentIndex(idx if idx >= 0 else 0)
        if problem.subject_id:
            i = self.subject.findData(problem.subject_id)
            self.subject.setCurrentIndex(i if i >= 0 else 0)
        else:
            self.subject.setCurrentIndex(0)
        self._reload_chapters()
        if problem.chapter_id:
            i = self.chapter.findData(problem.chapter_id)
            self.chapter.setCurrentIndex(i if i >= 0 else 0)
        else:
            self.chapter.setCurrentIndex(0)
        self.question.setPlainText(problem.question_markdown or "")
        self.latex.setPlainText(problem.question_latex or "")
        self.user_answer.setPlainText(problem.user_answer or "")
        self.correct.setPlainText(problem.correct_answer or "")
        self.solution.setPlainText(problem.solution_markdown or "")
        self.notes.setPlainText(problem.notes or "")
        self._refresh_preview()

    def _refresh_preview(self, *_args) -> None:
        subject_name = self.subject.currentText() if self.subject.currentData() else ""
        chapter_name = self.chapter.currentText() if self.chapter.currentData() else ""
        self.preview_view.set_problem(
            {
                "title": self.title_edit.text() or "(无标题题目)",
                "question_markdown": self.question.toPlainText(),
                "question_latex": self.latex.toPlainText().strip(),
                "user_answer": self.user_answer.toPlainText(),
                "correct_answer": self.correct.toPlainText(),
                "solution_markdown": self.solution.toPlainText(),
                "notes": self.notes.toPlainText(),
                "subject_name": subject_name,
                "chapter_name": chapter_name,
            },
        )

    def _save(self) -> None:
        if not self.problem_id:
            return
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
            self.saved.emit(self.problem_id)
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

