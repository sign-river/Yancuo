"""Task console three-queue behavior tests."""

from __future__ import annotations

from PySide6.QtWidgets import QApplication

from yancuo_win.data.models import AiJob
from yancuo_win.ui.task_center import TaskQueuePage, _queue_filter


def _job(job_id: str, domain: str) -> AiJob:
    return AiJob(
        id=job_id,
        job_type="intake",
        status="running",
        provider="faro",
        model="",
        prompt_key="",
        domain=domain,
        done_items=1,
        total_items=2,
    )


class _FakeAI:
    def __init__(self, jobs):
        self._jobs = jobs

    def list_jobs(self, limit: int = 50):
        return self._jobs[:limit]

    def get_job(self, job_id: str):
        return next((j for j in self._jobs if j.id == job_id), None)

    def today_cost(self) -> float:
        return 0.0


def test_queue_filter_partitions_domains() -> None:
    question = _job("q1", "question_intake")
    note = _job("n1", "note_intake")
    completion = _job("c1", "question_completion")
    assert _queue_filter("question")(question)
    assert not _queue_filter("question")(note)
    assert not _queue_filter("question")(completion)
    assert _queue_filter("note")(note)
    assert not _queue_filter("note")(question)
    assert _queue_filter("ai")(question)
    assert _queue_filter("ai")(note)
    assert _queue_filter("ai")(completion)


def test_console_has_three_queues_and_lists_jobs() -> None:
    app = QApplication.instance() or QApplication([])
    ai = _FakeAI([_job("q1", "question_intake"), _job("n1", "note_intake"), _job("c1", "question_completion")])
    page = TaskQueuePage(ai)
    assert page.tabs.count() == 3
    assert page.question_pane.list.count() == 1
    assert page.note_pane.list.count() == 1
    assert page.ai_pane.list.count() == 3
    page.close()
