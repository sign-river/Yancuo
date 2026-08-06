"""Operation 构造与校验。"""

from __future__ import annotations

import base64
import binascii
from datetime import datetime, timezone
import hashlib
import re
from typing import Any

from yancuo_win.data.ids import new_id
from yancuo_win.domain.rules import DomainError

OP_FORMAT = "yancuo-operation"
OP_FORMAT_VERSION = 1
ALLOWED_OPS = frozenset({"create", "update", "delete", "undelete"})
ALLOWED_ENTITIES = frozenset({"problem", "tag", "asset", "review"})
MAX_OPERATION_ATTACHMENT_BYTES = 32 * 1024 * 1024
MAX_OPERATION_ATTACHMENT_ITEM_BYTES = 32 * 1024 * 1024
MAX_OPERATION_ID_CHARS = 64
MAX_OPERATION_TIMESTAMP_CHARS = 64
MAX_OPERATION_FIELDS = 64
MAX_OPERATION_REVISION = 2**63 - 1
MAX_ATTACHMENT_DIMENSION = 100_000
PROBLEM_OPERATION_FIELDS = frozenset(
    {
        "status",
        "subject_id",
        "chapter_id",
        "problem_type",
        "title",
        "question_markdown",
        "question_latex",
        "question_content_json",
        "user_answer",
        "correct_answer",
        "solution_markdown",
        "error_analysis",
        "notes",
        "source_book",
        "source_year",
        "page_number",
        "original_number",
        "priority",
        "difficulty",
        "mastery",
        "is_favorite",
        "needs_redo",
        "allow_print",
        "human_confirmed",
        "next_review_at",
        "review_count",
        "deleted_at",
        "revision",
        "tags",
    }
)


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
    if (
        not isinstance(operation_id, str)
        or not operation_id.startswith("op_")
        or len(operation_id) > MAX_OPERATION_ID_CHARS
    ):
        raise DomainError("operation_id 格式不正确")
    for field in ("device_id", "database_id", "entity_id"):
        if (
            not isinstance(raw.get(field), str)
            or not raw[field].strip()
            or len(raw[field]) > MAX_OPERATION_ID_CHARS
        ):
            raise DomainError(f"operation 缺少有效 {field}")
    timestamp = raw.get("timestamp")
    if (
        not isinstance(timestamp, str)
        or not timestamp.strip()
        or len(timestamp) > MAX_OPERATION_TIMESTAMP_CHARS
    ):
        raise DomainError("operation 缺少有效 timestamp")
    try:
        parsed_timestamp = datetime.fromisoformat(timestamp.strip().replace("Z", "+00:00"))
        if parsed_timestamp.tzinfo is None or parsed_timestamp.utcoffset() is None:
            raise ValueError("timestamp must include a timezone")
        normalized_timestamp = parsed_timestamp.astimezone(timezone.utc).isoformat()
    except (ValueError, OverflowError) as exc:
        raise DomainError("operation timestamp 格式不正确") from exc
    if raw.get("operation") not in ALLOWED_OPS:
        raise DomainError("operation 非法")
    entity_type = raw.get("entity_type")
    if entity_type not in ALLOWED_ENTITIES:
        raise DomainError("entity_type 非法")
    field_maps: list[tuple[str, Any]] = [("changed_fields", raw.get("changed_fields"))]
    if "base_fields" in raw:
        field_maps.append(("base_fields", raw["base_fields"]))
    normalized_field_maps: dict[str, dict[str, Any]] = {}
    for label, fields in field_maps:
        if not isinstance(fields, dict):
            raise DomainError(f"{label} 必须是对象")
        if len(fields) > MAX_OPERATION_FIELDS:
            raise DomainError(f"{label} 字段过多")
        invalid_keys = [
            key
            for key in fields
            if not isinstance(key, str) or not key or len(key) > MAX_OPERATION_ID_CHARS
        ]
        if invalid_keys:
            raise DomainError(f"{label} 包含无效字段名")
        if entity_type == "problem":
            fields = {
                key: value for key, value in fields.items() if key in PROBLEM_OPERATION_FIELDS
            }
        normalized_field_maps[label] = dict(fields)
    if "tombstone" in raw and not isinstance(raw["tombstone"], bool):
        raise DomainError("tombstone 必须是布尔值")
    attachments = raw.get("attachments", [])
    if not isinstance(attachments, list) or len(attachments) > 100:
        raise DomainError("attachments 必须是最多 100 项的数组")
    attachment_bytes = 0
    attachment_ids: set[str] = set()
    for attachment in attachments:
        if not isinstance(attachment, dict):
            raise DomainError("attachment 必须是对象")
        if attachment.get("role") != "derived_figure":
            raise DomainError("Operation 只允许携带派生题图附件")
        if not isinstance(attachment.get("id"), str) or not attachment["id"].startswith("asset_"):
            raise DomainError("attachment id 无效")
        if attachment["id"] in attachment_ids:
            raise DomainError(f"Operation 内 attachment id 重复：{attachment['id']}")
        attachment_ids.add(attachment["id"])
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
        if len(attachment["id"]) > MAX_OPERATION_ID_CHARS:
            raise DomainError("attachment id 过长")
        if isinstance(mime_type, str) and len(mime_type) > 128:
            raise DomainError("attachment mime_type 过长")
        for dimension in ("width", "height"):
            if (attachment.get(dimension) or 0) > MAX_ATTACHMENT_DIMENSION:
                raise DomainError(f"attachment {dimension} 过大")
        if (attachment.get("size_bytes") or 0) > MAX_OPERATION_ATTACHMENT_ITEM_BYTES:
            raise DomainError("attachment size_bytes 过大")
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
        if hashlib.sha256(decoded).hexdigest() != attachment["sha256"]:
            raise DomainError("attachment sha256 与内容不一致")
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
        if value < 0 or value > MAX_OPERATION_REVISION:
            raise DomainError(f"{field} 必须是有效非负整数")
        normalized[field] = value
    normalized["format_version"] = format_version
    normalized["tombstone"] = bool(raw.get("tombstone", False))
    normalized["timestamp"] = normalized_timestamp
    normalized.update(normalized_field_maps)
    return normalized
