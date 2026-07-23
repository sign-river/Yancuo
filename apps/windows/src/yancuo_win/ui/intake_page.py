"""Task-oriented problem intake page.

Manual entry and AI-assisted entry live in one persistent page stack.  The
user never needs to navigate through the library, task center, and review
dialog to finish recording a new problem.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from yancuo_win.application.intake_service import (
    IntakeCandidate,
    ProblemIntakeService,
)
from yancuo_win.domain.rules import DomainError
from yancuo_win.tasks.worker import AIJobWorker
from yancuo_win.ui.math_content import MathContentView
from yancuo_win.ui.widgets import CardFrame, danger_button, ghost_button, primary_button


_PAGE_HOME = 0
_PAGE_MANUAL = 1
_PAGE_AI_UPLOAD = 2
_PAGE_AI_PROCESSING = 3
_PAGE_AI_CONFIRM = 4
_PAGE_DONE = 5


class ImagePreviewLabel(QLabel):
    """Aspect-ratio-preserving preview that follows the available panel size."""

    def __init__(self, empty_text: str, parent=None) -> None:
        super().__init__(empty_text, parent)
        self.empty_text = empty_text
        self.source = QPixmap()
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(QSize(360, 260))
        self.setStyleSheet(
            "background: #F5F7FA; border: 1px solid #E5EAF2; border-radius: 8px;"
        )

    def set_path(self, path: Path | None) -> bool:
        self.source = QPixmap(str(path)) if path is not None else QPixmap()
        self._render()
        return not self.source.isNull()

    def clear_preview(self) -> None:
        self.source = QPixmap()
        self._render()

    def resizeEvent(self, event) -> None:  # noqa: ANN001, N802
        super().resizeEvent(event)
        self._render()

    def _render(self) -> None:
        if self.source.isNull():
            self.setPixmap(QPixmap())
            self.setText(self.empty_text)
            return
        target = self.size() - QSize(24, 24)
        self.setText("")
        self.setPixmap(
            self.source.scaled(
                target,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )


class ProblemForm(QWidget):
    """Reusable inline form shared by manual entry and AI confirmation."""

    changed = Signal()

    def __init__(self, intake: ProblemIntakeService, parent=None) -> None:
        super().__init__(parent)
        self.intake = intake
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        basic = CardFrame()
        basic.add_title("基本归属")
        form = QFormLayout()
        form.setSpacing(10)
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("例如：换元积分中遗漏绝对值")
        self.subject = QComboBox()
        self.chapter = QComboBox()
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
        form.addRow("标题", self.title_edit)
        form.addRow("科目", self.subject)
        form.addRow("章节", self.chapter)
        form.addRow("题型", self.problem_type)
        form.addRow("优先级", self.priority)
        form.addRow("来源书籍", self.source_book)
        form.addRow("来源年份", self.source_year)
        form.addRow("原题题号", self.original_number)
        form.addRow("标签", self.tags)
        basic.body.addLayout(form)
        root.addWidget(basic)

        content = CardFrame()
        content.add_title("题目内容")
        self.question = self._text_area("题干 Markdown / 文本", 150)
        self.latex = self._text_area("公式 LaTeX（可留空）", 78)
        content.body.addWidget(QLabel("题干"))
        content.body.addWidget(self.question)
        content.body.addWidget(QLabel("LaTeX"))
        content.body.addWidget(self.latex)
        root.addWidget(content)

        answer = CardFrame()
        answer.add_title("作答与解析")
        self.user_answer = self._text_area("我的作答", 90)
        self.correct_answer = self._text_area("正确答案", 90)
        self.solution = self._text_area("完整解析", 130)
        self.error_analysis = self._text_area("错因分析", 90)
        self.notes = self._text_area("备注", 80)
        for label, editor in (
            ("我的作答", self.user_answer),
            ("正确答案", self.correct_answer),
            ("解析", self.solution),
            ("错因", self.error_analysis),
            ("备注", self.notes),
        ):
            answer.body.addWidget(QLabel(label))
            answer.body.addWidget(editor)
        root.addWidget(answer)
        root.addStretch(1)

        self.subject.currentIndexChanged.connect(self._reload_chapters)
        self.reload_catalog()
        self._connect_change_signals()

    def _connect_change_signals(self) -> None:
        def notify(*_args) -> None:
            self.changed.emit()

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
            self.latex,
            self.user_answer,
            self.correct_answer,
            self.solution,
            self.error_analysis,
            self.notes,
        ):
            editor.textChanged.connect(notify)
        self.subject.currentIndexChanged.connect(notify)
        self.chapter.currentIndexChanged.connect(notify)
        self.priority.valueChanged.connect(notify)

    @staticmethod
    def _text_area(placeholder: str, height: int) -> QTextEdit:
        editor = QTextEdit()
        editor.setPlaceholderText(placeholder)
        editor.setMinimumHeight(height)
        editor.setUndoRedoEnabled(True)
        return editor

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
        self.chapter.clear()
        self.chapter.addItem("（未指定）", None)
        subject_id = self.subject.currentData()
        if subject_id:
            for chapter in self.intake.app.list_chapters(subject_id):
                self.chapter.addItem(chapter.name, chapter.id)
        index = self.chapter.findData(current)
        self.chapter.setCurrentIndex(index if index >= 0 else 0)

    @staticmethod
    def _optional(text: str) -> str | None:
        value = text.strip()
        return value or None

    def values(self) -> dict[str, Any]:
        return {
            "title": self._optional(self.title_edit.text()),
            "subject_id": self.subject.currentData(),
            "chapter_id": self.chapter.currentData(),
            "problem_type": self._optional(self.problem_type.text()),
            "priority": self.priority.value(),
            "source_book": self._optional(self.source_book.text()),
            "source_year": self._optional(self.source_year.text()),
            "original_number": self._optional(self.original_number.text()),
            "question_markdown": self.question.toPlainText(),
            "question_latex": self.latex.toPlainText(),
            "user_answer": self.user_answer.toPlainText(),
            "correct_answer": self.correct_answer.toPlainText(),
            "solution_markdown": self.solution.toPlainText(),
            "error_analysis": self.error_analysis.toPlainText(),
            "notes": self.notes.toPlainText(),
        }

    def tag_names(self) -> list[str]:
        text = self.tags.text().replace("，", ",")
        return [part.strip() for part in text.split(",") if part.strip()]

    def set_values(self, values: dict[str, Any]) -> None:
        self.reload_catalog()
        self.title_edit.setText(str(values.get("title") or ""))
        subject_id = values.get("subject_id")
        if not subject_id and values.get("subject_name"):
            for index in range(self.subject.count()):
                if self.subject.itemText(index) == str(values["subject_name"]):
                    subject_id = self.subject.itemData(index)
                    break
        index = self.subject.findData(subject_id)
        self.subject.setCurrentIndex(index if index >= 0 else 0)
        self._reload_chapters()
        chapter_id = values.get("chapter_id")
        if not chapter_id and values.get("chapter_name"):
            for idx in range(self.chapter.count()):
                if self.chapter.itemText(idx) == str(values["chapter_name"]):
                    chapter_id = self.chapter.itemData(idx)
                    break
        chapter_index = self.chapter.findData(chapter_id)
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
        self.latex.setPlainText(str(values.get("question_latex") or ""))
        self.user_answer.setPlainText(str(values.get("user_answer") or ""))
        self.correct_answer.setPlainText(str(values.get("correct_answer") or ""))
        self.solution.setPlainText(str(values.get("solution_markdown") or ""))
        self.error_analysis.setPlainText(str(values.get("error_analysis") or ""))
        self.notes.setPlainText(str(values.get("notes") or ""))
        tags = values.get("tags")
        self.tags.setText(", ".join(str(tag) for tag in tags) if isinstance(tags, list) else "")

    def clear(self) -> None:
        self.set_values({})


class IntakePage(QWidget):
    problem_committed = Signal(str)
    status_message = Signal(str)
    dashboard_requested = Signal()
    open_problem_requested = Signal(str)

    def __init__(self, intake: ProblemIntakeService, parent=None) -> None:
        super().__init__(parent)
        self.intake = intake
        self.manual_images: list[Path] = []
        self.ai_files: list[Path] = []
        self.ai_job_id: str | None = None
        self.ai_worker: AIJobWorker | None = None
        self.ai_candidates: list[IntakeCandidate] = []
        self.candidate_index = 0
        self.last_problem_id: str | None = None

        self.preview_timer = QTimer(self)
        self.preview_timer.setSingleShot(True)
        self.preview_timer.setInterval(180)
        self.preview_timer.timeout.connect(self._refresh_ai_preview)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_home())
        self.stack.addWidget(self._build_manual())
        self.stack.addWidget(self._build_ai_upload())
        self.stack.addWidget(self._build_processing())
        self.stack.addWidget(self._build_confirmation())
        self.stack.addWidget(self._build_done())
        root.addWidget(self.stack)

        self.progress_timer = QTimer(self)
        self.progress_timer.setInterval(500)
        self.progress_timer.timeout.connect(self._poll_progress)
        self._restore_existing_session()
        self.show_home()

    @staticmethod
    def _scroll(widget: QWidget) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(widget)
        return scroll

    def _header(self, title_text: str, hint_text: str, back_slot) -> QHBoxLayout:
        header = QHBoxLayout()
        back = ghost_button("← 返回")
        back.clicked.connect(back_slot)
        header.addWidget(back)
        labels = QVBoxLayout()
        title = QLabel(title_text)
        title.setObjectName("PageTitle")
        hint = QLabel(hint_text)
        hint.setObjectName("PageHint")
        hint.setWordWrap(True)
        labels.addWidget(title)
        labels.addWidget(hint)
        header.addLayout(labels)
        header.addStretch(1)
        return header

    def _page(self) -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        page.setObjectName("PageRoot")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)
        return page, layout

    def _build_home(self) -> QWidget:
        page, layout = self._page()
        title = QLabel("录题")
        title.setObjectName("PageTitle")
        hint = QLabel("选择一种方式开始。后续步骤会在这里连续完成，不需要切换到其他页面。")
        hint.setObjectName("PageHint")
        layout.addWidget(title)
        layout.addWidget(hint)

        row = QHBoxLayout()
        manual = CardFrame()
        manual.add_title("手动录题")
        manual.add_hint("在完整表单中填写题干、答案、分类和标签，确认后一次性入库。")
        manual_button = primary_button("开始手动录题")
        manual_button.clicked.connect(self.show_manual)
        manual.body.addWidget(manual_button)
        row.addWidget(manual, stretch=1)

        ai = CardFrame()
        ai.add_title("AI 录题")
        ai.add_hint("上传图片并描述目标特征，AI 自动提取和整理，最后在同一页面确认。")
        ai_button = primary_button("上传图片识别")
        ai_button.clicked.connect(self.show_ai_upload)
        ai.body.addWidget(ai_button)
        row.addWidget(ai, stretch=1)
        layout.addLayout(row)

        self.resume_card = CardFrame()
        self.resume_card.add_title("未完成的本次会话")
        self.resume_hint = self.resume_card.add_hint("")
        self.resume_manual = QPushButton("继续手动录题")
        self.resume_manual.clicked.connect(self.show_manual)
        self.resume_ai = QPushButton("继续 AI 录题")
        self.resume_ai.clicked.connect(self._resume_ai)
        resume_row = QHBoxLayout()
        resume_row.addWidget(self.resume_manual)
        resume_row.addWidget(self.resume_ai)
        resume_row.addStretch(1)
        self.resume_card.body.addLayout(resume_row)
        layout.addWidget(self.resume_card)
        layout.addStretch(1)
        return page

    def _build_manual(self) -> QWidget:
        page, layout = self._page()
        layout.addLayout(
            self._header(
                "手动录题",
                "表单只在点击“确认入库”时创建正式题目；返回不会产生空白题。",
                self.show_home,
            )
        )
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 8, 0)
        self.manual_form = ProblemForm(self.intake)
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
        actions.addWidget(QLabel("返回后本次表单仍会保留"))
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
        layout.addLayout(
            self._header(
                "AI 录题 · 上传",
                "添加图片并说明目标特征，例如“红圈处是错题”或“只提取第 3 题”。",
                self.show_home,
            )
        )
        upload = CardFrame()
        upload.add_title("1. 添加图片")
        upload.add_hint("当前版本每张图片生成一个候选题；一图多题将在后续数据模型升级中接入。")
        upload_content = QHBoxLayout()
        self.ai_upload_preview = ImagePreviewLabel("选择图片后将在这里预览")
        upload_content.addWidget(self.ai_upload_preview, stretch=2)
        self.ai_file_list = QListWidget()
        self.ai_file_list.setObjectName("UploadFileList")
        self.ai_file_list.setViewMode(QListView.ViewMode.IconMode)
        self.ai_file_list.setResizeMode(QListView.ResizeMode.Adjust)
        self.ai_file_list.setMovement(QListView.Movement.Static)
        self.ai_file_list.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.ai_file_list.setIconSize(QSize(128, 88))
        self.ai_file_list.setGridSize(QSize(154, 122))
        self.ai_file_list.setMinimumSize(QSize(330, 260))
        self.ai_file_list.currentRowChanged.connect(self._show_ai_file_preview)
        upload_content.addWidget(self.ai_file_list, stretch=1)
        upload.body.addLayout(upload_content)
        file_actions = QHBoxLayout()
        add = primary_button("选择图片")
        add.clicked.connect(self._add_ai_files)
        remove = QPushButton("移除选中")
        remove.clicked.connect(self._remove_ai_files)
        file_actions.addWidget(add)
        file_actions.addWidget(remove)
        file_actions.addStretch(1)
        upload.body.addLayout(file_actions)
        layout.addWidget(upload)

        prompt = CardFrame()
        prompt.add_title("2. 告诉 AI 如何定位题目")
        prompt.add_hint("这是本批图片的补充说明；程序仍会强制结构化输出和字段安全规则。")
        self.ai_instruction = QTextEdit()
        self.ai_instruction.setPlaceholderText(
            "例如：画红圈的是目标错题；蓝色手写内容是我的作答；不要提取页脚答案。"
        )
        self.ai_instruction.setMaximumHeight(120)
        prompt.body.addWidget(self.ai_instruction)
        templates = QHBoxLayout()
        for text in ("红圈处是目标错题", "只提取第 3 题", "手写内容是我的作答"):
            button = ghost_button(text)
            button.clicked.connect(lambda _checked=False, value=text: self._append_instruction(value))
            templates.addWidget(button)
        templates.addStretch(1)
        prompt.body.addLayout(templates)
        layout.addWidget(prompt)
        layout.addStretch(1)

        start_row = QHBoxLayout()
        self.ai_config_hint = QLabel()
        self.ai_config_hint.setObjectName("PageHint")
        start_row.addWidget(self.ai_config_hint)
        start_row.addStretch(1)
        start = primary_button("开始识别")
        start.clicked.connect(self._start_ai)
        start_row.addWidget(start)
        layout.addLayout(start_row)
        return page

    def _build_processing(self) -> QWidget:
        page, layout = self._page()
        layout.addLayout(
            self._header(
                "AI 录题 · 后台处理中",
                "可以返回录题首页，任务仍会继续；再次进入即可查看当前进度。",
                self.show_home,
            )
        )
        card = CardFrame()
        card.add_title("正在整理图片")
        self.processing_status = card.add_hint("正在准备任务…")
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        card.body.addWidget(self.progress_bar)
        self.processing_steps = QLabel(
            "保存原图  →  提取题目与公式  →  推荐分类和标签  →  生成待确认表单"
        )
        self.processing_steps.setWordWrap(True)
        card.body.addWidget(self.processing_steps)
        self.processing_error = QLabel("")
        self.processing_error.setWordWrap(True)
        self.processing_error.setStyleSheet("color: #F54A45;")
        card.body.addWidget(self.processing_error)
        actions = QHBoxLayout()
        cancel = danger_button("取消后台任务")
        cancel.clicked.connect(self._cancel_ai)
        self.processing_back = QPushButton("返回修改上传内容")
        self.processing_back.clicked.connect(self.show_ai_upload)
        self.processing_back.setVisible(False)
        actions.addWidget(cancel)
        actions.addWidget(self.processing_back)
        actions.addStretch(1)
        card.body.addLayout(actions)
        layout.addWidget(card)
        layout.addStretch(1)
        return page

    def _build_confirmation(self) -> QWidget:
        page, layout = self._page()
        layout.addLayout(
            self._header(
                "AI 录题 · 确认结果",
                "先在“阅读预览”核对公式和内容；如需调整，切换到“编辑字段”后再入库。",
                self.show_home,
            )
        )
        self.candidate_counter = QLabel("")
        self.candidate_counter.setObjectName("PageHint")
        layout.addWidget(self.candidate_counter)
        splitter = QSplitter(Qt.Orientation.Horizontal)

        left = CardFrame()
        left.add_title("原始图片与识别提示")
        self.image_preview = QLabel("无原图预览")
        self.image_preview.setMinimumSize(QSize(340, 360))
        self.image_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_preview.setStyleSheet("background: #F5F7FA; border-radius: 8px;")
        left.body.addWidget(self.image_preview, stretch=1)
        self.uncertain_label = QLabel("")
        self.uncertain_label.setWordWrap(True)
        self.uncertain_label.setObjectName("PageHint")
        left.body.addWidget(self.uncertain_label)
        splitter.addWidget(left)

        self.ai_result_tabs = QTabWidget()
        self.ai_result_tabs.setObjectName("AIResultTabs")

        preview_host = QWidget()
        preview_layout = QVBoxLayout(preview_host)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        self.ai_result_preview = MathContentView()
        self.ai_result_preview.setMinimumSize(QSize(520, 440))
        preview_layout.addWidget(self.ai_result_preview)
        self.ai_result_tabs.addTab(preview_host, "阅读预览")

        form_host = QWidget()
        form_layout = QVBoxLayout(form_host)
        form_layout.setContentsMargins(0, 0, 8, 0)
        self.ai_form = ProblemForm(self.intake)
        self.ai_form.changed.connect(self._queue_ai_preview)
        form_layout.addWidget(self.ai_form)
        self.ai_result_tabs.addTab(self._scroll(form_host), "编辑字段")
        self.ai_result_tabs.currentChanged.connect(self._on_ai_result_tab_changed)
        splitter.addWidget(self.ai_result_tabs)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter, stretch=1)

        actions = QHBoxLayout()
        previous = QPushButton("上一题")
        previous.clicked.connect(lambda: self._move_candidate(-1))
        next_button = QPushButton("下一题")
        next_button.clicked.connect(lambda: self._move_candidate(1))
        skip = danger_button("跳过此题")
        skip.clicked.connect(self._reject_candidate)
        confirm = primary_button("确认入库")
        confirm.clicked.connect(self._commit_candidate)
        actions.addWidget(previous)
        actions.addWidget(next_button)
        actions.addStretch(1)
        actions.addWidget(skip)
        actions.addWidget(confirm)
        layout.addLayout(actions)
        return page

    def _build_done(self) -> QWidget:
        page, layout = self._page()
        layout.addStretch(1)
        card = CardFrame()
        card.add_title("录题完成")
        self.done_message = card.add_hint("")
        actions = QHBoxLayout()
        manual = primary_button("继续手动录题")
        manual.clicked.connect(self._new_manual)
        ai = QPushButton("继续 AI 录题")
        ai.clicked.connect(self._new_ai)
        view = QPushButton("查看刚入库的题目")
        view.clicked.connect(self._open_last_problem)
        finish = QPushButton("返回工作台")
        finish.clicked.connect(self.dashboard_requested.emit)
        for button in (manual, ai, view, finish):
            actions.addWidget(button)
        card.body.addLayout(actions)
        layout.addWidget(card)
        layout.addStretch(2)
        return page

    def show_home(self) -> None:
        has_manual = bool(
            self.manual_form.title_edit.text().strip()
            or self.manual_form.question.toPlainText().strip()
            or self.manual_images
        ) if hasattr(self, "manual_form") else False
        has_ai = bool(self.ai_job_id or self.ai_files)
        self.resume_card.setVisible(has_manual or has_ai)
        states = []
        if has_manual:
            states.append("手动表单尚未提交")
        if self.ai_job_id:
            states.append("AI 任务可以继续查看")
        elif self.ai_files:
            states.append("AI 上传列表尚未开始")
        self.resume_hint.setText("；".join(states))
        self.resume_manual.setVisible(has_manual)
        self.resume_ai.setVisible(has_ai)
        self.stack.setCurrentIndex(_PAGE_HOME)

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
        self.stack.setCurrentIndex(_PAGE_AI_UPLOAD)

    def _resume_ai(self) -> None:
        if self.ai_candidates:
            self._load_candidate()
            self.stack.setCurrentIndex(_PAGE_AI_CONFIRM)
        elif self.ai_job_id:
            self.stack.setCurrentIndex(_PAGE_AI_PROCESSING)
            if not (self.ai_worker and self.ai_worker.isRunning()):
                self._start_worker(self.ai_job_id)
            else:
                self.progress_timer.start()
                self._poll_progress()
        else:
            self.show_ai_upload()

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

    def _remove_manual_images(self) -> None:
        rows = sorted({self.manual_asset_list.row(item) for item in self.manual_asset_list.selectedItems()}, reverse=True)
        for row in rows:
            self.manual_asset_list.takeItem(row)
            self.manual_images.pop(row)

    def _clear_manual(self) -> None:
        if (
            QMessageBox.question(self, "清空表单", "清空当前尚未入库的内容？")
            != QMessageBox.StandardButton.Yes
        ):
            return
        self.manual_form.clear()
        self.manual_images.clear()
        self.manual_asset_list.clear()

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
        self.problem_committed.emit(problem.id)
        self._show_done(f"“{problem.title or '无标题题目'}”已进入正式题库。")

    def _add_ai_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "选择需要 AI 整理的图片",
            "",
            "Images (*.png *.jpg *.jpeg *.webp);;All (*.*)",
        )
        invalid: list[str] = []
        first_added_row: int | None = None
        for value in files:
            path = Path(value)
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
            self.ai_upload_preview.clear_preview()

    def _show_ai_file_preview(self, row: int) -> None:
        if 0 <= row < len(self.ai_files):
            if self.ai_upload_preview.set_path(self.ai_files[row]):
                return
        self.ai_upload_preview.clear_preview()

    def _append_instruction(self, text: str) -> None:
        current = self.ai_instruction.toPlainText().strip()
        self.ai_instruction.setPlainText(f"{current}\n{text}".strip())

    def _start_ai(self) -> None:
        if self.ai_worker and self.ai_worker.isRunning():
            self.stack.setCurrentIndex(_PAGE_AI_PROCESSING)
            return
        try:
            started = self.intake.start_ai(
                self.ai_files,
                user_instruction=self.ai_instruction.toPlainText(),
            )
        except DomainError as exc:
            QMessageBox.warning(self, "无法开始识别", str(exc))
            return
        self.ai_job_id = started.job_id
        self.ai_candidates.clear()
        self.processing_error.clear()
        self.processing_back.setVisible(False)
        self.stack.setCurrentIndex(_PAGE_AI_PROCESSING)
        self._start_worker(started.job_id)
        self.status_message.emit("AI 录题任务已在后台开始")

    def _start_worker(self, job_id: str) -> None:
        self.ai_worker = AIJobWorker(self.intake.ai, job_id, self)
        self.ai_worker.finished_ok.connect(self._on_ai_done)
        self.ai_worker.failed.connect(self._on_ai_failed)
        self.ai_worker.start()
        self.progress_timer.start()
        self._poll_progress()

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
        labels = {
            "pending": "任务正在排队",
            "running": "AI 正在提取题目并整理字段",
            "completed": "识别完成，正在准备确认表单",
            "cancelled": "任务已取消",
        }
        self.processing_status.setText(
            f"{labels.get(progress.status, progress.status)} · "
            f"完成 {progress.done} / {progress.total} · 失败 {progress.failed}"
        )
        if progress.status in {"completed", "cancelled"}:
            self.progress_timer.stop()

    def _cancel_ai(self) -> None:
        if self.ai_worker and self.ai_worker.isRunning():
            self.ai_worker.cancel()
            self.processing_status.setText("正在取消任务…")

    def _on_ai_done(self, job_id: str) -> None:
        if job_id != self.ai_job_id:
            return
        self.progress_timer.stop()
        self._poll_progress()
        try:
            self.ai_candidates = self.intake.list_candidates(job_id)
            failures = self.intake.failed_items(job_id)
        except DomainError as exc:
            self._on_ai_failed(job_id, str(exc))
            return
        if not self.ai_candidates:
            detail = "\n".join(failures[:5]) or "AI 没有生成可确认的题目。"
            self.processing_error.setText(detail)
            self.processing_back.setVisible(True)
            self.status_message.emit("AI 识别未生成候选题")
            return
        self.candidate_index = 0
        self._load_candidate()
        self.stack.setCurrentIndex(_PAGE_AI_CONFIRM)
        self.status_message.emit(
            f"AI 已完成，生成 {len(self.ai_candidates)} 道待确认题目"
        )

    def _on_ai_failed(self, job_id: str, error: str) -> None:
        if self.ai_job_id and job_id != self.ai_job_id:
            return
        self.progress_timer.stop()
        self.processing_error.setText(error)
        self.processing_back.setVisible(True)
        self.stack.setCurrentIndex(_PAGE_AI_PROCESSING)
        self.status_message.emit(f"AI 录题失败：{error}")

    def _load_candidate(self) -> None:
        if not self.ai_candidates:
            return
        self.candidate_index %= len(self.ai_candidates)
        candidate = self.ai_candidates[self.candidate_index]
        self.candidate_counter.setText(
            f"待确认 {self.candidate_index + 1} / {len(self.ai_candidates)}"
        )
        self.ai_form.set_values(candidate.fields)
        self._refresh_ai_preview()
        if candidate.original_image and candidate.original_image.is_file():
            pixmap = QPixmap(str(candidate.original_image))
            if pixmap.isNull():
                self.image_preview.setText(str(candidate.original_image))
            else:
                self.image_preview.setPixmap(
                    pixmap.scaled(
                        QSize(420, 500),
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )
        else:
            self.image_preview.setPixmap(QPixmap())
            self.image_preview.setText("无原图预览")
        if candidate.uncertain:
            self.uncertain_label.setText(
                "AI 不确定字段：\n"
                + json.dumps(candidate.uncertain, ensure_ascii=False, indent=2)
            )
            self.uncertain_label.setStyleSheet("color: #B26A00;")
        else:
            self.uncertain_label.setText("未报告不确定字段")
            self.uncertain_label.setStyleSheet("")

    def _queue_ai_preview(self) -> None:
        self.preview_timer.start()

    def _on_ai_result_tab_changed(self, index: int) -> None:
        if index == 0:
            self._refresh_ai_preview()

    def _refresh_ai_preview(self) -> None:
        if not hasattr(self, "ai_result_preview") or not hasattr(self, "ai_form"):
            return
        fields = self.ai_form.values()
        if self.ai_form.subject.currentData():
            fields["subject_name"] = self.ai_form.subject.currentText()
        if self.ai_form.chapter.currentData():
            fields["chapter_name"] = self.ai_form.chapter.currentText()
        self.ai_result_preview.set_problem(
            fields,
            tag_names=self.ai_form.tag_names(),
            include_answers=True,
        )

    def _move_candidate(self, delta: int) -> None:
        if not self.ai_candidates:
            return
        self.candidate_index = (self.candidate_index + delta) % len(self.ai_candidates)
        self._load_candidate()

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
            self.ai_job_id = None
            self.ai_files.clear()
            self.ai_file_list.clear()
            self.ai_upload_preview.clear_preview()
            self.ai_instruction.clear()
            self._show_done(f"“{problem.title or 'AI 识别题目'}”已进入正式题库。")

    def _reject_candidate(self) -> None:
        if not self.ai_candidates:
            return
        if (
            QMessageBox.question(self, "跳过候选题", "跳过并移除此候选题？")
            != QMessageBox.StandardButton.Yes
        ):
            return
        candidate = self.ai_candidates[self.candidate_index]
        try:
            self.intake.reject_ai_candidate(candidate.review_item_id)
        except DomainError as exc:
            QMessageBox.warning(self, "无法跳过", str(exc))
            return
        self.ai_candidates.pop(self.candidate_index)
        if self.ai_candidates:
            self.candidate_index %= len(self.ai_candidates)
            self._load_candidate()
        else:
            self.ai_job_id = None
            self.processing_error.setText("本批候选题均已跳过。可以返回并重新上传。")
            self.processing_back.setVisible(True)
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
        self.ai_files.clear()
        self.ai_file_list.clear()
        self.ai_upload_preview.clear_preview()
        self.ai_instruction.clear()
        self.show_ai_upload()

    def _open_last_problem(self) -> None:
        if self.last_problem_id:
            self.open_problem_requested.emit(self.last_problem_id)

    def shutdown(self) -> None:
        """Stop the page-owned worker before the application destroys Qt objects."""

        self.progress_timer.stop()
        if self.ai_worker and self.ai_worker.isRunning():
            self.ai_worker.cancel()
            self.ai_worker.wait(3000)
