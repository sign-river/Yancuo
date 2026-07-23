"""AI 任务后台执行（不阻塞 UI）。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QThread, Signal

from yancuo_win.application.ai_service import AIService

if TYPE_CHECKING:
    from yancuo_win.application.intake_service import ProblemIntakeService


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


class RegionRecognitionWorker(QThread):
    finished_ok = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        intake: ProblemIntakeService,
        candidate_id: str,
        fields: dict[str, Any],
        tag_names: list[str],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.intake = intake
        self.candidate_id = candidate_id
        self.fields = fields
        self.tag_names = tag_names

    def run(self) -> None:
        try:
            proposal = self.intake.rerecognize_ai_candidate_region(
                self.candidate_id,
                self.fields,
                tag_names=self.tag_names,
            )
            self.finished_ok.emit(proposal)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))
