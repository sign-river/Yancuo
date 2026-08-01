"""Human review flow for AI completion and other proposed changes."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QSplitter,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
)

from yancuo_win.application.ai_service import AIService
from yancuo_win.application.services import AppServices
from yancuo_win.domain.rules import DomainError
from yancuo_win.tasks.worker import AIJobWorker, ReviewApplicationWorker
from yancuo_win.ui.widgets import (
    ConfirmDialog,
    IconButton,
    PageHeader,
    SoftItemDelegate,
    ToastMessage,
    default_button,
    primary_button,
    set_tab_order_chain,
)


_FIELD_LABELS = {
    "title": "标题",
    "question_markdown": "题干",
    "question_latex": "公式",
    "user_answer": "用户答案",
    "correct_answer": "正确答案",
    "solution_markdown": "解析",
    "error_analysis": "错因分析",
    "notes": "备注",
    "tags": "标签",
    "priority": "优先级",
    "difficulty": "难度",
    "subject_id": "科目",
    "chapter_id": "章节",
}


class ReviewDialog(QDialog):
    """Keep all AI output as proposals until the user applies final decisions."""

    def __init__(self, ai: AIService, app: AppServices, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("ReviewDialog")
        self.ai = ai
        self.app = app
        self._decisions: dict[str, str] = {}
        self._applied_problem_ids: list[str] = []
        self._job_id: str | None = None
        self._job_worker: AIJobWorker | None = None
        self._apply_worker: ReviewApplicationWorker | None = None
        self.setWindowTitle("AI 补全审核")
        self.resize(1000, 680)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(12)
        self.header = PageHeader(
            "AI 补全审核",
            "AI 只生成建议。逐项决定后，仍需在最后一步确认写入题库。",
        )
        self.review_back_button = IconButton("chevron-left", "返回准备页")
        self.review_back_button.hide()
        self.header.add_leading(self.review_back_button)
        layout.addWidget(self.header)

        self.stack = QStackedWidget()
        self.prepare_page = self._build_prepare_page()
        self.review_page = self._build_review_page()
        self.complete_page = self._build_complete_page()
        self.stack.addWidget(self.prepare_page)
        self.stack.addWidget(self.review_page)
        self.stack.addWidget(self.complete_page)
        self.review_back_button.clicked.connect(
            lambda: self.stack.setCurrentWidget(self.prepare_page)
        )
        self.stack.currentChanged.connect(self._sync_back_button)
        layout.addWidget(self.stack, 1)
        self.toast = ToastMessage(self)
        self.refresh()

    def _build_prepare_page(self) -> QFrame:
        page = QFrame()
        body = QVBoxLayout(page)
        body.setSpacing(12)
        title = QLabel("准备审核")
        title.setObjectName("SectionTitle")
        body.addWidget(title)
        explanation = QLabel(
            "补全任务只会产生待审核建议，不会自动修改正式题目。"
            "请先阅读每项差异，分别选择采纳或保留当前内容，最后统一应用。"
        )
        explanation.setWordWrap(True)
        body.addWidget(explanation)
        self.execution_status = QLabel()
        self.execution_status.setObjectName("MutedLabel")
        self.execution_status.setWordWrap(True)
        body.addWidget(self.execution_status)
        body.addWidget(QLabel("继续审核"))
        self.resume_list = QListWidget()
        self.resume_list.setObjectName("CompletionResumeList")
        self.resume_list.setAccessibleName("可继续的 AI 补全任务")
        self.resume_list.setItemDelegate(SoftItemDelegate(self.resume_list, minimum_height=40))
        self.resume_list.currentItemChanged.connect(self._select_job)
        body.addWidget(self.resume_list, 1)
        body.addStretch(1)
        row = QHBoxLayout()
        self.begin_button = primary_button("继续审核")
        self.begin_button.clicked.connect(self._continue_review)
        self.refresh_button = default_button("刷新状态")
        self.refresh_button.clicked.connect(self.refresh)
        row.addWidget(self.begin_button)
        row.addWidget(self.refresh_button)
        row.addStretch(1)
        body.addLayout(row)
        return page

    def _build_review_page(self) -> QFrame:
        page = QFrame()
        layout = QVBoxLayout(page)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        left_box = QFrame()
        left = QVBoxLayout(left_box)
        left.addWidget(QLabel("逐项决定"))
        self.list = QListWidget()
        self.list.setObjectName("DialogItemList")
        self.list.setAccessibleName("待审核建议列表")
        self.list.setItemDelegate(SoftItemDelegate(self.list, minimum_height=40))
        self.list.currentItemChanged.connect(self._on_select)
        left.addWidget(self.list)
        right_box = QFrame()
        right = QVBoxLayout(right_box)
        self.meta = QLabel("选择一项建议查看内容")
        self.meta.setWordWrap(True)
        right.addWidget(self.meta)
        right.addWidget(QLabel("建议变更"))
        self.diff_view = QTextEdit()
        self.diff_view.setReadOnly(True)
        self.diff_view.setObjectName("DialogTextSurface")
        self.diff_view.setAccessibleName("建议变更详情")
        right.addWidget(self.diff_view)
        right.addWidget(QLabel("需要留意"))
        self.uncertain = QTextEdit()
        self.uncertain.setReadOnly(True)
        self.uncertain.setObjectName("DialogTextSurface")
        self.uncertain.setMaximumHeight(110)
        self.uncertain.setAccessibleName("需要人工确认的内容")
        right.addWidget(self.uncertain)
        splitter.addWidget(left_box)
        splitter.addWidget(right_box)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter, 1)
        actions = QHBoxLayout()
        self.accept_button = primary_button("采纳此建议")
        self.accept_button.clicked.connect(lambda: self._decide("accept"))
        self.reject_button = default_button("保留当前内容")
        self.reject_button.clicked.connect(lambda: self._decide("reject"))
        self.apply_button = primary_button("应用已确认决定")
        self.apply_button.clicked.connect(self._apply)
        for button in (self.accept_button, self.reject_button, self.apply_button):
            actions.addWidget(button)
        actions.addStretch(1)
        layout.addLayout(actions)
        set_tab_order_chain(self.list, self.diff_view, self.uncertain, self.accept_button, self.reject_button, self.apply_button)
        return page

    def _sync_back_button(self, *_args) -> None:
        self.review_back_button.setVisible(
            self.stack.currentWidget() is self.review_page
        )

    def _build_complete_page(self) -> QFrame:
        page = QFrame()
        layout = QVBoxLayout(page)
        title = QLabel("审核已应用")
        title.setObjectName("SectionTitle")
        layout.addWidget(title)
        self.complete_summary = QLabel()
        self.complete_summary.setWordWrap(True)
        layout.addWidget(self.complete_summary)
        self.undo_button = default_button("撤销本次采纳")
        self.undo_button.clicked.connect(self._undo)
        layout.addWidget(self.undo_button)
        self.close_button = primary_button("完成")
        self.close_button.clicked.connect(self.accept)
        layout.addWidget(self.close_button)
        layout.addStretch(1)
        return page

    def refresh(self) -> None:
        selected = self._job_id
        overview = self.ai.completion_review_overview()
        self.resume_list.clear()
        for job in overview:
            progress = f"已处理 {job['completed']} / {job['total']}"
            if job["failed"]:
                progress += f"，待重试 {job['failed']}"
            text = f"{job['label']} · {progress} · 待审核 {job['review_count']} 项"
            row = QListWidgetItem(text)
            row.setData(Qt.ItemDataRole.UserRole, job["job_id"])
            row.setData(Qt.ItemDataRole.UserRole + 1, job)
            self.resume_list.addItem(row)
        if not overview:
            empty = QListWidgetItem("暂无可继续的 AI 补全任务")
            empty.setFlags(empty.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self.resume_list.addItem(empty)
            self.execution_status.setText("创建补全任务后，可在这里查看进度并继续审核。")
            self.begin_button.setEnabled(False)
            self._refresh_list([])
            return
        for index in range(self.resume_list.count()):
            if self.resume_list.item(index).data(Qt.ItemDataRole.UserRole) == selected:
                self.resume_list.setCurrentRow(index)
                break
        if self.resume_list.currentRow() < 0:
            self.resume_list.setCurrentRow(0)

    def _select_job(self, current: QListWidgetItem | None, _previous) -> None:
        job = current.data(Qt.ItemDataRole.UserRole + 1) if current else None
        if not isinstance(job, dict):
            self._job_id = None
            self.begin_button.setEnabled(False)
            return
        job_id = str(job["job_id"])
        if self._job_id != job_id:
            self._decisions.clear()
        self._job_id = job_id
        self.execution_status.setText(
            f"当前任务：{job['label']}。已完成 {job['completed']} / {job['total']}，"
            f"待审核 {job['review_count']} 项。"
        )
        if job["review_count"]:
            self.begin_button.setText("继续审核建议")
        elif job["status"] in {"pending", "failed"}:
            self.begin_button.setText("开始或重试补全")
        else:
            self.begin_button.setText("等待建议生成")
        self.begin_button.setEnabled(job["status"] in {"pending", "failed", "completed"})
        self._refresh_list(self.ai.list_open_review_items_for_job(self._job_id))

    def _continue_review(self) -> None:
        if not self._job_id:
            return
        items = self.ai.list_open_review_items_for_job(self._job_id)
        if items:
            self._begin_review()
            return
        if self._job_worker and self._job_worker.isRunning():
            return
        self.begin_button.setEnabled(False)
        self.execution_status.setText("正在生成补全建议，完成后会自动进入审核。")
        self._job_worker = AIJobWorker(self.ai, self._job_id, self)
        self._job_worker.finished_ok.connect(self._on_job_done)
        self._job_worker.failed.connect(self._on_job_failed)
        self._job_worker.start()

    def _on_job_done(self, _job_id: str) -> None:
        self.refresh()
        if self._job_id and self.ai.list_open_review_items_for_job(self._job_id):
            self._begin_review()

    def _on_job_failed(self, _job_id: str, error: str) -> None:
        self.execution_status.setText(f"补全未完成：{error}")
        self.refresh_button.setFocus()

    def _begin_review(self) -> None:
        self.stack.setCurrentWidget(self.review_page)
        if self.list.count():
            self.list.setCurrentRow(0)

    def _refresh_list(self, items: list[Any]) -> None:
        current_id = self._current_id()
        self.list.clear()
        for item in items:
            card = self.ai.review_presentation(item.id)
            decision = self._decisions.get(item.id, "待决定")
            row = QListWidgetItem(f"[{decision}] {card['title']}")
            row.setData(Qt.ItemDataRole.UserRole, item.id)
            self.list.addItem(row)
        if self.list.count() == 0:
            empty = QListWidgetItem("暂无待审核建议")
            empty.setFlags(empty.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self.list.addItem(empty)
        if current_id:
            for index in range(self.list.count()):
                if self.list.item(index).data(Qt.ItemDataRole.UserRole) == current_id:
                    self.list.setCurrentRow(index)
                    break
        self._update_apply_button()

    def _current_id(self) -> str | None:
        current = self.list.currentItem()
        value = current.data(Qt.ItemDataRole.UserRole) if current else None
        return value if isinstance(value, str) else None

    def _on_select(self, current: QListWidgetItem | None, _previous) -> None:
        item_id = current.data(Qt.ItemDataRole.UserRole) if current else None
        if not isinstance(item_id, str):
            return
        try:
            card = self.ai.review_presentation(item_id)
        except DomainError:
            return
        self.meta.setText(f"{card['source']} · {card['status']}")
        self.diff_view.setPlainText(self._format_diffs(card["diffs"]))
        self.uncertain.setPlainText("\n".join(card["warnings"]) or "没有额外提示。")

    @staticmethod
    def _format_diffs(diffs: list[dict[str, Any]]) -> str:
        if not diffs:
            return "没有可应用的字段变更。"
        return "\n\n".join(
            f"{_FIELD_LABELS.get(str(diff['field']), str(diff['field']))}\n"
            f"当前：{diff['before']}\n建议：{diff['after']}"
            for diff in diffs
        )

    def _decide(self, decision: str) -> None:
        item_id = self._current_id()
        if item_id is None:
            return
        self._decisions[item_id] = decision
        self._refresh_list(
            self.ai.list_open_review_items_for_job(self._job_id)
            if self._job_id
            else []
        )
        self._update_apply_button()
        self.toast.show_message("已记录你的决定，尚未写入题库。")

    def _update_apply_button(self) -> None:
        item_ids = [
            self.list.item(index).data(Qt.ItemDataRole.UserRole)
            for index in range(self.list.count())
            if isinstance(self.list.item(index).data(Qt.ItemDataRole.UserRole), str)
        ]
        self.apply_button.setEnabled(bool(item_ids) and all(item_id in self._decisions for item_id in item_ids))

    def _apply(self) -> None:
        if not ConfirmDialog.ask(self, "应用审核决定", "确认后才会把采纳的建议写入正式题目，并保留可撤销的版本记录。", "确认应用"):
            return
        self.apply_button.setEnabled(False)
        self.meta.setText("正在应用已确认决定，请稍候。")
        self._apply_worker = ReviewApplicationWorker(self.ai, self._decisions, self)
        self._apply_worker.finished_ok.connect(self._on_apply_done)
        self._apply_worker.failed.connect(self._on_apply_failed)
        self._apply_worker.start()

    def _on_apply_done(self, result: object) -> None:
        if not isinstance(result, dict):
            QMessageBox.warning(self, "无法应用", "审核结果无效")
            self.apply_button.setEnabled(True)
            return
        self._applied_problem_ids = result["accepted_problem_ids"]
        self.complete_summary.setText(
            f"已采纳 {len(self._applied_problem_ids)} 项建议，保留当前内容 {len(result['rejected_item_ids'])} 项。"
        )
        self.undo_button.setEnabled(bool(self._applied_problem_ids))
        self.stack.setCurrentWidget(self.complete_page)

    def _on_apply_failed(self, error: str) -> None:
        QMessageBox.warning(self, "无法应用", error)
        self.apply_button.setEnabled(True)

    def _undo(self) -> None:
        try:
            undone = self.ai.undo_review_accepts(self._applied_problem_ids)
        except DomainError as exc:
            QMessageBox.warning(self, "无法撤销", str(exc))
            return
        self.undo_button.setEnabled(False)
        self.complete_summary.setText(f"已撤销本次应用的 {undone} 项 AI 补全。")
