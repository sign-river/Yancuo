"""迁移与启动编排测试。"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from yancuo_win.application.bootstrap import bootstrap_runtime
from yancuo_win.config.settings import default_toml_path
from yancuo_win.data.db import make_engine
from yancuo_win.data.migrate import (
    create_pre_migration_backup,
    get_schema_version,
    migrate,
    restore_pre_migration_backup,
    verify_core_tables,
    verify_sqlite_database,
)
from sqlalchemy import text

migrate_module = importlib.import_module("yancuo_win.data.migrate")


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
    assert runtime.schema_version == 8
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


def test_migrate_v6_to_v7_adds_trigram_search_projection(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "search-upgrade.db")
    assert migrate(engine, target_version=6) == 6
    assert migrate(engine, target_version=7) == 7
    with engine.begin() as connection:
        tables = {
            row[0]
            for row in connection.execute(
                text("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')")
            )
        }
        connection.execute(
            text(
                "INSERT INTO search_documents_fts"
                "(problem_id, title, body, tags_text, knowledge_path) "
                "VALUES ('p1', '二重积分', '计算区域', '', '高数/积分')"
            )
        )
        matches = connection.execute(
            text(
                "SELECT problem_id FROM search_documents_fts "
                "WHERE search_documents_fts MATCH '重积分'"
            )
        ).fetchall()
    assert {"search_documents", "search_documents_fts"} <= tables
    assert matches == [("p1",)]


def test_migrate_v7_to_v8_adds_independent_note_tables(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "notes-upgrade.db")
    assert migrate(engine, target_version=7) == 7
    assert migrate(engine, target_version=8) == 8
    with engine.connect() as connection:
        tables = {
            row[0]
            for row in connection.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            )
        }
    assert {"note_documents", "note_blocks", "note_assets", "note_tags"} <= tables
    assert verify_core_tables(engine) == []


def test_pre_migration_backup_can_restore_original_database(tmp_path: Path) -> None:
    database = tmp_path / "restore.db"
    engine = make_engine(database)
    assert migrate(engine, target_version=6) == 6
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO meta_kv(key, value) VALUES ('restore_marker', 'original')")
        )
    engine.dispose()

    backup = create_pre_migration_backup(
        database,
        tmp_path / "backups",
        from_version=6,
        target_version=7,
    )
    damaged_engine = make_engine(database)
    with damaged_engine.begin() as connection:
        connection.execute(
            text("UPDATE meta_kv SET value='damaged' WHERE key='restore_marker'")
        )
    damaged_engine.dispose()

    restore_pre_migration_backup(
        backup,
        database,
        expected_schema_version=6,
    )
    verify_sqlite_database(database, expected_schema_version=6)
    restored = make_engine(database)
    with restored.connect() as connection:
        marker = connection.execute(
            text("SELECT value FROM meta_kv WHERE key='restore_marker'")
        ).scalar_one()
    restored.dispose()
    assert marker == "original"


def test_bootstrap_restores_backup_when_migration_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "failed-upgrade"
    database = data_root / "error_book.db"
    engine = make_engine(database)
    assert migrate(engine, target_version=6) == 6
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO meta_kv(key, value) VALUES ('restore_marker', 'original')")
        )
    engine.dispose()

    def fail_v7(upgrade_engine) -> None:
        with upgrade_engine.begin() as connection:
            connection.execute(
                text("UPDATE meta_kv SET value='damaged' WHERE key='restore_marker'")
            )
            connection.execute(
                text(
                    "UPDATE meta_kv SET value='7' WHERE key='schema_version'"
                )
            )
        raise RuntimeError("simulated migration failure")

    monkeypatch.setenv("YANCUO_DATA_ROOT", str(data_root))
    monkeypatch.setenv("YANCUO_CONFIG_FILE", str(default_toml_path()))
    monkeypatch.setitem(migrate_module.MIGRATIONS, 7, fail_v7)
    with pytest.raises(RuntimeError, match="simulated migration failure"):
        bootstrap_runtime()

    restored = make_engine(database)
    assert get_schema_version(restored) == 6
    with restored.connect() as connection:
        marker = connection.execute(
            text("SELECT value FROM meta_kv WHERE key='restore_marker'")
        ).scalar_one()
    restored.dispose()
    assert marker == "original"
    backups = list((data_root / "backups").glob("pre-migration-v6-to-v8-*.sqlite"))
    assert len(backups) == 1
    verify_sqlite_database(backups[0], expected_schema_version=6)
