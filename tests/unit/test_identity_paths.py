"""身份与目录初始化。"""

from __future__ import annotations

from pathlib import Path

import pytest

from yancuo_win.config.settings import PathsConfig
from yancuo_win.domain.identity import load_or_create_identity
from yancuo_win.infrastructure.paths import build_data_paths, resolve_data_root


def test_identity_persists(tmp_path: Path) -> None:
    path = tmp_path / "identity.json"
    first = load_or_create_identity(path, display_name="测试")
    second = load_or_create_identity(path)
    assert first.user_id == second.user_id
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
