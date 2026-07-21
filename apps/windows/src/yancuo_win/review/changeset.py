"""AI 结构化结果校验与字段权限。"""

from __future__ import annotations

from typing import Any

from yancuo_win.domain.rules import DomainError

# 默认可由 AI 提议修改的字段（禁止删除题目 / 禁止动原图）
DEFAULT_ALLOWED_FIELDS = frozenset(
    {
        "title",
        "question_markdown",
        "question_latex",
        "user_answer",
        "correct_answer",
        "solution_markdown",
        "error_analysis",
        "notes",
        "tags",
    }
)

FORBIDDEN_FIELDS = frozenset(
    {
        "id",
        "status",
        "revision",
        "deleted_at",
        "assets",
        "original_path",
    }
)


def validate_and_filter_proposal(
    raw: dict[str, Any],
    *,
    allowed_fields: set[str] | frozenset[str],
    allow_delete: bool = False,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    if not isinstance(raw, dict):
        raise DomainError("结构化结果必须是对象")
    if raw.get("delete") or raw.get("delete_problem"):
        if not allow_delete:
            raise DomainError("AI 无权删除题目")
    uncertain = raw.get("uncertain_fields") or []
    if not isinstance(uncertain, list):
        uncertain = []

    filtered: dict[str, Any] = {}
    for key, value in raw.items():
        if key in {"uncertain_fields", "delete", "delete_problem"}:
            continue
        if key in FORBIDDEN_FIELDS:
            continue
        if key not in allowed_fields:
            continue
        if key == "tags":
            if isinstance(value, list):
                filtered[key] = [str(x) for x in value][:20]
            continue
        if value is None:
            continue
        filtered[key] = value if not isinstance(value, str) else value
    return filtered, [u for u in uncertain if isinstance(u, dict)]


def snapshot_problem_fields(problem) -> dict[str, Any]:  # noqa: ANN001
    return {
        "title": problem.title,
        "question_markdown": problem.question_markdown,
        "question_latex": problem.question_latex,
        "user_answer": problem.user_answer,
        "correct_answer": problem.correct_answer,
        "solution_markdown": problem.solution_markdown,
        "error_analysis": problem.error_analysis,
        "notes": problem.notes,
        "priority": problem.priority,
        "status": problem.status,
        "revision": problem.revision,
    }


def field_diffs(before: dict[str, Any], proposed: dict[str, Any]) -> list[dict[str, Any]]:
    diffs = []
    keys = sorted(set(before) | set(proposed))
    for key in keys:
        if key in {"status", "revision", "priority"} and key not in proposed:
            continue
        old = before.get(key)
        new = proposed.get(key, old)
        if key not in proposed:
            continue
        if old != new:
            diffs.append({"field": key, "before": old, "after": new})
    return diffs
