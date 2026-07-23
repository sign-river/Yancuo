"""迁移与启动编排测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from yancuo_win.application.bootstrap import bootstrap_runtime
from yancuo_win.config.settings import default_toml_path
from yancuo_win.data.db import make_engine
from yancuo_win.data.migrate import get_schema_version, migrate, verify_core_tables
from sqlalchemy import text


def test_migrate_creates_core_tables(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    engine = make_engine(db)
    assert get_schema_version(engine) == 0
    version = migrate(engine, target_version=2)
    assert version == 2
    assert get_schema_version(engine) == 2
    assert verify_core_tables(engine) == []
    assert migrate(engine, target_version=2) == 2


def test_bootstrap_creates_layout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("YANCUO_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("YANCUO_CONFIG_FILE", str(default_toml_path()))

    runtime = bootstrap_runtime()
    assert runtime.schema_version == 6
    assert runtime.paths.database.is_file()
    assert runtime.paths.asset_objects_dir.is_dir()
    assert runtime.paths.identity_file.is_file()
    assert runtime.identity.user_id.startswith("usr_")
    assert verify_core_tables(runtime.engine) == []


def test_migrate_v4_to_v5_adds_review_region(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "upgrade.db")
    assert migrate(engine, target_version=4) == 4
    # Current ORM metadata includes v5 fields even when constructing a test
    # database at an older target. Remove it to reproduce an actual v4 file.
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE review_items DROP COLUMN region_json"))
    with engine.connect() as connection:
        columns = {
            row[1]
            for row in connection.execute(text("PRAGMA table_info(review_items)"))
        }
    assert "region_json" not in columns

    assert migrate(engine, target_version=5) == 5
    with engine.connect() as connection:
        columns = {
            row[1]
            for row in connection.execute(text("PRAGMA table_info(review_items)"))
        }
    assert "region_json" in columns


def test_migrate_v5_to_v6_adds_dedicated_intake_tables(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "intake-upgrade.db")
    assert migrate(engine, target_version=5) == 5
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE ai_job_items"))
        connection.execute(text("DROP TABLE intake_candidates"))
        connection.execute(text("DROP TABLE intake_assets"))
        connection.execute(text("DROP TABLE intake_sessions"))
        connection.execute(
            text(
                """
                CREATE TABLE ai_job_items (
                    id VARCHAR(64) PRIMARY KEY,
                    job_id VARCHAR(64) NOT NULL REFERENCES ai_jobs(id),
                    problem_id VARCHAR(64) REFERENCES problems(id),
                    asset_id VARCHAR(64) REFERENCES assets(id),
                    status VARCHAR(32) NOT NULL DEFAULT 'pending',
                    raw_response TEXT NOT NULL DEFAULT '',
                    structured_json TEXT NOT NULL DEFAULT '{}',
                    error_message TEXT NOT NULL DEFAULT '',
                    cost_estimate FLOAT NOT NULL DEFAULT 0,
                    created_at DATETIME,
                    updated_at DATETIME
                )
                """
            )
        )

    assert migrate(engine, target_version=6) == 6
    with engine.connect() as connection:
        tables = {
            row[0]
            for row in connection.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            )
        }
        columns = {
            row[1]
            for row in connection.execute(
                text("PRAGMA table_info(ai_job_items)")
            )
        }
    assert {"intake_sessions", "intake_assets", "intake_candidates"} <= tables
    assert "intake_asset_id" in columns
