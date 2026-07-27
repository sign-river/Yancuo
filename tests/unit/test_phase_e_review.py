"""阶段 E：复习间隔与去重提示。"""

from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo
from pathlib import Path

import pytest

from yancuo_win.application.bootstrap import bootstrap_runtime
from yancuo_win.application.note_service import NoteService
from yancuo_win.application.services import AppServices
from yancuo_win.config.settings import default_toml_path
from yancuo_win.domain.review_rules import (
    compute_next_review_at,
    interval_days_for_grade,
    is_due,
)
from yancuo_win.domain.similarity import text_similarity


@pytest.fixture()
def services(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AppServices:
    monkeypatch.setenv("YANCUO_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("YANCUO_CONFIG_FILE", str(default_toml_path()))
    return AppServices(bootstrap_runtime())


def test_interval_rules_deterministic() -> None:
    assert interval_days_for_grade(1) == 1
    assert interval_days_for_grade(5) == 14
    fixed = datetime(2026, 7, 21, 15, 30, tzinfo=timezone.utc)
    nxt = compute_next_review_at(4, from_dt=fixed)
    # 15:30 UTC is already July 21 in Asia/Shanghai; the next local midnight
    # after seven calendar days is 2026-07-27 16:00 UTC.
    assert nxt == datetime(2026, 7, 27, 16, tzinfo=timezone.utc)


def test_record_review_sets_next_date(services: AppServices) -> None:
    p = services.create_problem(title="复习题", status="active")
    result = services.record_review(p.id, 3)
    got = services.get_problem(p.id)
    assert got is not None
    assert got.review_count == 1
    assert got.mastery == 3
    assert got.next_review_at is not None
    assert result["next_review_at"].startswith(got.next_review_at.date().isoformat()[:10])
    # 刚复习完（间隔 4 天）不应出现在今日到期（除非 next 就是今天）
    due = services.list_due_reviews()
    assert all(x.id != p.id for x in due)

    # 把下次日期改到今天 → 应出现
    services.schedule_initial_review(p.id)
    due2 = services.list_due_reviews()
    assert any(x.id == p.id for x in due2)


def test_paused_review_is_excluded_but_new_enabled_item_is_due(services: AppServices) -> None:
    problem = services.create_problem(title="暂停题", status="active")
    assert any(item.id == problem.id for item in services.list_due_reviews())
    services.set_review_enabled(problem.id, False)
    assert all(item.id != problem.id for item in services.list_due_reviews())
    services.set_review_enabled(problem.id, True)
    assert any(item.id == problem.id for item in services.list_due_reviews())


def test_due_date_uses_shanghai_calendar_boundary() -> None:
    local = ZoneInfo("Asia/Shanghai")
    # 16:00 UTC is midnight on July 22 in Shanghai, so it is not due on July 21.
    due_at = datetime(2026, 7, 21, 16, tzinfo=timezone.utc)
    assert not is_due(due_at, today=date(2026, 7, 21), local_timezone=local)
    assert is_due(due_at, today=date(2026, 7, 22), local_timezone=local)


def test_study_session_records_each_grade_without_reusing_change_review(services: AppServices) -> None:
    problem = services.create_problem(title="学习记录题", status="active")
    session, queue = services.start_study_session()
    assert [item.id for item in queue] == [problem.id]
    services.record_review(
        problem.id,
        4,
        study_session_id=session.id,
        answer_viewed_at=datetime.now(timezone.utc),
    )
    records = services.study_session_records(session.id)
    assert len(records) == 1
    assert records[0].problem_id == problem.id
    assert records[0].grade == 4
    summary = services.finish_study_session(session.id)
    assert summary["status"] == "completed"
    assert summary["completed_count"] == 1


def test_review_queue_respects_type_and_quantity_without_rescheduling(services: AppServices) -> None:
    calculation = services.create_problem(title="计算", status="active")
    choice = services.create_problem(title="选择", status="active")
    services.update_problem(calculation.id, {"problem_type": "计算题"})
    services.update_problem(choice.id, {"problem_type": "选择题"})

    queue = services.prepare_study_queue(
        scope="active",
        problem_types={"计算题"},
        order="scheduled",
        limit=1,
    )

    assert [problem.id for problem in queue] == [calculation.id]
    assert services.get_problem(calculation.id).review_count == 0
    assert services.get_problem(choice.id).review_count == 0


def test_daily_review_plan_has_stable_name_and_deduplicates_items(services: AppServices) -> None:
    problem = services.create_problem(title="当日计划", status="active")
    first = services.add_to_daily_review_plan("problem", problem.id, "2026-07-26")
    second = services.add_to_daily_review_plan("problem", problem.id, "2026-07-26")

    plan = services.get_review_plan(first.id)
    assert second.id == first.id
    assert plan is not None
    assert plan.name == "2026年7月26日 复习计划"
    assert [item.source_id for item in plan.items] == [problem.id]


def test_explicit_review_plan_uses_the_draft_order_selected_by_the_user(
    services: AppServices,
) -> None:
    first = services.create_problem(title="第一题", status="active")
    second = services.create_problem(title="第二题", status="active")
    third = services.create_problem(title="第三题", status="active")
    services.add_to_review_waiting_queue(
        "problem", [first.id, second.id, third.id]
    )

    plan = services.create_review_plan_from_waiting_queue(
        "problem", "排序计划", [third.id, first.id, second.id]
    )

    saved = services.get_review_plan(plan.id)
    assert saved is not None
    assert [item.source_id for item in saved.items] == [
        third.id,
        first.id,
        second.id,
    ]
    assert services.list_review_waiting_ids("problem") == []


def test_note_review_completion_is_persisted_without_problem_scoring(
    services: AppServices,
) -> None:
    notes = NoteService(services.runtime)
    note = notes.create_note(title="泰勒展开", status="active")
    services.add_to_review_waiting_queue("note", [note.id])
    plan = services.create_review_plan_from_waiting_queue("note", "公式笔记")

    record = services.record_note_review(note.id, review_plan_id=plan.id)

    assert record.note_id == note.id
    assert record.review_plan_id == plan.id
    assert [item.id for item in services.note_study_records(note.id)] == [record.id]


def test_import_duplicate_tip_no_second_copy(
    services: AppServices, tmp_path: Path
) -> None:
    img = tmp_path / "dup.jpg"
    img.write_bytes(b"\xff\xd8\xff" + b"dup-content-e")
    r1 = services.import_images([img])
    assert len(r1["created"]) == 1
    r2 = services.import_images([img])
    assert len(r2["created"]) == 0
    assert len(r2["skipped"]) == 1
    assert r2["skipped_existing"][0]["existing_problem_id"] == r1["created"][0]
    assert "跳过" in r2["duplicate_tip"]
    # 不自动删除旧题
    assert services.get_problem(r1["created"][0]) is not None


def test_text_similar_and_hash_groups_no_auto_delete(services: AppServices) -> None:
    a = services.create_problem(
        title="A", question_markdown="计算积分 ∫x dx 从 0 到 1", status="active"
    )
    b = services.create_problem(
        title="B", question_markdown="计算积分 ∫x dx 从0到1", status="active"
    )
    services.create_problem(title="C", question_markdown="完全不同的矩阵题目", status="active")
    similar = services.find_text_similar(a.id, threshold=0.8)
    assert any(x["problem_id"] == b.id for x in similar)
    # 无哈希重复组（无图）
    assert services.find_hash_duplicates() == []
    assert text_similarity("abc", "abc") == 1.0


def test_batch_update_priority(services: AppServices) -> None:
    ids = [
        services.create_problem(title="1", status="active").id,
        services.create_problem(title="2", status="active").id,
    ]
    n = services.batch_update_problems(ids, priority=5)
    assert n == 2
    assert services.get_problem(ids[0]).priority == 5
