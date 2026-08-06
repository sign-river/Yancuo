"""Human review flow for AI completion and other proposed changes."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
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
from yancuo_win.ui.math_content import MathContentView
from yancuo_win.ui.widgets import (
    ConfirmDialog,
    IconButton,
    PageHeader,
    SoftItemDelegate,
    ToastStack,
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
    "source_book": "来源",
}

_FIELD_GROUPS = {
    "answer": {"user_answer", "correct_answer", "solution_markdown", "error_analysis"},
    "classification": {"subject_id", "chapter_id", "tags", "priority", "difficulty"},
    "source": {"source_book", "notes"},
    "content": {"title", "question_markdown", "question_latex"},
}

_GROUP_LABELS = {
    "content": "题目内容",
    "answer": "答案与解析",
    "classification": "分类标签",
    "source": "来源信息",
}


def _field_group(field: str) -> str:
    return next(
        (group for group, fields in _FIELD_GROUPS.items() if field in fields),
        "content",
    )


class ReviewDialog(QDialog):
    """Keep all AI output as proposals until the user applies final decisions."""

    background_requested = Signal()
    applied = Signal(list)
    review_ready = Signal(str, int)

    def __init__(self, ai: AIService, app: AppServices, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("ReviewDialog")
        self.ai = ai
        self.app = app
        self._decisions: dict[tuple[str, str], str] = {}
        self._edited_values: dict[tuple[str, str], Any] = {}
        self._applied_problem_ids: list[str] = []
        self._job_id: str | None = None
        self._job_worker: AIJobWorker | None = None
        self._apply_worker: ReviewApplicationWorker | None = None
        self._pending_problem_ids: list[str] = []
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
        self.review_back_button.clicked.connect(self._go_back)
        self.stack.currentChanged.connect(self._sync_back_button)
        layout.addWidget(self.stack, 1)
        self.toast = ToastStack(self)
        self.refresh()

    def prepare_new(self, problem_ids: list[str], problem_labels: list[str]) -> None:
        """Show a non-mutating preparation step for a new completion batch."""

        self._pending_problem_ids = list(problem_ids)
        self._job_id = None
        self._decisions.clear()
        self._edited_values.clear()
        self.prepare_title.setText("准备 AI 补全")
        self.prepare_explanation.setText(
            "AI 只会生成待审核建议，不会自动修改正式题目。"
            "分析完成后，你可以逐项采用、保留或编辑，再一次性应用。"
        )
        shown = "、".join(problem_labels[:3])
        if len(problem_labels) > 3:
            shown += f" 等 {len(problem_labels)} 道题"
        self.problem_summary.setText(f"当前题目：{shown or '未选择'}")
        model = (
            getattr(self.ai.runtime.settings.ai, "default_text_model", "")
            or getattr(self.ai.runtime.settings.ai, "default_vision_model", "")
            or "未配置"
        )
        self.model_summary.setText(
            f"模型：{model} · 只读取题目的结构化文字与题图引用；"
            "耗时和费用受 AI 设置中的限额控制。"
        )
        self.user_instruction.clear()
        self.new_task_panel.show()
        self.resume_title.hide()
        self.resume_list.hide()
        self.begin_button.hide()
        self.refresh_button.hide()
        self.background_button.hide()
        self.cancel_job_button.hide()
        self.execution_status.clear()
        self.stack.setCurrentWidget(self.prepare_page)
        self._sync_back_button()

    def show_queue(self, *, preferred_job_id: str | None = None) -> None:
        self._pending_problem_ids.clear()
        self._job_id = preferred_job_id
        self.prepare_title.setText("AI 补全任务")
        self.prepare_explanation.setText(
            "选择任务查看进度或继续审核；正式题目只会在最终确认后修改。"
        )
        self.new_task_panel.hide()
        self.resume_title.show()
        self.resume_list.show()
        self.begin_button.show()
        self.refresh_button.show()
        self.background_button.hide()
        self.cancel_job_button.hide()
        self.stack.setCurrentWidget(self.prepare_page)
        self.refresh()
        self._sync_back_button()

    def _start_new_completion(self) -> None:
        allowed_fields: set[str] = set()
        for group, checkbox in self.field_groups.items():
            if checkbox.isChecked():
                allowed_fields.update(_FIELD_GROUPS[group])
        if not allowed_fields:
            self.execution_status.setText("请至少选择一组要检查的内容。")
            return
        try:
            job = self.ai.create_structure_job(
                self._pending_problem_ids,
                user_instruction=self.user_instruction.toPlainText(),
                allowed_fields=allowed_fields,
            )
        except DomainError as exc:
            self.execution_status.setText(f"无法开始分析：{exc}")
            return
        self._job_id = job.id
        self.start_analysis_button.setEnabled(False)
        self.new_task_panel.setEnabled(False)
        self.background_button.show()
        self.cancel_job_button.show()
        self.execution_status.setText("正在准备结构化题目和补全范围…")
        self._continue_review()

    def _cancel_current_job(self) -> None:
        if not self._job_id:
            return
        if self._job_worker and self._job_worker.isRunning():
            self._job_worker.cancel()
        try:
            self.ai.cancel_job(self._job_id)
        except DomainError as exc:
            self.execution_status.setText(f"无法取消任务：{exc}")
            return
        self.execution_status.setText("任务已取消。补全范围和补充要求已保留，可重新开始。")
        self._job_id = None
        self.start_analysis_button.setEnabled(True)
        self.new_task_panel.setEnabled(True)
        self.background_button.hide()
        self.cancel_job_button.hide()

    def _build_prepare_page(self) -> QFrame:
        page = QFrame()
        body = QVBoxLayout(page)
        body.setSpacing(12)
        self.prepare_title = QLabel("准备审核")
        self.prepare_title.setObjectName("SectionTitle")
        body.addWidget(self.prepare_title)
        self.prepare_explanation = QLabel(
            "补全任务只会产生待审核建议，不会自动修改正式题目。"
            "请先阅读每项差异，分别选择采纳或保留当前内容，最后统一应用。"
        )
        self.prepare_explanation.setWordWrap(True)
        body.addWidget(self.prepare_explanation)

        self.new_task_panel = QFrame()
        self.new_task_panel.setObjectName("CardFrame")
        new_task = QVBoxLayout(self.new_task_panel)
        new_task.setContentsMargins(16, 14, 16, 14)
        new_task.setSpacing(10)
        self.problem_summary = QLabel()
        self.problem_summary.setWordWrap(True)
        new_task.addWidget(self.problem_summary)
        new_task.addWidget(QLabel("AI 将检查和补全"))
        self.field_groups: dict[str, QCheckBox] = {}
        for key, text, checked in (
            ("answer", "答案与解析", True),
            ("classification", "分类与标签", True),
            ("source", "来源信息", True),
            ("content", "题目内容（已有内容也需逐项审核）", False),
        ):
            checkbox = QCheckBox(text)
            checkbox.setChecked(checked)
            self.field_groups[key] = checkbox
            new_task.addWidget(checkbox)
        self.model_summary = QLabel()
        self.model_summary.setObjectName("MutedLabel")
        self.model_summary.setWordWrap(True)
        new_task.addWidget(self.model_summary)
        self.user_instruction = QTextEdit()
        self.user_instruction.setPlaceholderText("可选：补充本次检查要求")
        self.user_instruction.setMaximumHeight(88)
        new_task.addWidget(self.user_instruction)
        start_row = QHBoxLayout()
        self.start_analysis_button = primary_button("开始分析")
        self.start_analysis_button.clicked.connect(self._start_new_completion)
        start_row.addWidget(self.start_analysis_button)
        start_row.addStretch(1)
        new_task.addLayout(start_row)
        self.new_task_panel.hide()
        body.addWidget(self.new_task_panel)

        self.execution_status = QLabel()
        self.execution_status.setObjectName("MutedLabel")
        self.execution_status.setWordWrap(True)
        body.addWidget(self.execution_status)
        self.resume_title = QLabel("继续审核")
        body.addWidget(self.resume_title)
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
        self.background_button = default_button("后台运行")
        self.background_button.clicked.connect(self.background_requested.emit)
        self.background_button.hide()
        row.addWidget(self.background_button)
        self.cancel_job_button = default_button("取消任务")
        self.cancel_job_button.clicked.connect(self._cancel_current_job)
        self.cancel_job_button.hide()
        row.addWidget(self.cancel_job_button)
        row.addStretch(1)
        body.addLayout(row)
        return page

    def _build_review_page(self) -> QFrame:
        page = QFrame()
        layout = QVBoxLayout(page)
        self.review_summary = QLabel("正在整理 AI 建议…")
        self.review_summary.setObjectName("SectionTitle")
        self.review_summary.setWordWrap(True)
        layout.addWidget(self.review_summary)
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
        self.diff_view = MathContentView()
        self.diff_view.setObjectName("DialogTextSurface")
        self.diff_view.set_accessible_content(
            "建议变更详情", "渲染后的当前内容与 AI 建议对照"
        )
        right.addWidget(self.diff_view)
        self.uncertain_title = QLabel("需要留意")
        right.addWidget(self.uncertain_title)
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
        self.accept_all_button = default_button("采纳全部安全项")
        self.accept_all_button.clicked.connect(lambda: self._decide_all("accept"))
        self.reject_all_button = default_button("全部保留")
        self.reject_all_button.clicked.connect(lambda: self._decide_all("reject"))
        self.accept_button = primary_button("采纳此建议")
        self.accept_button.clicked.connect(lambda: self._decide("accept"))
        self.reject_button = default_button("保留当前内容")
        self.reject_button.clicked.connect(lambda: self._decide("reject"))
        self.edit_accept_button = default_button("编辑后采用")
        self.edit_accept_button.clicked.connect(self._edit_and_accept)
        self.apply_button = primary_button("应用已确认决定")
        self.apply_button.clicked.connect(self._apply)
        for button in (
            self.accept_all_button,
            self.reject_all_button,
            self.accept_button,
            self.reject_button,
            self.edit_accept_button,
            self.apply_button,
        ):
            actions.addWidget(button)
        actions.addStretch(1)
        layout.addLayout(actions)
        set_tab_order_chain(
            self.list,
            self.diff_view,
            self.uncertain,
            self.accept_button,
            self.reject_button,
            self.edit_accept_button,
            self.apply_button,
        )
        return page

    def _sync_back_button(self, *_args) -> None:
        current = self.stack.currentWidget()
        self.review_back_button.setVisible(current is not self.complete_page)
        label = "返回准备页" if current is self.review_page else "返回上一页"
        self.review_back_button.setToolTip(label)
        self.review_back_button.setAccessibleName(label)

    def _go_back(self) -> None:
        if self.stack.currentWidget() is self.review_page:
            self.stack.setCurrentWidget(self.prepare_page)
            return
        self.background_requested.emit()

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
            text = (
                f"{job.get('title', '题目信息补全')} · {job['label']} · "
                f"{progress} · 建议 {job['review_count']} 项 · 继续审核"
            )
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
            self._edited_values.clear()
        self._job_id = job_id
        self.execution_status.setText(
            f"当前任务：{job.get('title', '题目信息补全')} · {job['label']}。"
            f"已完成 {job['completed']} / {job['total']}，"
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
        self.new_task_panel.setEnabled(True)
        self.start_analysis_button.setEnabled(True)
        self.background_button.hide()
        self.cancel_job_button.hide()
        self.refresh()
        items = (
            self.ai.list_open_review_items_for_job(self._job_id)
            if self._job_id
            else []
        )
        if self._job_id and items:
            if not self.isVisible():
                self.review_ready.emit(self._job_id, len(items))
            self._begin_review()

    def _on_job_failed(self, _job_id: str, error: str) -> None:
        self.execution_status.setText(f"补全未完成：{error}")
        self.new_task_panel.setEnabled(True)
        self.start_analysis_button.setEnabled(True)
        self.background_button.hide()
        self.cancel_job_button.hide()
        self.refresh_button.setFocus()

    def _begin_review(self) -> None:
        self.stack.setCurrentWidget(self.review_page)
        if self.list.count():
            self.list.setCurrentRow(0)

    def _refresh_list(self, items: list[Any]) -> None:
        current_key = self._current_key()
        self.list.clear()
        additions = 0
        modifications = 0
        uncertain_count = 0
        for item in items:
            card = self.ai.review_presentation(item.id)
            uncertain_count += len(card["warnings"])
            for diff in card["diffs"]:
                if diff["before"] in (None, "", []):
                    additions += 1
                else:
                    modifications += 1
                field = str(diff["field"])
                key = (item.id, field)
                decision = self._decisions.get(key, "待决定")
                group = _GROUP_LABELS[_field_group(field)]
                label = _FIELD_LABELS.get(field, "其他内容")
                row = QListWidgetItem(
                    f"[{decision}] {group} · {label} · {card['title']}"
                )
                row.setData(Qt.ItemDataRole.UserRole, item.id)
                row.setData(Qt.ItemDataRole.UserRole + 1, field)
                row.setData(Qt.ItemDataRole.UserRole + 2, card["status"])
                self.list.addItem(row)
        self.review_summary.setText(
            f"AI 建议修改 {modifications} 项、补充 {additions} 项，"
            f"仍有 {uncertain_count} 项需要特别核对。"
        )
        if self.list.count() == 0:
            empty = QListWidgetItem("暂无待审核建议")
            empty.setFlags(empty.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self.list.addItem(empty)
        if current_key:
            for index in range(self.list.count()):
                row = self.list.item(index)
                if (
                    row.data(Qt.ItemDataRole.UserRole),
                    row.data(Qt.ItemDataRole.UserRole + 1),
                ) == current_key:
                    self.list.setCurrentRow(index)
                    break
        self._update_apply_button()

    def _current_key(self) -> tuple[str, str] | None:
        current = self.list.currentItem()
        item_id = current.data(Qt.ItemDataRole.UserRole) if current else None
        field = current.data(Qt.ItemDataRole.UserRole + 1) if current else None
        if isinstance(item_id, str) and isinstance(field, str):
            return item_id, field
        return None

    def _current_id(self) -> str | None:
        key = self._current_key()
        return key[0] if key else None

    def _on_select(self, current: QListWidgetItem | None, _previous) -> None:
        key = self._current_key()
        if key is None:
            return
        item_id, field = key
        try:
            card = self.ai.review_presentation(item_id)
        except DomainError:
            return
        selected = [diff for diff in card["diffs"] if str(diff["field"]) == field]
        self.meta.setText(
            f"{card['title']} · {_GROUP_LABELS[_field_group(field)]} · "
            f"{card['source']} · {card['status']}"
        )
        self.diff_view.set_message("建议变更", self._format_diffs(selected))
        has_warnings = bool(card["warnings"])
        self.uncertain_title.setVisible(has_warnings)
        self.uncertain.setVisible(has_warnings)
        self.uncertain.setPlainText("\n".join(card["warnings"]))
        conflict = card["status"] == "存在并发变更"
        self.accept_button.setEnabled(not conflict)
        self.edit_accept_button.setEnabled(not conflict)
        if conflict:
            self.meta.setText(self.meta.text() + " · 请保留当前内容后重新分析")

    @staticmethod
    def _format_diffs(diffs: list[dict[str, Any]]) -> str:
        if not diffs:
            return "没有可应用的字段变更。"
        def readable(value: Any, *, empty: str) -> str:
            if value in (None, "", []):
                return empty
            if isinstance(value, list):
                return "、".join(str(item) for item in value) or empty
            if isinstance(value, dict):
                return "结构化内容（可在技术详情中检查）"
            return str(value)

        return "\n\n".join(
            f"### {_FIELD_LABELS.get(str(diff['field']), '其他内容')}\n\n"
            f"**当前**\n\n{readable(diff['before'], empty='当前未填写')}\n\n"
            f"**AI 建议**\n\n{readable(diff['after'], empty='建议保持为空')}"
            for diff in diffs
        )

    def _decide(self, decision: str) -> None:
        key = self._current_key()
        if key is None:
            return
        self._decisions[key] = decision
        if decision == "reject":
            self._edited_values.pop(key, None)
        self._refresh_list(
            self.ai.list_open_review_items_for_job(self._job_id)
            if self._job_id
            else []
        )
        self._update_apply_button()
        self.toast.show_message("已记录你的决定，尚未写入题库。")

    def _decide_all(self, decision: str) -> None:
        skipped = 0
        for index in range(self.list.count()):
            row = self.list.item(index)
            item_id = row.data(Qt.ItemDataRole.UserRole)
            field = row.data(Qt.ItemDataRole.UserRole + 1)
            if not isinstance(item_id, str) or not isinstance(field, str):
                continue
            if (
                decision == "accept"
                and row.data(Qt.ItemDataRole.UserRole + 2) == "存在并发变更"
            ):
                skipped += 1
                continue
            self._decisions[(item_id, field)] = decision
            if decision == "reject":
                self._edited_values.pop((item_id, field), None)
        self._refresh_list(
            self.ai.list_open_review_items_for_job(self._job_id)
            if self._job_id
            else []
        )
        suffix = f"；跳过 {skipped} 个冲突字段" if skipped else ""
        self.toast.show_message(f"已批量记录决定，尚未写入题库{suffix}。")

    def _edit_and_accept(self) -> None:
        key = self._current_key()
        if key is None:
            return
        item_id, field = key
        card = self.ai.review_presentation(item_id)
        diff = next(
            (value for value in card["diffs"] if str(value["field"]) == field),
            None,
        )
        if diff is None:
            return
        original = diff["after"]
        value, accepted = QInputDialog.getMultiLineText(
            self,
            f"编辑{_FIELD_LABELS.get(field, '其他内容')}",
            "确认后仍需点击“应用已确认修改”才会写入题库：",
            str(original),
        )
        if not accepted:
            return
        edited: Any = value
        if isinstance(original, list):
            edited = [part.strip() for part in value.split(",") if part.strip()]
        elif isinstance(original, int):
            try:
                edited = int(value)
            except ValueError:
                self.toast.show_message("请输入有效整数。")
                return
        self._edited_values[key] = edited
        self._decisions[key] = "accept"
        self._refresh_list(
            self.ai.list_open_review_items_for_job(self._job_id)
            if self._job_id
            else []
        )
        self.toast.show_message("已保存编辑值，尚未写入题库。")

    def _update_apply_button(self) -> None:
        keys = [
            (
                self.list.item(index).data(Qt.ItemDataRole.UserRole),
                self.list.item(index).data(Qt.ItemDataRole.UserRole + 1),
            )
            for index in range(self.list.count())
            if isinstance(self.list.item(index).data(Qt.ItemDataRole.UserRole), str)
            and isinstance(
                self.list.item(index).data(Qt.ItemDataRole.UserRole + 1), str
            )
        ]
        accepted = sum(self._decisions.get(key) == "accept" for key in keys)
        self.apply_button.setText(f"应用已确认修改（{accepted} 项）")
        self.apply_button.setEnabled(
            bool(keys) and all(key in self._decisions for key in keys)
        )

    def _apply(self) -> None:
        if not ConfirmDialog.ask(self, "应用审核决定", "确认后才会把采纳的建议写入正式题目，并保留可撤销的版本记录。", "确认应用"):
            return
        self.apply_button.setEnabled(False)
        self.meta.setText("正在应用已确认决定，请稍候。")
        grouped: dict[str, dict[str, dict[str, Any]]] = {}
        for (item_id, field), decision in self._decisions.items():
            field_decision: dict[str, Any] = {"decision": decision}
            if (item_id, field) in self._edited_values:
                field_decision["value"] = self._edited_values[(item_id, field)]
            grouped.setdefault(item_id, {})[field] = field_decision
        self._apply_worker = ReviewApplicationWorker(self.ai, grouped, self)
        self._apply_worker.finished_ok.connect(self._on_apply_done)
        self._apply_worker.failed.connect(self._on_apply_failed)
        self._apply_worker.start()

    def _on_apply_done(self, result: object) -> None:
        if not isinstance(result, dict):
            QMessageBox.warning(self, "无法应用", "审核结果无效")
            self.apply_button.setEnabled(True)
            return
        self._applied_problem_ids = result["accepted_problem_ids"]
        self.applied.emit(list(self._applied_problem_ids))
        accepted_count = int(
            result.get("accepted_field_count", len(self._applied_problem_ids))
        )
        rejected_count = int(
            result.get("rejected_field_count", len(result["rejected_item_ids"]))
        )
        self.complete_summary.setText(
            f"已采纳 {accepted_count} 项修改，保留当前内容 {rejected_count} 项。"
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
