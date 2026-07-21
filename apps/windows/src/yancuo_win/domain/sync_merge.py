"""字段级合并规则（纯函数，可单测）。"""

from __future__ import annotations

from typing import Any

# 两端皆改且值不同时必须进冲突 UI
CONFLICT_FIELDS = frozenset(
    {
        "question_markdown",
        "question_latex",
        "correct_answer",
        "solution_markdown",
        "error_analysis",
        "chapter_id",
        "status",
        "deleted_at",
    }
)

AUTO_OR_FIELDS = frozenset({"is_favorite"})
AUTO_UNION_FIELDS = frozenset({"tags"})


def _norm_tags(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return sorted({str(x) for x in value if str(x).strip()})


def merge_snapshots(
    base: dict[str, Any],
    local: dict[str, Any],
    remote: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """三路合并。返回 (merged, conflicts)。

    conflicts 项：{field, base, local, remote}
    """
    keys = sorted(set(base) | set(local) | set(remote))
    merged: dict[str, Any] = {}
    conflicts: list[dict[str, Any]] = []

    for key in keys:
        b = base.get(key)
        l = local.get(key, b)
        r = remote.get(key, b)
        lc = l != b
        rc = r != b

        if key in AUTO_UNION_FIELDS:
            # 标签：始终并集（含仅一端变更）
            merged[key] = _norm_tags(list(_norm_tags(l)) + list(_norm_tags(r)))
            # 若 base 也有，并集仍覆盖
            continue

        if key in AUTO_OR_FIELDS:
            merged[key] = bool(l) or bool(r)
            continue

        if lc and rc and l != r:
            # tags / favorite 已处理；其余同字段分歧一律冲突（保守）
            conflicts.append({"field": key, "base": b, "local": l, "remote": r})
            merged[key] = l  # 暂留本地，待审核
            continue
        if rc:
            merged[key] = r
        elif lc:
            merged[key] = l
        else:
            merged[key] = b

    return merged, conflicts


def apply_patch(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in patch.items():
        out[k] = v
    return out
