"""Operation 构造与校验。"""

from __future__ import annotations

import base64
import binascii
from datetime import datetime, timezone
import re
from typing import Any

from yancuo_win.data.ids import new_id
from yancuo_win.domain.rules import DomainError

OP_FORMAT = "yancuo-operation"
OP_FORMAT_VERSION = 1
ALLOWED_OPS = frozenset({"create", "update", "delete", "undelete"})
ALLOWED_ENTITIES = frozenset({"problem", "tag", "asset", "review"})
MAX_OPERATION_ATTACHMENT_BYTES = 32 * 1024 * 1024


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
    try:
        format_version = int(raw.get("format_version") or 0)
    except (TypeError, ValueError) as exc:
        raise DomainError("operation format_version 无效") from exc
    if format_version != OP_FORMAT_VERSION:
        raise DomainError("operation format_version 不受支持")
    operation_id = raw.get("operation_id")
    if not isinstance(operation_id, str) or not operation_id.startswith("op_"):
        raise DomainError("operation_id 格式不正确")
    for field in ("device_id", "database_id", "timestamp", "entity_id"):
        if not isinstance(raw.get(field), str) or not raw[field].strip():
            raise DomainError(f"operation 缺少有效 {field}")
    if raw.get("operation") not in ALLOWED_OPS:
        raise DomainError("operation 非法")
    if raw.get("entity_type") not in ALLOWED_ENTITIES:
        raise DomainError("entity_type 非法")
    if not isinstance(raw.get("changed_fields"), dict):
        raise DomainError("缺少 changed_fields")
    if "base_fields" in raw and not isinstance(raw["base_fields"], dict):
        raise DomainError("base_fields 必须是对象")
    if "tombstone" in raw and not isinstance(raw["tombstone"], bool):
        raise DomainError("tombstone 必须是布尔值")
    attachments = raw.get("attachments", [])
    if not isinstance(attachments, list) or len(attachments) > 100:
        raise DomainError("attachments 必须是最多 100 项的数组")
    attachment_bytes = 0
    for attachment in attachments:
        if not isinstance(attachment, dict):
            raise DomainError("attachment 必须是对象")
        if attachment.get("role") != "derived_figure":
            raise DomainError("Operation 只允许携带派生题图附件")
        if not isinstance(attachment.get("id"), str) or not attachment["id"].startswith("asset_"):
            raise DomainError("attachment id 无效")
        if not isinstance(attachment.get("sha256"), str) or re.fullmatch(r"[0-9a-f]{64}", attachment["sha256"]) is None:
            raise DomainError("attachment sha256 无效")
        mime_type = attachment.get("mime_type")
        if mime_type is not None and (
            not isinstance(mime_type, str) or not mime_type.startswith("image/")
        ):
            raise DomainError("attachment mime_type 无效")
        for dimension in ("size_bytes", "width", "height"):
            value = attachment.get(dimension)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value < 0
            ):
                raise DomainError(f"attachment {dimension} 无效")
        payload = attachment.get("content_base64")
        if not isinstance(payload, str) or len(payload) > 48 * 1024 * 1024:
            raise DomainError("attachment 内容无效或过大")
        try:
            decoded = base64.b64decode(payload, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise DomainError("attachment Base64 无效") from exc
        if attachment.get("size_bytes") is not None and attachment["size_bytes"] != len(decoded):
            raise DomainError("attachment size_bytes 与内容不一致")
        if not decoded:
            raise DomainError("attachment 内容不能为空")
        attachment_bytes += len(decoded)
        if attachment_bytes > MAX_OPERATION_ATTACHMENT_BYTES:
            raise DomainError("Operation 附件总大小不能超过 32 MiB")

    normalized = dict(raw)
    for field in ("base_revision", "new_revision"):
        value = raw.get(field, 0)
        if isinstance(value, bool):
            raise DomainError(f"{field} 必须是非负整数")
        try:
            value = int(value)
        except (TypeError, ValueError) as exc:
            raise DomainError(f"{field} 必须是非负整数") from exc
        if value < 0:
            raise DomainError(f"{field} 必须是非负整数")
        normalized[field] = value
    normalized["format_version"] = format_version
    normalized["tombstone"] = bool(raw.get("tombstone", False))
    return normalized
