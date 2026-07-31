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
from yancuo_win.ui.widgets import (
    ConfirmDialog,
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
        self.setWindowTitle("AI 补全审核")
        self.resize(1000, 680)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(12)
        layout.addWidget(PageHeader("AI 补全审核", "AI 只生成建议。逐项决定后，仍需在最后一步确认写入题库。"))

        self.stack = QStackedWidget()
        self.prepare_page = self._build_prepare_page()
        self.review_page = self._build_review_page()
        self.complete_page = self._build_complete_page()
        self.stack.addWidget(self.prepare_page)
        self.stack.addWidget(self.review_page)
        self.stack.addWidget(self.complete_page)
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
        body.addStretch(1)
        row = QHBoxLayout()
        self.begin_button = primary_button("开始审核建议")
        self.begin_button.clicked.connect(self._begin_review)
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
        self.back_button = default_button("返回准备页")
        self.back_button.clicked.connect(lambda: self.stack.setCurrentWidget(self.prepare_page))
        self.apply_button = primary_button("应用已确认决定")
        self.apply_button.clicked.connect(self._apply)
        for button in (self.accept_button, self.reject_button, self.back_button, self.apply_button):
            actions.addWidget(button)
        actions.addStretch(1)
        layout.addLayout(actions)
        set_tab_order_chain(self.list, self.diff_view, self.uncertain, self.accept_button, self.reject_button, self.apply_button)
        return page

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
        cards = self.ai.list_open_review_items()
        latest = self.ai.list_jobs(limit=1)
        if latest:
            diagnostics = self.ai.get_job_diagnostics(latest[0].id)
            self.execution_status.setText(f"最近一次补全任务：{diagnostics['label']}。待审核建议：{len(cards)} 项。")
        else:
            self.execution_status.setText(f"当前没有补全任务。待审核建议：{len(cards)} 项。")
        self.begin_button.setEnabled(bool(cards))
        self._refresh_list(cards)

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
        self._refresh_list(self.ai.list_open_review_items())
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
        try:
            result = self.ai.apply_review_decisions(dict(self._decisions))
        except DomainError as exc:
            QMessageBox.warning(self, "无法应用", str(exc))
            self.refresh()
            return
        self._applied_problem_ids = result["accepted_problem_ids"]
        self.complete_summary.setText(
            f"已采纳 {len(self._applied_problem_ids)} 项建议，保留当前内容 {len(result['rejected_item_ids'])} 项。"
        )
        self.undo_button.setEnabled(bool(self._applied_problem_ids))
        self.stack.setCurrentWidget(self.complete_page)

    def _undo(self) -> None:
        try:
            undone = self.ai.undo_review_accepts(self._applied_problem_ids)
        except DomainError as exc:
            QMessageBox.warning(self, "无法撤销", str(exc))
            return
        self.undo_button.setEnabled(False)
        self.complete_summary.setText(f"已撤销本次应用的 {undone} 项 AI 补全。")
