"""Task console status-queue behavior tests."""

from __future__ import annotations

from PySide6.QtWidgets import QApplication

from yancuo_win.data.models import AiJob
from yancuo_win.ui.task_center import TaskQueuePage, _job_label


def _job(job_id: str, domain: str, status: str = "running") -> AiJob:
    return AiJob(
        id=job_id,
        job_type="intake",
        status=status,
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


def test_console_has_three_status_queues_and_lists_jobs() -> None:
    _ = QApplication.instance() or QApplication([])
    ai = _FakeAI([
        _job("q1", "question_intake", status="running"),
        _job("q2", "question_intake", status="completed"),
        _job("n1", "note_intake", status="failed"),
        _job("c1", "question_completion", status="pending"),
    ])
    page = TaskQueuePage(ai)
    assert page.tabs.count() == 3
    assert [page.tabs.tabText(i) for i in range(3)] == ["进行中", "已完成", "失败"]
    assert page.active_pane.list.count() == 2  # running + pending
    assert page.done_pane.list.count() == 1
    assert page.failed_pane.list.count() == 1
    page.close()


def test_job_label_is_user_friendly() -> None:
    job = _job("q1", "question_intake", status="completed")
    label = _job_label(job)
    assert "题目识别" in label
    assert "已完成" in label
    assert job.id not in label
    assert "openai_compatible" not in label


def test_refresh_keeps_selected_job() -> None:
    _ = QApplication.instance() or QApplication([])
    ai = _FakeAI([_job("q1", "question_intake"), _job("q2", "question_intake")])
    page = TaskQueuePage(ai)
    page.active_pane.list.setCurrentRow(1)
    assert page.active_pane._selected_job_id() == "q2"
    page.refresh()  # same call the 2s timer performs
    assert page.active_pane.list.currentItem() is not None
    assert page.active_pane._selected_job_id() == "q2"
    page.close()

