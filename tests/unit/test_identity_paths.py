"""身份与目录初始化。"""

from __future__ import annotations

from pathlib import Path
import json

import pytest

from yancuo_win.config.settings import PathsConfig
from yancuo_win.domain.identity import MAX_IDENTITY_BYTES, load_or_create_identity
from yancuo_win.infrastructure.paths import build_data_paths, resolve_data_root


def test_identity_persists(tmp_path: Path) -> None:
    path = tmp_path / "identity.json"
    first = load_or_create_identity(path, display_name="测试")
    second = load_or_create_identity(path)
    assert first.user_id == second.user_id
    assert first.profile_id == second.profile_id
    assert first.profile_id.startswith("profile_")
    assert first.last_snapshot_id == ""
    assert first.database_id == second.database_id
    assert first.device_id.startswith("dev_win_")


def test_data_paths_layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("YANCUO_DATA_ROOT", str(tmp_path / "data"))
    root = resolve_data_root()
    paths = build_data_paths(root, PathsConfig())
    paths.ensure_directories()
    assert paths.asset_objects_dir.is_dir()
    assert paths.inbox_dir.is_dir()
    assert paths.log_dir.is_dir()
    assert paths.database.name == "error_book.db"


def test_legacy_identity_receives_persistent_profile_id(tmp_path: Path) -> None:
    path = tmp_path / "identity.json"
    path.write_text(
        '{"user_id":"usr_old","device_id":"dev_win_old","database_id":"db_old"}',
        encoding="utf-8",
    )

    identity = load_or_create_identity(path)

    assert identity.profile_id.startswith("profile_")
    assert identity.last_snapshot_id == ""
    assert identity.profile_id in path.read_text(encoding="utf-8")


def test_identity_write_is_atomic_and_leaves_no_temporary_file(tmp_path: Path) -> None:
    path = tmp_path / "identity.json"

    identity = load_or_create_identity(path)

    assert json.loads(path.read_text(encoding="utf-8"))["profile_id"] == identity.profile_id
    assert list(tmp_path.glob(".identity-*.tmp")) == []


def test_identity_rejects_oversized_file(tmp_path: Path) -> None:
    path = tmp_path / "identity.json"
    path.write_bytes(b" " * (MAX_IDENTITY_BYTES + 1))

    with pytest.raises(ValueError, match="过大"):
        load_or_create_identity(path)


def test_identity_rejects_invalid_root_and_identifiers(tmp_path: Path) -> None:
    path = tmp_path / "identity.json"
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="根节点"):
        load_or_create_identity(path)

    path.write_text(
        json.dumps(
            {
                "user_id": "../outside",
                "device_id": "dev_win_valid",
                "database_id": "db_valid",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="user_id"):
        load_or_create_identity(path)
