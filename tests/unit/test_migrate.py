"""迁移与启动编排测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from yancuo_win.application.bootstrap import bootstrap_runtime
from yancuo_win.config.settings import default_toml_path
from yancuo_win.data.db import make_engine
from yancuo_win.data.migrate import get_schema_version, migrate, verify_core_tables


def test_migrate_creates_core_tables(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    engine = make_engine(db)
    assert get_schema_version(engine) == 0
    version = migrate(engine, target_version=1)
    assert version == 1
    assert get_schema_version(engine) == 1
    assert verify_core_tables(engine) == []
    assert migrate(engine, target_version=1) == 1


def test_bootstrap_creates_layout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("YANCUO_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("YANCUO_CONFIG_FILE", str(default_toml_path()))

    runtime = bootstrap_runtime()
    assert runtime.schema_version == 1
    assert runtime.paths.database.is_file()
    assert runtime.paths.asset_objects_dir.is_dir()
    assert runtime.paths.identity_file.is_file()
    assert runtime.identity.user_id.startswith("usr_")
    assert verify_core_tables(runtime.engine) == []
