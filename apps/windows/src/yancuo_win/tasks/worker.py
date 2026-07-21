"""AI 任务后台执行（不阻塞 UI）。"""

from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from yancuo_win.application.ai_service import AIService


class AIJobWorker(QThread):
    finished_ok = Signal(str)
    failed = Signal(str, str)
    progress = Signal(str)

    def __init__(self, ai: AIService, job_id: str, parent=None) -> None:
        super().__init__(parent)
        self.ai = ai
        self.job_id = job_id
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        try:
            self.progress.emit(f"running:{self.job_id}")
            self.ai.run_job(self.job_id, should_cancel=lambda: self._cancel)
            self.finished_ok.emit(self.job_id)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(self.job_id, str(exc))
