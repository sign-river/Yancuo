"""AI 任务后台执行（不阻塞 UI）。"""

from __future__ import annotations

from pathlib import Path
from time import perf_counter
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QThread, Signal

from yancuo_win.application.ai_service import AIService
from yancuo_win.application.problem_chat_service import ProblemChatService

if TYPE_CHECKING:
    from yancuo_win.application.intake_service import ProblemIntakeService


class CallableWorker(QThread):
    """Run one blocking service call without giving it access to Qt widgets."""

    finished_ok = Signal(object)
    failed = Signal(str)

    def __init__(self, task: Callable[[], Any], parent=None) -> None:
        super().__init__(parent)
        self._task = task

    def run(self) -> None:
        try:
            self.finished_ok.emit(self._task())
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


class AIJobWorker(QThread):
    finished_ok = Signal(str)
    failed = Signal(str, str)
    progress = Signal(object)

    def __init__(self, ai: AIService, job_id: str, parent=None) -> None:
        super().__init__(parent)
        self.ai = ai
        self.job_id = job_id
        self._cancel = False
        self.service_finished_at: float | None = None

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        try:
            self.progress.emit({"stage": "started", "label": "任务已开始"})
            self.ai.run_job(
                self.job_id,
                should_cancel=lambda: self._cancel,
                on_progress=self.progress.emit,
            )
            self.service_finished_at = perf_counter()
            self.finished_ok.emit(self.job_id)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(self.job_id, str(exc))


class ReviewApplicationWorker(QThread):
    """Apply human-confirmed review decisions without blocking the dialog."""

    finished_ok = Signal(object)
    failed = Signal(str)

    def __init__(self, ai: AIService, decisions: dict[str, Any], parent=None) -> None:
        super().__init__(parent)
        self.ai = ai
        self.decisions = dict(decisions)

    def run(self) -> None:
        try:
            self.finished_ok.emit(self.ai.apply_review_decisions(self.decisions))
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


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


class UserAnswerRecognitionWorker(QThread):
    """Extract a handwritten user answer without creating an intake candidate."""

    finished_ok = Signal(str)
    failed = Signal(str)

    def __init__(
        self,
        intake: ProblemIntakeService,
        image_paths: list[str],
        keywords: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.intake = intake
        self.image_paths = image_paths
        self.keywords = keywords

    def run(self) -> None:
        try:
            answer = self.intake.recognize_user_answer_images(
                [Path(value) for value in self.image_paths], keywords=self.keywords
            )
            self.finished_ok.emit(answer)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


class ProblemChatWorker(QThread):
    """Run a problem discussion request outside Qt's event loop."""

    finished_ok = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        service: ProblemChatService,
        conversation_id: str,
        content: str,
        references=(),
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.service = service
        self.conversation_id = conversation_id
        self.content = content
        self.references = references

    def run(self) -> None:
        try:
            self.finished_ok.emit(self.service.send_message(self.conversation_id, self.content, self.references))
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))
