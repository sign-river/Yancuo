"""领域常量与本地身份。"""

from __future__ import annotations

import json
import os
import re
import tempfile
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

# 与 protocol/data-format-v1.md、迁移目标版本一致
DATA_FORMAT_VERSION = 1
SCHEMA_VERSION = 22
MAX_IDENTITY_BYTES = 64 * 1024
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")


@dataclass(frozen=True)
class LocalIdentity:
    user_id: str
    device_id: str
    database_id: str
    profile_id: str
    last_snapshot_id: str
    display_name: str
    created_at: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _write_identity(path: Path, identity: LocalIdentity) -> None:
    path = Path(path)
    if path.is_symlink():
        raise ValueError("identity.json 不能是符号链接")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(identity.to_dict(), ensure_ascii=False, indent=2) + "\n"
    fd, temporary_name = tempfile.mkstemp(
        prefix=".identity-", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if path.is_symlink():
            raise ValueError("identity.json 不能是符号链接")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _validated_identity(raw: object, display_name: str) -> LocalIdentity:
    if not isinstance(raw, dict):
        raise ValueError("identity.json 根节点必须是对象")
    required: dict[str, str] = {}
    for field in ("user_id", "device_id", "database_id"):
        value = raw.get(field)
        if not isinstance(value, str) or _SAFE_ID_RE.fullmatch(value) is None:
            raise ValueError(f"identity.json 的 {field} 无效")
        required[field] = value
    profile_id = raw.get("profile_id") or _new_id("profile")
    if (
        not isinstance(profile_id, str)
        or not profile_id.startswith("profile_")
        or _SAFE_ID_RE.fullmatch(profile_id) is None
    ):
        raise ValueError("identity.json 的 profile_id 无效")
    snapshot_id = raw.get("last_snapshot_id") or ""
    if not isinstance(snapshot_id, str) or (
        snapshot_id
        and (
            not snapshot_id.startswith("snapshot_")
            or _SAFE_ID_RE.fullmatch(snapshot_id) is None
        )
    ):
        raise ValueError("identity.json 的 last_snapshot_id 无效")
    shown_name = raw.get("display_name", display_name)
    created_at = raw.get("created_at", "")
    if not isinstance(shown_name, str) or len(shown_name) > 256:
        raise ValueError("identity.json 的 display_name 无效")
    if not isinstance(created_at, str) or len(created_at) > 64:
        raise ValueError("identity.json 的 created_at 无效")
    return LocalIdentity(
        user_id=required["user_id"],
        device_id=required["device_id"],
        database_id=required["database_id"],
        profile_id=profile_id,
        last_snapshot_id=snapshot_id,
        display_name=shown_name,
        created_at=created_at,
    )


def load_or_create_identity(path: Path, display_name: str = "本地用户") -> LocalIdentity:
    """首次启动创建本地身份；不依赖任何云账号。"""
    path = Path(path)
    if path.is_symlink():
        raise ValueError("identity.json 不能是符号链接")
    if path.is_file():
        try:
            size = path.stat().st_size
            if size <= 0 or size > MAX_IDENTITY_BYTES:
                raise ValueError("identity.json 为空或过大")
            with path.open("rb") as stream:
                payload = stream.read(MAX_IDENTITY_BYTES + 1)
            if len(payload) != size or len(payload) > MAX_IDENTITY_BYTES:
                raise ValueError("identity.json 在读取期间发生变化或过大")
            raw = json.loads(payload.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("identity.json 无法读取或解析") from exc
        identity = _validated_identity(raw, display_name)
        if "profile_id" not in raw:
            _write_identity(path, identity)
        return identity

    identity = LocalIdentity(
        user_id=_new_id("usr"),
        device_id=_new_id("dev_win"),
        database_id=_new_id("db"),
        profile_id=_new_id("profile"),
        last_snapshot_id="",
        display_name=display_name,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    _write_identity(path, identity)
    return identity


def bind_profile(path: Path, identity: LocalIdentity, profile_id: str) -> LocalIdentity:
    """Bind one local data space to a confirmed canonical cloud profile."""

    profile_id = profile_id.strip()
    if not profile_id.startswith("profile_") or len(profile_id) <= len("profile_"):
        raise ValueError("profile_id 格式无效")
    if any(char not in "abcdefghijklmnopqrstuvwxyz0123456789_" for char in profile_id):
        raise ValueError("profile_id 格式无效")
    bound = LocalIdentity(
        user_id=identity.user_id,
        device_id=identity.device_id,
        database_id=identity.database_id,
        profile_id=profile_id,
        last_snapshot_id="",
        display_name=identity.display_name,
        created_at=identity.created_at,
    )
    _write_identity(path, bound)
    return bound


def record_snapshot_head(
    path: Path, identity: LocalIdentity, snapshot_id: str
) -> LocalIdentity:
    """Persist the cloud snapshot on which this device's local edits are based."""

    snapshot_id = snapshot_id.strip()
    if not snapshot_id.startswith("snapshot_"):
        raise ValueError("snapshot_id 格式无效")
    updated = LocalIdentity(
        user_id=identity.user_id,
        device_id=identity.device_id,
        database_id=identity.database_id,
        profile_id=identity.profile_id,
        last_snapshot_id=snapshot_id,
        display_name=identity.display_name,
        created_at=identity.created_at,
    )
    _write_identity(path, updated)
    return updated
