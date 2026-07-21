"""可重复执行的 schema 迁移。"""

from __future__ import annotations

import logging
from collections.abc import Callable

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from yancuo_win.data.models import Base, MetaKV
from yancuo_win.domain.identity import SCHEMA_VERSION

logger = logging.getLogger("yancuo.data.migrate")

MigrationFn = Callable[[Engine], None]


def get_schema_version(engine: Engine) -> int:
    with engine.connect() as conn:
        exists = conn.execute(
            text(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='meta_kv'"
            )
        ).fetchone()
        if not exists:
            return 0
        row = conn.execute(
            text("SELECT value FROM meta_kv WHERE key='schema_version'")
        ).fetchone()
        if not row:
            return 0
        return int(row[0])


def set_schema_version(session: Session, version: int) -> None:
    existing = session.get(MetaKV, "schema_version")
    if existing is None:
        session.add(MetaKV(key="schema_version", value=str(version)))
    else:
        existing.value = str(version)


def _migrate_to_v1(engine: Engine) -> None:
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        set_schema_version(session, 1)
        # 标记数据格式版本，便于后续 .ebpack / 跨端校验
        if session.get(MetaKV, "data_format_version") is None:
            session.add(MetaKV(key="data_format_version", value="1"))
        session.commit()
    logger.info("migrated database to schema_version=1")


MIGRATIONS: dict[int, MigrationFn] = {
    1: _migrate_to_v1,
}


def migrate(engine: Engine, target_version: int | None = None) -> int:
    """将数据库迁移到目标版本。可重复执行（已是目标版本则 no-op）。"""
    target = target_version if target_version is not None else SCHEMA_VERSION
    current = get_schema_version(engine)
    if current > target:
        raise RuntimeError(
            f"数据库 schema_version={current} 高于程序支持的 {target}，请升级软件后再打开。"
        )

    for version in range(current + 1, target + 1):
        fn = MIGRATIONS.get(version)
        if fn is None:
            raise RuntimeError(f"缺少迁移脚本：v{version}")
        logger.info("applying migration v%s (from %s)", version, current)
        fn(engine)
        current = version

    return current


def verify_core_tables(engine: Engine) -> list[str]:
    """返回缺失的核心表名（空列表表示齐全）。"""
    required = {
        "meta_kv",
        "subjects",
        "chapters",
        "problems",
        "assets",
        "tags",
        "problem_tags",
        "versions",
    }
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table'")
        ).fetchall()
    existing = {row[0] for row in rows}
    return sorted(required - existing)
