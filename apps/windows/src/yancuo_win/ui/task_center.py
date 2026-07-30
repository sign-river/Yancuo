"""任务中心对话框。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QVBoxLayout,
)

from yancuo_win.application.ai_service import AIService
from yancuo_win.tasks.worker import AIJobWorker
from yancuo_win.ui.widgets import (
    PageHeader,
    SoftItemDelegate,
    danger_button,
    default_button,
    primary_button,
)


class TaskCenterDialog(QDialog):
    def __init__(self, ai: AIService, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("TaskCenterDialog")
        self.ai = ai
        self._worker: AIJobWorker | None = None
        self.setWindowTitle("AI 任务中心")
        self.resize(640, 420)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(12)
        layout.addWidget(PageHeader("AI 任务中心", "查看后台识别任务、进度和当日估算费用。"))
        self.summary = QLabel("")
        self.summary.setObjectName("MutedLabel")
        summary_surface = QFrame()
        summary_surface.setObjectName("DialogSummarySurface")
        summary_layout = QVBoxLayout(summary_surface)
        summary_layout.setContentsMargins(16, 12, 16, 12)
        summary_layout.addWidget(self.summary)
        layout.addWidget(summary_surface)
        self.list = QListWidget()
        self.list.setObjectName("DialogItemList")
        self.list.setAccessibleName("AI 后台任务")
        self.list.setUniformItemSizes(True)
        self.list.setMouseTracking(True)
        self.list.setItemDelegate(
            SoftItemDelegate(self.list, minimum_height=40)
        )
        layout.addWidget(self.list, stretch=1)

        actions = QFrame()
        actions.setObjectName("DialogActionBar")
        row = QHBoxLayout(actions)
        row.setContentsMargins(12, 8, 12, 8)
        row.setSpacing(8)
        refresh = default_button("刷新")
        refresh.clicked.connect(self.refresh)
        run_btn = primary_button("运行选中任务")
        run_btn.clicked.connect(self._run_selected)
        cancel_btn = danger_button("取消运行中")
        cancel_btn.clicked.connect(self._cancel_running)
        row.addWidget(refresh)
        row.addWidget(run_btn)
        row.addWidget(cancel_btn)
        row.addStretch(1)
        layout.addWidget(actions)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        close_button = buttons.button(QDialogButtonBox.StandardButton.Close)
        close_button.setText("关闭")
        close_button.clicked.connect(self.accept)
        layout.addWidget(buttons)
        self.refresh()

    def refresh(self) -> None:
        self.list.clear()
        cost = self.ai.today_cost()
        self.summary.setText(
            f"今日估算费用：{cost:.4f} / 上限 {self.ai.runtime.settings.ai.max_daily_cost_yuan}"
        )
        for job in self.ai.list_jobs():
            text = (
                f"[{job.status}] {job.job_type} · {job.provider} · "
                f"{job.done_items}/{job.total_items} · fail={job.failed_items} · "
                f"cost≈{job.estimated_cost:.4f} · {job.id[:18]}"
            )
            item = QListWidgetItem(text)
            item.setData(256, job.id)  # Qt.UserRole
            self.list.addItem(item)
        if not self.list.count():
            empty = QListWidgetItem("暂无后台任务")
            empty.setFlags(empty.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self.list.addItem(empty)

    def _run_selected(self) -> None:
        items = self.list.selectedItems()
        if not items:
            QMessageBox.information(self, "提示", "请选择任务")
            return
        if self._worker and self._worker.isRunning():
            QMessageBox.information(self, "提示", "已有任务在运行")
            return
        job_id = items[0].data(256)
        self._worker = AIJobWorker(self.ai, job_id, self)
        self._worker.finished_ok.connect(self._on_done)
        self._worker.failed.connect(self._on_fail)
        self._worker.start()
        self.summary.setText(self.summary.text() + "  · 后台运行中…")

    def _cancel_running(self) -> None:
        if self._worker and self._worker.isRunning():
            self._worker.cancel()

    def _on_done(self, job_id: str) -> None:
        QMessageBox.information(self, "完成", f"任务完成：{job_id}\n请打开「AI 审核」查看结果。")
        self.refresh()

    def _on_fail(self, job_id: str, err: str) -> None:
        QMessageBox.warning(self, "失败", f"{job_id}\n{err}")
        self.refresh()
