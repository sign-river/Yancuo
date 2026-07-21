"""轻量复习间隔规则（确定性、可单测）。"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from yancuo_win.domain.rules import DomainError

# 五档：1 完全不会 … 5 完全掌握
REVIEW_GRADES = {
    1: "完全不会",
    2: "有思路但做不出",
    3: "计算出错",
    4: "基本正确",
    5: "完全掌握",
}

# 固定间隔（天）：不做复杂 SM-2
_INTERVAL_DAYS = {
    1: 1,
    2: 2,
    3: 4,
    4: 7,
    5: 14,
}


def validate_grade(grade: int) -> int:
    if grade not in REVIEW_GRADES:
        raise DomainError("复习结果必须是 1–5")
    return grade


def interval_days_for_grade(grade: int) -> int:
    grade = validate_grade(grade)
    return _INTERVAL_DAYS[grade]


def compute_next_review_at(
    grade: int,
    *,
    from_dt: datetime | None = None,
) -> datetime:
    """根据打分计算下次复习时间（UTC，日期对齐到当天 00:00+间隔）。"""
    grade = validate_grade(grade)
    base = from_dt or datetime.now(timezone.utc)
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)
    day = base.astimezone(timezone.utc).date()
    nxt = day + timedelta(days=interval_days_for_grade(grade))
    return datetime(nxt.year, nxt.month, nxt.day, tzinfo=timezone.utc)


def mastery_from_grade(grade: int) -> int:
    """将五档映射到 mastery 1–5。"""
    return validate_grade(grade)


def is_due(next_review_at: datetime | None, *, today: date | None = None) -> bool:
    if next_review_at is None:
        return True  # 从未复习：进入今日复习候选（由调用方再过滤状态）
    today = today or datetime.now(timezone.utc).date()
    due_date = next_review_at.astimezone(timezone.utc).date()
    return due_date <= today
