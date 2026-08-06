"""Task console status-queue behavior tests."""

from __future__ import annotations

from PySide6.QtWidgets import QApplication, QMessageBox

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

    def delete_jobs(self, job_ids) -> int:
        ids = {str(job_id) for job_id in job_ids}
        before = len(self._jobs)
        self._jobs = [job for job in self._jobs if job.id not in ids]
        return before - len(self._jobs)

    def today_cost(self) -> float:
        return 0.0


def test_console_has_four_status_queues_and_lists_jobs() -> None:
    _ = QApplication.instance() or QApplication([])
    ai = _FakeAI([
        _job("q1", "question_intake", status="running"),
        _job("q2", "question_intake", status="completed"),
        _job("q3", "question_intake", status="completed"),
        _job("n1", "note_intake", status="failed"),
        _job("c1", "question_completion", status="pending"),
    ])
    def pending(job):
        return job.id == "q2"  # q2 已完成但仍有待审核内容

    page = TaskQueuePage(ai, pending_review=pending)
    assert page.tabs.count() == 4
    assert [page.tabs.tabText(i) for i in range(4)] == ["进行中", "待审核", "已完成", "失败"]
    assert page.active_pane.list.count() == 2  # running + pending
    assert page.review_pane.list.count() == 1  # q2 待审核
    assert "待审核" in page.review_pane.list.item(0).text()
    assert page.done_pane.list.count() == 1  # q3 已完成
    assert page.failed_pane.list.count() == 1  # n1
    page.close()


def test_job_label_is_user_friendly() -> None:
    job = _job("q1", "question_intake", status="completed")
    label = _job_label(job)
    assert "题目识别" in label
    assert "已完成" in label
    assert job.id not in label
    assert "openai_compatible" not in label


def test_refresh_skips_rebuild_when_unchanged() -> None:
    _ = QApplication.instance() or QApplication([])
    ai = _FakeAI([_job("q1", "question_intake"), _job("q2", "question_intake")])
    page = TaskQueuePage(ai)
    pane = page.active_pane
    item_before = pane.list.item(0)
    pane.list.setCurrentRow(1)
    page.refresh()  # same call the 2s timer performs
    assert pane.list.item(0) is item_before  # 未变化时不重建，滚动位置自然保持
    assert pane._selected_job_id() == "q2"
    page.close()


def test_refresh_keeps_selected_job_after_rebuild() -> None:
    _ = QApplication.instance() or QApplication([])
    ai = _FakeAI([_job("q1", "question_intake"), _job("q2", "question_intake")])
    page = TaskQueuePage(ai)
    page.active_pane.list.setCurrentRow(1)
    ai._jobs[0].done_items = 2  # 让摘要变化，触发重建
    page.refresh()
    assert page.active_pane.list.currentItem() is not None
    assert page.active_pane._selected_job_id() == "q2"
    page.close()


def test_clear_records_rebuilds_queue_when_all_jobs_removed(monkeypatch) -> None:
    _ = QApplication.instance() or QApplication([])
    ai = _FakeAI([
        _job("d1", "question_intake", status="completed"),
        _job("d2", "question_intake", status="completed"),
    ])
    page = TaskQueuePage(ai)
    pane = page.done_pane
    assert pane.list.count() == 2
    monkeypatch.setattr(
        "PySide6.QtWidgets.QMessageBox.question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )
    pane._clear_records()
    assert ai._jobs == []
    # 清空后必须重建列表，显示“暂无已完成任务”占位，而不是残留旧任务
    assert pane.list.count() == 1
    assert pane.list.item(0).data(256) is None
    assert "暂无" in pane.list.item(0).text()
    page.close()
