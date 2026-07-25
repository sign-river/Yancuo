"""领域常量与本地身份。"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

# 与 protocol/data-format-v1.md、迁移目标版本一致
DATA_FORMAT_VERSION = 1
SCHEMA_VERSION = 17


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
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(identity.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def load_or_create_identity(path: Path, display_name: str = "本地用户") -> LocalIdentity:
    """首次启动创建本地身份；不依赖任何云账号。"""
    if path.is_file():
        raw = json.loads(path.read_text(encoding="utf-8"))
        identity = LocalIdentity(
            user_id=str(raw["user_id"]),
            device_id=str(raw["device_id"]),
            database_id=str(raw["database_id"]),
            profile_id=str(raw.get("profile_id") or _new_id("profile")),
            last_snapshot_id=str(raw.get("last_snapshot_id") or ""),
            display_name=str(raw.get("display_name", display_name)),
            created_at=str(raw.get("created_at", "")),
        )
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
