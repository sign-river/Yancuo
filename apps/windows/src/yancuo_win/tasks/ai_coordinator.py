"""Application-owned, persistent single-concurrency AI task coordinator."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable

from PySide6.QtCore import QObject, QThread, Signal

from yancuo_win.application.ai_service import AIService
from yancuo_win.tasks.worker import AIJobWorker


class AIJobCoordinator(QObject):
    job_started = Signal(str)
    job_progress = Signal(str, object)
    job_finished = Signal(str)
    job_failed = Signal(str, str)
    queue_changed = Signal()

    def __init__(self, ai: AIService, parent=None) -> None:
        super().__init__(parent)
        self.ai = ai
        self._queue: deque[str] = deque()
        self._queued: set[str] = set()
        self._worker: AIJobWorker | None = None
        self._active_job_id: str | None = None
        self._handlers: dict[
            str, Callable[[str, Callable[[object], None], Callable[[], bool]], object]
        ] = {}

    def register_handler(
        self,
        domain: str,
        handler: Callable[[str, Callable[[object], None], Callable[[], bool]], object],
    ) -> None:
        self._handlers[domain] = handler

    @property
    def active_job_id(self) -> str | None:
        return self._active_job_id

    def resume_pending(self) -> None:
        for job_id in self.ai.recover_interrupted_jobs():
            self.enqueue(job_id)

    def enqueue(self, job_id: str) -> None:
        if job_id == self._active_job_id or job_id in self._queued:
            return
        job = self.ai.get_job(job_id)
        if job is None or job.status == "cancelled":
            return
        if job.status == "completed" and not job.failed_items:
            return
        self._queue.append(job_id)
        self._queued.add(job_id)
        self.queue_changed.emit()
        self._start_next()

    def cancel(self, job_id: str) -> None:
        if job_id == self._active_job_id and self._worker is not None:
            self._worker.cancel()
            return
        if job_id not in self._queued:
            return
        self._queue = deque(value for value in self._queue if value != job_id)
        self._queued.discard(job_id)
        self.ai.cancel_job(job_id)
        self.queue_changed.emit()

    def shutdown(self) -> None:
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            if not self._worker.wait(300):
                worker = self._worker
                worker.setParent(None)
                worker.finished.connect(worker.deleteLater)
        self._worker = None

    def _start_next(self) -> None:
        if self._worker is not None or not self._queue:
            return
        job_id = self._queue.popleft()
        self._queued.discard(job_id)
        self._active_job_id = job_id
        job = self.ai.get_job(job_id)
        handler = self._handlers.get(job.domain if job else "")
        worker = (
            DomainJobWorker(self.ai, job_id, handler, self)
            if handler is not None
            else AIJobWorker(self.ai, job_id, self)
        )
        worker.progress.connect(lambda event, value=job_id: self.job_progress.emit(value, event))
        worker.finished_ok.connect(self._on_finished)
        worker.failed.connect(self._on_failed)
        worker.finished.connect(self._worker_stopped)
        self._worker = worker
        self.job_started.emit(job_id)
        self.queue_changed.emit()
        worker.start()

    def _on_finished(self, job_id: str) -> None:
        self.job_finished.emit(job_id)

    def _on_failed(self, job_id: str, message: str) -> None:
        self.ai.mark_job_failed(job_id, message)
        self.job_failed.emit(job_id, message)

    def _worker_stopped(self) -> None:
        worker = self._worker
        self._worker = None
        self._active_job_id = None
        if worker is not None:
            worker.deleteLater()
        self.queue_changed.emit()
        self._start_next()


class DomainJobWorker(QThread):
    finished_ok = Signal(str)
    failed = Signal(str, str)
    progress = Signal(object)

    def __init__(self, ai: AIService, job_id: str, handler, parent=None) -> None:
        super().__init__(parent)
        self.ai = ai
        self.job_id = job_id
        self.handler = handler
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        try:
            self.ai.start_background_job(self.job_id)
            result = self.handler(
                self.job_id,
                self.progress.emit,
                lambda: self._cancel,
            )
            if self._cancel:
                self.ai.cancel_job(self.job_id)
                return
            self.ai.complete_background_job(
                self.job_id,
                result=result if isinstance(result, dict) else {},
            )
            self.finished_ok.emit(self.job_id)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(self.job_id, str(exc))
