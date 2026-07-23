"""领域常量与本地身份。"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

# 与 protocol/data-format-v1.md、迁移目标版本一致
DATA_FORMAT_VERSION = 1
SCHEMA_VERSION = 8


@dataclass(frozen=True)
class LocalIdentity:
    user_id: str
    device_id: str
    database_id: str
    display_name: str
    created_at: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def load_or_create_identity(path: Path, display_name: str = "本地用户") -> LocalIdentity:
    """首次启动创建本地身份；不依赖任何云账号。"""
    if path.is_file():
        raw = json.loads(path.read_text(encoding="utf-8"))
        return LocalIdentity(
            user_id=str(raw["user_id"]),
            device_id=str(raw["device_id"]),
            database_id=str(raw["database_id"]),
            display_name=str(raw.get("display_name", display_name)),
            created_at=str(raw.get("created_at", "")),
        )

    identity = LocalIdentity(
        user_id=_new_id("usr"),
        device_id=_new_id("dev_win"),
        database_id=_new_id("db"),
        display_name=display_name,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(identity.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return identity
