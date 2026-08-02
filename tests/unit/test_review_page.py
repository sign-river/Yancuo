"""Review controls enforce the answer-before-grading workflow."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from PySide6.QtWidgets import QApplication, QLabel, QWidget

import yancuo_win.ui.review_page as review_page_module
import yancuo_win.ui.today_review as today_review_module
from yancuo_win.data.models import Problem
from yancuo_win.ui.widgets import ReadingCanvas


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

    def prepare_study_queue(self, **_kwargs) -> list[Problem]:
        return self.list_due_reviews()

    def list_review_plans(self):
        class _Item:
            source_id = "problem_review_ui"

        class _Plan:
            id = "plan_review_ui"
            name = "测试计划"
            content_type = "problem"
            kind = "explicit"
            items = [_Item()]

        return [_Plan()]

    def get_review_plan(self, plan_id: str):
        return self.list_review_plans()[0] if plan_id == "plan_review_ui" else None

    def list_problems_by_ids(self, _ids):
        return self.list_due_reviews()

    def list_problems(self):
        return self.list_due_reviews()

    def list_review_waiting_ids(self, _content_type):
        return []

    def start_study_session(self, *, selection, problem_ids):
        class _Session:
            id = "study_review_ui"

        return _Session(), [self.problem] if problem_ids else []

    def review_plan_study_sessions(self, _plan_id):
        return []

    def review_plan_note_records(self, _plan_id):
        return []

    def record_review(self, problem_id: str, grade: int, **_kwargs) -> dict[str, str]:
        self.recorded.append((problem_id, grade))
        return {
            "label": "基本正确",
            "next_review_at": "2026-07-24T00:00:00+00:00",
        }


class _NoteServicesStub(_ServicesStub):
    def __init__(self, note) -> None:
        super().__init__()
        self.note = note
        self.note_reviews: list[str] = []

    def list_review_plans(self):
        return [
            SimpleNamespace(
                id="plan_note_ui",
                name="笔记计划",
                content_type="note",
                kind="explicit",
                items=[SimpleNamespace(source_id=self.note.id)],
            )
        ]

    def get_review_plan(self, plan_id: str):
        return self.list_review_plans()[0] if plan_id == "plan_note_ui" else None

    def record_note_review(self, note_id: str, **_kwargs):
        self.note_reviews.append(note_id)
        return SimpleNamespace(note_id=note_id)


class _NotesStub:
    def __init__(self, note) -> None:
        self.note = note

    def get_note(self, note_id: str):
        return self.note if note_id == self.note.id else None


def test_review_page_does_not_show_progress_during_construction(monkeypatch) -> None:
    QApplication.instance() or QApplication([])
    monkeypatch.setattr(review_page_module, "MathContentView", _ReaderStub)
    visibility_requests: list[tuple[QLabel, bool]] = []
    original_set_visible = QLabel.setVisible

    def track_visibility(label: QLabel, visible: bool) -> None:
        visibility_requests.append((label, visible))
        original_set_visible(label, visible)

    monkeypatch.setattr(QLabel, "setVisible", track_visibility)
    page = review_page_module.ReviewPage(_ServicesStub())

    assert (page.progress_label, True) not in visibility_requests
    page.close()


def test_answer_control_lives_in_grade_card_and_unlocks_grading(
    monkeypatch,
) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(review_page_module, "MathContentView", _ReaderStub)
    services = _ServicesStub()
    page = review_page_module.ReviewPage(services)

    page.plan_combo.setCurrentIndex(0)
    page.start_session()
    assert page.grade_card.isAncestorOf(page.answer_button)
    assert page.grade_card.objectName() == "ReviewGradeSurface"
    assert isinstance(page.session_canvas, ReadingCanvas)
    assert page.session_canvas.content is page.reader
    assert page.session_canvas.maximum_content_width == 960
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


def test_review_workbench_collects_session_options(monkeypatch) -> None:
    QApplication.instance() or QApplication([])
    monkeypatch.setattr(review_page_module, "MathContentView", _ReaderStub)
    page = review_page_module.ReviewPage(_ServicesStub())

    assert page.stack.currentWidget() is page.home_page
    assert page.scope_combo.currentData() == "due"
    assert page.order_combo.currentData() == "scheduled"
    assert page.limit_spin.value() == 20
    assert page.type_checks

    page.close()


def test_selecting_a_plan_only_updates_preview_until_start(monkeypatch) -> None:
    QApplication.instance() or QApplication([])
    monkeypatch.setattr(review_page_module, "MathContentView", _ReaderStub)
    services = _ServicesStub()
    page = review_page_module.ReviewPage(services)

    assert not page.start_selected_button.isEnabled()
    page.plan_combo.setCurrentIndex(0)

    assert page.start_selected_button.isEnabled()
    assert page.start_selected_button.text() == "开始题目复习"
    assert page.plan_preview.count() == 1
    assert page.stack.currentWidget() is page.home_page
    assert page._study_session_id is None
    page.close()


def test_selected_plan_exposes_read_only_full_history(monkeypatch) -> None:
    QApplication.instance() or QApplication([])
    monkeypatch.setattr(review_page_module, "MathContentView", _ReaderStub)
    services = _ServicesStub()
    session = SimpleNamespace(
        id="study-history",
        started_at=datetime(2026, 8, 1, 10, 30, tzinfo=timezone.utc),
        status="completed",
        problem_count=1,
    )
    services.review_plan_study_sessions = lambda _plan_id: [session]
    services.study_session_records = lambda _session_id: [
        SimpleNamespace(grade=4)
    ]
    opened: list[tuple[str, list[object]]] = []

    class _HistoryDialogStub:
        def __init__(self, title, entries, _parent) -> None:
            opened.append((title, entries))

        def exec(self) -> None:
            return None

    monkeypatch.setattr(review_page_module, "ReviewHistoryDialog", _HistoryDialogStub)
    page = review_page_module.ReviewPage(services)
    page.plan_combo.setCurrentIndex(0)

    assert page.plan_preview.item(0).text() == "复习交互测试"
    assert "已完成" in page.plan_history.item(0).text()
    assert page.open_history_button.isEnabled()
    page._open_plan_history()

    assert opened[0][0] == "测试计划"
    assert opened[0][1][0].title == "已完成 · 1 题"
    assert "4分 1题" in opened[0][1][0].details
    assert page._study_session_id is None
    page.close()


def test_legacy_today_review_grade_uses_non_blocking_feedback(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(today_review_module, "MathContentView", _ReaderStub)
    services = _ServicesStub()
    dialog = today_review_module.TodayReviewDialog(services)

    dialog._grade(3)
    app.processEvents()

    assert services.recorded == [("problem_review_ui", 3)]
    assert not dialog.toast.isHidden()
    assert "下次复习 2026-07-24" in dialog.toast.label.text()
    dialog.close()


def test_note_session_completes_and_skips_trashed_notes(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(review_page_module, "MathContentView", _ReaderStub)
    note = SimpleNamespace(
        id="note_review_ui",
        title="极限笔记",
        status="active",
        blocks=[],
        tags=[],
        summary="",
    )
    services = _NoteServicesStub(note)
    notes = _NotesStub(note)
    page = review_page_module.ReviewPage(services, notes)

    page.plan_combo.setCurrentIndex(0)
    page.start_session()
    page.note_complete_button.click()
    app.processEvents()

    assert services.note_reviews == [note.id]
    assert page.note_complete_button.isHidden()
    assert not page.session_finish_button.isHidden()

    note.status = "trashed"
    page.start_session()
    assert page.stack.currentWidget() is page.home_page
    page.close()


def test_session_shortcut_hints_match_the_active_content_type(monkeypatch) -> None:
    QApplication.instance() or QApplication([])
    monkeypatch.setattr(review_page_module, "MathContentView", _ReaderStub)
    page = review_page_module.ReviewPage(_ServicesStub())
    page.plan_combo.setCurrentIndex(0)
    page.start_session()

    assert "→ 暂时跳过" in page.keyboard_hint.text()
    assert page.keyboard_hint.accessibleDescription() == page.keyboard_hint.text()
    page.close()


def test_note_session_uses_the_same_accessible_shortcut_legend(monkeypatch) -> None:
    QApplication.instance() or QApplication([])
    monkeypatch.setattr(review_page_module, "MathContentView", _ReaderStub)
    note = SimpleNamespace(
        id="note_shortcut_ui",
        title="快捷键笔记",
        status="active",
        blocks=[],
        tags=[],
        summary="",
    )
    page = review_page_module.ReviewPage(
        _NoteServicesStub(note), _NotesStub(note)
    )
    page.plan_combo.setCurrentIndex(0)
    page.start_session()

    assert page.keyboard_hint.text() == "快捷键：Enter 标记已阅读并继续。"
    assert page.keyboard_hint.accessibleDescription() == page.keyboard_hint.text()
    assert page.grade_card.isHidden()
    page.close()
