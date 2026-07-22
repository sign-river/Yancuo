"""阶段 E：复习间隔与去重提示。"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from yancuo_win.application.bootstrap import bootstrap_runtime
from yancuo_win.application.services import AppServices
from yancuo_win.config.settings import default_toml_path
from yancuo_win.domain.review_rules import (
    compute_next_review_at,
    interval_days_for_grade,
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
    assert nxt == datetime(2026, 7, 28, tzinfo=timezone.utc)


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
