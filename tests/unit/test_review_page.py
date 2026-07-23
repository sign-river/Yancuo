"""Review controls enforce the answer-before-grading workflow."""

from __future__ import annotations

from PySide6.QtWidgets import QApplication, QWidget

import yancuo_win.ui.review_page as review_page_module
from yancuo_win.data.models import Problem


class _ReaderStub(QWidget):
    def set_problem(self, *_args, **_kwargs) -> None:
        pass

    def set_message(self, *_args, **_kwargs) -> None:
        pass


class _ServicesStub:
    def __init__(self) -> None:
        self.problem = Problem(
            id="problem_review_ui",
            title="复习交互测试",
            status="active",
            priority=3,
            review_count=0,
            question_markdown="题干",
            correct_answer="答案",
            solution_markdown="解析",
            tags=[],
        )
        self.recorded: list[tuple[str, int]] = []

    def list_due_reviews(self) -> list[Problem]:
        return [self.problem] if not self.recorded else []

    def record_review(self, problem_id: str, grade: int) -> dict[str, str]:
        self.recorded.append((problem_id, grade))
        return {
            "label": "基本正确",
            "next_review_at": "2026-07-24T00:00:00+00:00",
        }


def test_answer_control_lives_in_grade_card_and_unlocks_grading(
    monkeypatch,
) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(review_page_module, "MathContentView", _ReaderStub)
    services = _ServicesStub()
    page = review_page_module.ReviewPage(services)

    assert page.grade_card.isAncestorOf(page.answer_button)
    assert "查看答案后才可评分" in page.grade_hint.text()
    assert not any(button.isEnabled() for button in page.grade_buttons)

    page.answer_button.click()
    app.processEvents()

    assert all(button.isEnabled() for button in page.grade_buttons)
    assert "答案与解析已显示" in page.grade_hint.text()

    page.grade_buttons[3].click()
    app.processEvents()
    assert services.recorded == [("problem_review_ui", 4)]
    assert not any(button.isEnabled() for button in page.grade_buttons)
    page.close()
