"""任务队列页：题目 / 笔记 / AI 三个队列子界面，支持进入任务详情。"""

from __future__ import annotations

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from yancuo_win.application.ai_service import AIService
from yancuo_win.tasks.ai_coordinator import AIJobCoordinator
from yancuo_win.ui.widgets import (
    PageHeader,
    SoftItemDelegate,
    danger_button,
    default_button,
    primary_button,
)

_QUEUE_LABELS = {
    "question": "题目队列",
    "note": "笔记队列",
    "ai": "AI 队列",
}


def _queue_filter(queue: str):
    if queue == "question":
        return lambda job: job.domain == "question_intake"
    if queue == "note":
        return lambda job: job.domain == "note_intake"
    return lambda job: True


def _job_label(job, *, with_domain: bool = False) -> str:
    domain = f" · {job.domain}" if with_domain and job.domain else ""
    cost = float(job.estimated_cost or 0.0)
    return (
        f"[{job.status}]{domain} {job.job_type} · {job.provider} · "
        f"{int(job.done_items or 0)}/{int(job.total_items or 0)} · "
        f"fail={int(job.failed_items or 0)} · "
        f"cost≈{cost:.4f} · {job.id[:18]}"
    )


class AIJobDetailDialog(QDialog):
    """Streaming AI job detail: metadata plus live response text."""

    def __init__(
        self,
        ai: AIService,
        job_id: str,
        coordinator: AIJobCoordinator | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("AIJobDetailDialog")
        self.ai = ai
        self.job_id = job_id
        self.coordinator = coordinator
        self.setWindowTitle("AI 任务详情")
        self.resize(680, 520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(12)
        layout.addWidget(PageHeader("AI 任务详情", "实时查看 AI 任务的流式回复与执行状态。"))

        self.meta = QLabel("")
        self.meta.setObjectName("MutedLabel")
        self.meta.setWordWrap(True)
        layout.addWidget(self.meta)

        self.response = QTextEdit()
        self.response.setReadOnly(True)
        self.response.setPlaceholderText("AI 回复将在这里流式显示…")
        layout.addWidget(self.response, stretch=1)

        actions = QFrame()
        actions.setObjectName("DialogActionBar")
        row = QHBoxLayout(actions)
        row.setContentsMargins(12, 8, 12, 8)
        row.setSpacing(8)
        refresh = default_button("刷新")
        refresh.clicked.connect(self._refresh)
        cancel_btn = danger_button("取消任务")
        cancel_btn.clicked.connect(self._cancel)
        row.addWidget(refresh)
        row.addWidget(cancel_btn)
        row.addStretch(1)
        layout.addWidget(actions)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        close_button = buttons.button(QDialogButtonBox.StandardButton.Close)
        close_button.setText("关闭")
        close_button.clicked.connect(self.accept)
        layout.addWidget(buttons)

        self._timer = QTimer(self)
        self._timer.setInterval(800)
        self._timer.timeout.connect(self._refresh)
        self._timer.start()
        self._refresh()

    def _refresh(self) -> None:
        job = self.ai.get_job(self.job_id)
        if job is None:
            self.meta.setText("任务不存在或已被清理。")
            self._timer.stop()
            return
        self.meta.setText(
            f"任务：{job.id}\n"
            f"类型：{job.job_type} · 领域：{job.domain} · 提供商：{job.provider} · 模型：{job.model}\n"
            f"状态：{job.status} · 进度：{int(job.done_items or 0)}/{int(job.total_items or 0)} · "
            f"失败：{int(job.failed_items or 0)} · 费用≈{float(job.estimated_cost or 0):.4f} 元"
        )
        if job.error_message:
            self.meta.setText(self.meta.text() + f"\n错误：{job.error_message}")
        self.response.setPlainText(job.response_text or "")
        if job.status in ("done", "failed", "canceled", "cancelled"):
            self._timer.stop()

    def _cancel(self) -> None:
        if self.coordinator is not None:
            self.coordinator.cancel(self.job_id)
            self._refresh()


class _QueuePane(QWidget):
    """One queue tab: task list with enter / run / cancel actions."""

    open_requested = Signal(str, str)  # (queue, job_id)

    def __init__(
        self,
        ai: AIService,
        coordinator: AIJobCoordinator,
        queue: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.ai = ai
        self.coordinator = coordinator
        self.queue = queue
        self._with_domain = queue == "ai"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.list = QListWidget()
        self.list.setObjectName("DialogItemList")
        self.list.setAccessibleName(f"{_QUEUE_LABELS[queue]}任务")
        self.list.setUniformItemSizes(True)
        self.list.setMouseTracking(True)
        self.list.setItemDelegate(SoftItemDelegate(self.list, minimum_height=40))
        self.list.currentItemChanged.connect(self._show_selected)
        layout.addWidget(self.list, stretch=1)

        self.preview = QTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setPlaceholderText("选择任务后查看已接收的 AI 回复")
        self.preview.setMinimumHeight(90)
        layout.addWidget(self.preview)

        actions = QFrame()
        actions.setObjectName("DialogActionBar")
        row = QHBoxLayout(actions)
        row.setContentsMargins(8, 6, 8, 6)
        row.setSpacing(8)
        refresh = default_button("刷新")
        refresh.clicked.connect(self.refresh)
        enter = primary_button("进入任务")
        enter.clicked.connect(self._open_selected)
        row.addWidget(refresh)
        row.addWidget(enter)
        if queue == "ai":
            run_btn = default_button("运行选中任务")
            run_btn.clicked.connect(self._run_selected)
            cancel_btn = danger_button("取消运行中")
            cancel_btn.clicked.connect(self._cancel_running)
            row.addWidget(run_btn)
            row.addWidget(cancel_btn)
        row.addStretch(1)
        layout.addWidget(actions)

    def refresh(self) -> None:
        # Capture the selection before clearing so the periodic refresh
        # (every 2s) does not drop the user’s current selection.
        current = self.list.currentItem()
        selected_job_id = current.data(256) if current is not None else None
        self.list.clear()
        keep = _queue_filter(self.queue)
        for job in self.ai.list_jobs(limit=100):
            if not keep(job):
                continue
            item = QListWidgetItem(
                _job_label(job, with_domain=self._with_domain)
            )
            item.setData(256, job.id)
            self.list.addItem(item)
            if job.id == selected_job_id:
                self.list.setCurrentItem(item)
        if not self.list.count():
            empty = QListWidgetItem(f"暂无{_QUEUE_LABELS[self.queue]}任务")
            empty.setFlags(empty.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self.list.addItem(empty)

    def _selected_job_id(self) -> str | None:
        item = self.list.currentItem()
        return str(item.data(256)) if item is not None else None

    def _show_selected(self, current, _previous) -> None:
        job_id = current.data(256) if current is not None else None
        job = self.ai.get_job(str(job_id)) if job_id else None
        self.preview.setPlainText(job.response_text if job else "")

    def _open_selected(self) -> None:
        job_id = self._selected_job_id()
        if job_id:
            self.open_requested.emit(self.queue, job_id)

    def _run_selected(self) -> None:
        job_id = self._selected_job_id()
        if job_id:
            self.coordinator.enqueue(job_id)

    def _cancel_running(self) -> None:
        job_id = self._selected_job_id()
        if job_id:
            self.coordinator.cancel(job_id)
            self.refresh()


class TaskQueuePage(QWidget):
    """Embedded task queue page with three queue sub-interfaces.

    Question intake, note intake and AI jobs each get their own tab; the
    page lives in the main window stack instead of a modal dialog.
    """

    job_open_requested = Signal(str, str)  # (queue, job_id)

    def __init__(
        self, ai: AIService, coordinator: AIJobCoordinator | None = None, parent=None
    ) -> None:
        super().__init__(parent)
        self.setObjectName("TaskQueuePage")
        self.ai = ai
        self.coordinator = coordinator or AIJobCoordinator(ai, self)
        self.setWindowTitle("任务队列")
        self.resize(760, 520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(12)
        layout.addWidget(
            PageHeader("任务队列", "按题目、笔记、AI 三个队列查看后台任务，进入任务查看详情。")
        )
        self.summary = QLabel("")
        self.summary.setObjectName("MutedLabel")
        layout.addWidget(self.summary)

        self.tabs = QTabWidget()
        self.question_pane = _QueuePane(self.ai, self.coordinator, "question", self)
        self.note_pane = _QueuePane(self.ai, self.coordinator, "note", self)
        self.ai_pane = _QueuePane(self.ai, self.coordinator, "ai", self)
        self.question_pane.open_requested.connect(self.job_open_requested)
        self.note_pane.open_requested.connect(self.job_open_requested)
        self.ai_pane.open_requested.connect(self.job_open_requested)
        self.tabs.addTab(self.question_pane, "题目队列")
        self.tabs.addTab(self.note_pane, "笔记队列")
        self.tabs.addTab(self.ai_pane, "AI 队列")
        layout.addWidget(self.tabs, stretch=1)

        self._timer = QTimer(self)
        self._timer.setInterval(2000)
        self._timer.timeout.connect(self.refresh)

        self.refresh()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.refresh()
        self._timer.start()

    def hideEvent(self, event) -> None:  # noqa: N802
        self._timer.stop()
        super().hideEvent(event)

    def refresh(self) -> None:
        cost = self.ai.today_cost()
        jobs = self.ai.list_jobs(limit=100)
        question = sum(1 for j in jobs if j.domain == "question_intake")
        note = sum(1 for j in jobs if j.domain == "note_intake")
        ai = len(jobs)
        self.summary.setText(
            f"题目队列 {question} 项 · 笔记队列 {note} 项 · AI 队列 {ai} 项 · "
            f"今日估算费用 {cost:.4f} 元"
        )
        self.question_pane.refresh()
        self.note_pane.refresh()
        self.ai_pane.refresh()

    def _run_selected(self) -> None:
        # Kept for compatibility: runs the selected task in the AI pane.
        self.ai_pane._run_selected()

    def _on_done(self, job_id: str) -> None:
        QMessageBox.information(self, "完成", f"任务完成：{job_id}\n请打开「AI 审核」查看结果。")
        self.refresh()

    def _on_fail(self, job_id: str, err: str) -> None:
        QMessageBox.warning(self, "失败", f"{job_id}\n{err}")
        self.refresh()
