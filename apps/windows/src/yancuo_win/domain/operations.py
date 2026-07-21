"""Operation 构造与校验。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from yancuo_win.data.ids import new_id
from yancuo_win.domain.rules import DomainError

OP_FORMAT = "yancuo-operation"
OP_FORMAT_VERSION = 1
ALLOWED_OPS = frozenset({"create", "update", "delete", "undelete"})
ALLOWED_ENTITIES = frozenset({"problem", "tag", "asset", "review"})


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_operation(
    *,
    device_id: str,
    database_id: str,
    entity_type: str,
    entity_id: str,
    operation: str,
    changed_fields: dict[str, Any],
    base_revision: int = 0,
    new_revision: int = 0,
    tombstone: bool = False,
    operation_id: str | None = None,
) -> dict[str, Any]:
    if entity_type not in ALLOWED_ENTITIES:
        raise DomainError(f"不支持的 entity_type：{entity_type}")
    if operation not in ALLOWED_OPS:
        raise DomainError(f"不支持的 operation：{operation}")
    if not isinstance(changed_fields, dict):
        raise DomainError("changed_fields 必须是对象")
    op = {
        "format": OP_FORMAT,
        "format_version": OP_FORMAT_VERSION,
        "operation_id": operation_id or new_id("op"),
        "device_id": device_id,
        "database_id": database_id,
        "timestamp": utc_now_iso(),
        "entity_type": entity_type,
        "entity_id": entity_id,
        "operation": operation,
        "base_revision": int(base_revision),
        "new_revision": int(new_revision),
        "changed_fields": changed_fields,
        "tombstone": bool(tombstone),
    }
    validate_operation(op)
    return op


def validate_operation(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise DomainError("Operation 必须是对象")
    if raw.get("format") != OP_FORMAT:
        raise DomainError("不是 yancuo-operation")
    if int(raw.get("format_version") or 0) != OP_FORMAT_VERSION:
        raise DomainError("operation format_version 不受支持")
    if not str(raw.get("operation_id") or "").startswith("op_"):
        raise DomainError("operation_id 格式不正确")
    if raw.get("operation") not in ALLOWED_OPS:
        raise DomainError("operation 非法")
    if raw.get("entity_type") not in ALLOWED_ENTITIES:
        raise DomainError("entity_type 非法")
    if not isinstance(raw.get("changed_fields"), dict):
        raise DomainError("缺少 changed_fields")
    return raw
