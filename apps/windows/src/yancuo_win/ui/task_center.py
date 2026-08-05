"""任务队列页：按进行中 / 已完成 / 失败三个状态子队列查看后台任务，进入任务查看详情。"""

from __future__ import annotations

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from yancuo_win.application.ai_service import AIService
from yancuo_win.tasks.ai_coordinator import AIJobCoordinator
from yancuo_win.ui.widgets import (
    IconButton,
    PageHeader,
    SoftItemDelegate,
    danger_button,
    default_button,
    primary_button,
)

_STATUS_LABELS = {
    "pending": "排队中",
    "running": "进行中",
    "completed": "已完成",
    "done": "已完成",
    "failed": "失败",
    "canceled": "已取消",
    "cancelled": "已取消",
}
_DOMAIN_LABELS = {
    "question_intake": "题目识别",
    "note_intake": "笔记识别",
    "question_completion": "题目补全",
    "note_completion": "笔记补全",
    "generic": "后台任务",
}
_JOB_TYPE_LABELS = {
    "intake": "识别录入",
    "intake_structure": "识别录入",
    "structure_recognize": "结构识别",
    "structure": "结构整理",
    "completion": "AI 补全",
    "note_extract": "笔记提取",
    "extract": "内容提取",
    "user_answer": "作答识别",
    "chat": "AI 对话",
}
_PROVIDER_LABELS = {
    "openai_compatible": "Faro API",
    "faro": "Faro API",
    "mock": "本地模拟",
}

_ACTIVE_STATUSES = frozenset({"pending", "running"})
_DONE_STATUSES = frozenset({"completed", "done"})
_FAILED_STATUSES = frozenset({"failed", "canceled", "cancelled"})
_QUEUE_TABS = (
    ("进行中", _ACTIVE_STATUSES, "active"),
    ("已完成", _DONE_STATUSES, "done"),
    ("失败", _FAILED_STATUSES, "failed"),
)


def _status_label(status: str) -> str:
    return _STATUS_LABELS.get(status, status)


def _domain_label(domain: str) -> str:
    return _DOMAIN_LABELS.get(domain, domain)


def _job_type_label(job_type: str) -> str:
    return _JOB_TYPE_LABELS.get(job_type, job_type)


def _provider_label(provider: str) -> str:
    return _PROVIDER_LABELS.get(provider, provider)


def _job_label(job) -> str:
    """用户友好的一行任务摘要，不展示原始英文代码与任务 ID。"""
    cost = float(job.estimated_cost or 0.0)
    parts = [
        _domain_label(job.domain),
        _job_type_label(job.job_type),
        _provider_label(job.provider),
        _status_label(job.status),
        f"{int(job.done_items or 0)}/{int(job.total_items or 0)}",
    ]
    if int(job.failed_items or 0):
        parts.append(f"失败 {int(job.failed_items or 0)}")
    if cost > 0:
        parts.append(f"费用 ¥{cost:.2f}")
    return " · ".join(parts)


def _job_detail_summary(job) -> str:
    """任务详情页与预览区的友好摘要。"""
    cost = float(job.estimated_cost or 0.0)
    lines = [
        f"{_domain_label(job.domain)} · {_job_type_label(job.job_type)} · {_provider_label(job.provider)}",
        f"状态：{_status_label(job.status)} · 进度：{int(job.done_items or 0)}/{int(job.total_items or 0)}"
        + (f" · 失败：{int(job.failed_items or 0)}" if int(job.failed_items or 0) else "")
        + (f" · 费用 ¥{cost:.2f}" if cost > 0 else ""),
    ]
    if job.error_message:
        lines.append(f"错误：{job.error_message}")
    return "\n".join(lines)


