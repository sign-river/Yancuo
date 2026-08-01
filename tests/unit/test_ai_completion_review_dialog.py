"""UI-116 coverage for the staged AI completion review flow."""

from __future__ import annotations

from types import SimpleNamespace

from PySide6.QtWidgets import QApplication, QPushButton

from yancuo_win.ui.review_dialog import ReviewDialog


class _AIStub:
    def __init__(self) -> None:
        self.item = SimpleNamespace(id="internal-review-id")
        self.applied: dict[str, str] | None = None
        self.undone: list[str] | None = None

    def list_open_review_items(self):
        return [self.item]

    def list_open_review_items_for_job(self, job_id: str):
        assert job_id == "internal-job-id"
        return [self.item]

    def completion_review_overview(self):
        return [
            {
                "job_id": "internal-job-id",
                "label": "建议已生成",
                "status": "completed",
                "completed": 1,
                "total": 1,
                "failed": 0,
                "review_count": 1,
            }
        ]

    def list_jobs(self, *, limit: int):
        assert limit == 1
        return []

    def review_presentation(self, item_id: str):
        assert item_id == self.item.id
        return {
            "title": "极限练习",
            "source": "AI 补全建议",
            "status": "等待确认",
            "diffs": [{"field": "title", "before": "", "after": "极限练习"}],
            "warnings": ["题干：请核对符号"],
        }

    def apply_review_decisions(self, decisions):
        self.applied = decisions
        return {"accepted_problem_ids": ["problem-private-id"], "rejected_item_ids": []}

    def undo_review_accepts(self, problem_ids):
        self.undone = problem_ids
        return len(problem_ids)


def test_review_dialog_stages_decisions_and_hides_internal_identifiers(monkeypatch) -> None:
    QApplication.instance() or QApplication([])
    ai = _AIStub()
    dialog = ReviewDialog(ai, SimpleNamespace())
    dialog._continue_review()
    dialog.list.setCurrentRow(0)

    assert not dialog.review_back_button.isHidden()
    assert dialog.review_back_button.text() == ""
    assert dialog.review_back_button.accessibleName() == "返回准备页"
    assert not any(
        button.text() == "返回准备页"
        for button in dialog.review_page.findChildren(QPushButton)
    )
    assert "internal-review-id" not in dialog.list.item(0).text()
    assert "problem-private-id" not in dialog.meta.text()
    assert not dialog.apply_button.isEnabled()

    dialog._decide("accept")
    assert ai.applied is None
    assert dialog.apply_button.isEnabled()

    monkeypatch.setattr(
        "yancuo_win.ui.review_dialog.ConfirmDialog.ask", lambda *_args: True
    )
    dialog._apply()
    assert dialog._apply_worker is not None
    assert dialog._apply_worker.wait(1000)
    assert ai.applied == {"internal-review-id": "accept"}
    dialog._on_apply_done(
        {"accepted_problem_ids": ["problem-private-id"], "rejected_item_ids": []}
    )
    assert dialog.complete_summary.text().startswith("已采纳 1 项")
    assert dialog.review_back_button.isHidden()

    dialog._undo()
    assert ai.undone == ["problem-private-id"]
    dialog.close()
