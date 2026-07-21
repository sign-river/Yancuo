"""题目状态机与领域校验。"""

from __future__ import annotations

ALLOWED_STATUS = frozenset({"inbox", "active", "archived", "trashed"})

# from -> allowed targets
_TRANSITIONS: dict[str, frozenset[str]] = {
    "inbox": frozenset({"active", "trashed"}),
    "active": frozenset({"archived", "trashed", "inbox"}),
    "archived": frozenset({"active", "trashed"}),
    "trashed": frozenset({"inbox", "active"}),  # 恢复
}


class DomainError(Exception):
    """领域规则违反。"""


def validate_priority(priority: int) -> int:
    if priority < 1 or priority > 5:
        raise DomainError("优先级必须在 1–5 之间")
    return priority


def validate_status(status: str) -> str:
    if status not in ALLOWED_STATUS:
        raise DomainError(f"非法状态：{status}")
    return status


def can_transition(current: str, target: str) -> bool:
    validate_status(current)
    validate_status(target)
    if current == target:
        return True
    return target in _TRANSITIONS.get(current, frozenset())


def assert_transition(current: str, target: str) -> None:
    if not can_transition(current, target):
        raise DomainError(f"不允许从 {current} 转换到 {target}")


IMMUTABLE_ASSET_ROLES = frozenset({"original"})


def assert_asset_writable(role: str, is_immutable: bool) -> None:
    if is_immutable or role in IMMUTABLE_ASSET_ROLES:
        raise DomainError("原始图片不可覆盖或修改")