class AIJobDetailPage(QWidget):
    """嵌入式 AI 任务详情页，带返回任务列表动作，替代原弹窗。"""

    back_requested = Signal()

    def __init__(
        self,
        ai: AIService,
        coordinator: AIJobCoordinator | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("AIJobDetailPage")
        self.ai = ai
        self.coordinator = coordinator
        self.job_id = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(12)
        header = PageHeader("AI 任务详情", "实时查看 AI 任务的流式回复与执行状态。")
        back = IconButton("chevron-left", "返回任务列表")
        back.clicked.connect(self.back_requested.emit)
        header.add_leading(back)
        layout.addWidget(header)

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
        refresh.clicked.connect(self.refresh)
        rerun = default_button("重新运行")
        rerun.clicked.connect(self._rerun)
        cancel_btn = danger_button("取消任务")
        cancel_btn.clicked.connect(self._cancel)
        row.addWidget(refresh)
        row.addWidget(rerun)
        row.addWidget(cancel_btn)
        row.addStretch(1)
        layout.addWidget(actions)

        self._timer = QTimer(self)
        self._timer.setInterval(800)
        self._timer.timeout.connect(self.refresh)

    def open_job(self, job_id: str) -> None:
        self.job_id = job_id
        self.refresh()
        self._timer.start()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if self.job_id:
            self._timer.start()
            self.refresh()

    def hideEvent(self, event) -> None:  # noqa: N802
        self._timer.stop()
        super().hideEvent(event)

    def refresh(self) -> None:
        if not self.job_id:
            self.meta.setText("请选择任务后查看详情。")
            return
        job = self.ai.get_job(self.job_id)
        if job is None:
            self.meta.setText("任务不存在或已被清理。")
            self._timer.stop()
            self.response.setPlainText("")
            return
        self.meta.setText(_job_detail_summary(job))
        self.response.setPlainText(job.response_text or "")
        if job.status in _DONE_STATUSES | _FAILED_STATUSES:
            self._timer.stop()

    def _cancel(self) -> None:
        if self.coordinator is not None:
            self.coordinator.cancel(self.job_id)
            self.refresh()

    def _rerun(self) -> None:
        if self.coordinator is not None and self.job_id:
            self.coordinator.enqueue(self.job_id)
            self._timer.start()
            self.refresh()


class _QueuePane(QWidget):
    """一个状态子队列：任务列表 + 预览 + 操作按钮。"""

    open_requested = Signal(str)  # job_id

    def __init__(
        self,
        ai: AIService,
        coordinator: AIJobCoordinator,
        statuses: frozenset[str],
        kind: str,
        tab_label: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.ai = ai
        self.coordinator = coordinator
        self.statuses = statuses
        self.kind = kind
        self.tab_label = tab_label

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.list = QListWidget()
        self.list.setObjectName("DialogItemList")
        self.list.setAccessibleName(f"{tab_label}任务")
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
        if kind == "active":
            cancel_btn = danger_button("取消运行中")
            cancel_btn.clicked.connect(self._cancel_running)
            row.addWidget(cancel_btn)
        elif kind == "failed":
            rerun = default_button("重新运行")
            rerun.clicked.connect(self._run_selected)
            row.addWidget(rerun)
        row.addStretch(1)
        layout.addWidget(actions)

    def refresh(self) -> None:
        # 先保存选中项，再清空重建（否则每 2s 定时刷新会丢掉当前选择）
        current = self.list.currentItem()
        selected_job_id = current.data(256) if current is not None else None
        self.list.clear()
        for job in self.ai.list_jobs(limit=100):
            if job.status not in self.statuses:
                continue
            item = QListWidgetItem(_job_label(job))
            item.setData(256, job.id)
            self.list.addItem(item)
            if job.id == selected_job_id:
                self.list.setCurrentItem(item)
        if not self.list.count():
            empty = QListWidgetItem(f"暂无{self.tab_label}任务")
            empty.setFlags(empty.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self.list.addItem(empty)

    def _selected_job_id(self) -> str | None:
        item = self.list.currentItem()
        return str(item.data(256)) if item is not None else None

    def _show_selected(self, current, _previous) -> None:
        job_id = current.data(256) if current is not None else None
        job = self.ai.get_job(str(job_id)) if job_id else None
        if job is None:
            self.preview.setPlainText("")
            return
        self.preview.setPlainText(
            _job_detail_summary(job) + "\n\n" + (job.response_text or "")
        )

    def _open_selected(self) -> None:
        job_id = self._selected_job_id()
        if job_id:
            self.open_requested.emit(job_id)

    def _run_selected(self) -> None:
        job_id = self._selected_job_id()
        if job_id:
            self.coordinator.enqueue(job_id)
            self.refresh()

    def _cancel_running(self) -> None:
        job_id = self._selected_job_id()
        if job_id:
            self.coordinator.cancel(job_id)
            self.refresh()


class TaskQueuePage(QWidget):
    """嵌入式任务队列页，按进行中 / 已完成 / 失败三个状态子队列展示。"""

    job_open_requested = Signal(str)  # job_id

    def __init__(
        self,
        ai: AIService,
        coordinator: AIJobCoordinator | None = None,
        parent: QWidget | None = None,
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
            PageHeader("任务队列", "按进行中、已完成、失败三个队列查看后台任务，进入任务查看详情。")
        )
        self.summary = QLabel("")
        self.summary.setObjectName("MutedLabel")
        layout.addWidget(self.summary)

        self.tabs = QTabWidget()
        self.panes: dict[str, _QueuePane] = {}
        for tab_label, statuses, kind in _QUEUE_TABS:
            pane = _QueuePane(
                self.ai, self.coordinator, statuses, kind, tab_label, self
            )
            pane.open_requested.connect(self.job_open_requested)
            self.panes[kind] = pane
            self.tabs.addTab(pane, tab_label)
        layout.addWidget(self.tabs, stretch=1)

        self._timer = QTimer(self)
        self._timer.setInterval(2000)
        self._timer.timeout.connect(self.refresh)

        self.refresh()

    @property
    def active_pane(self) -> _QueuePane:
        return self.panes["active"]

    @property
    def done_pane(self) -> _QueuePane:
        return self.panes["done"]

    @property
    def failed_pane(self) -> _QueuePane:
        return self.panes["failed"]

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
        active = sum(1 for j in jobs if j.status in _ACTIVE_STATUSES)
        done = sum(1 for j in jobs if j.status in _DONE_STATUSES)
        failed = sum(1 for j in jobs if j.status in _FAILED_STATUSES)
        self.summary.setText(
            f"进行中 {active} 项 · 已完成 {done} 项 · 失败 {failed} 项 · 今日费用 ¥{cost:.2f}"
        )
        for pane in self.panes.values():
            pane.refresh()

    def _run_selected(self) -> None:
        # 兼容入口：重新运行失败队列中选中的任务。
        self.failed_pane._run_selected()


