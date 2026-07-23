"""可重复执行的 schema 迁移。"""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import shutil
import sqlite3
from collections.abc import Callable

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from yancuo_win.data.ids import new_id
from yancuo_win.data.models import Base, MetaKV, Prompt
from yancuo_win.domain.identity import SCHEMA_VERSION

logger = logging.getLogger("yancuo.data.migrate")

MigrationFn = Callable[[Engine], None]

STRUCTURE_PROMPT = """你是考研错题结构化助手。根据题目图片输出严格 JSON（不要 Markdown 围栏），字段如下：
{
  "title": "短标题",
  "question_markdown": "原题文本",
  "question_latex": "关键公式 LaTeX，可空",
  "user_answer": "用户作答，可空",
  "correct_answer": "正确答案，可空",
  "solution_markdown": "解析，可空",
  "error_analysis": "错因，可空",
  "tags": ["可选标签"],
  "uncertain_fields": [{"field": "字段名", "content": "存疑内容", "reason": "原因"}]
}
question_markdown、user_answer、correct_answer、solution_markdown、error_analysis 等 Markdown 字段中的公式必须使用 $...$ 或 $$...$$ 定界；question_latex 只写裸 LaTeX，不要添加公式定界符。
只填写允许修改的字段语义；不要建议删除题目；不要编造不存在的原图路径。
"""


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


def verify_sqlite_database(
    database_path: Path,
    *,
    expected_schema_version: int | None = None,
) -> None:
    """Verify that a SQLite database is readable, intact, and at the expected version."""

    with closing(sqlite3.connect(database_path)) as connection:
        with closing(connection.execute("PRAGMA integrity_check")) as cursor:
            integrity = cursor.fetchone()
        if integrity is None or integrity[0] != "ok":
            detail = integrity[0] if integrity else "no result"
            raise RuntimeError(f"SQLite 完整性检查失败：{detail}")
        if expected_schema_version is None:
            return
        with closing(
            connection.execute(
                "SELECT value FROM meta_kv WHERE key='schema_version'"
            )
        ) as cursor:
            row = cursor.fetchone()
        actual = int(row[0]) if row else 0
        if actual != expected_schema_version:
            raise RuntimeError(
                "SQLite schema 版本校验失败："
                f"期望 {expected_schema_version}，实际 {actual}"
            )


def create_pre_migration_backup(
    database_path: Path,
    backup_dir: Path,
    *,
    from_version: int,
    target_version: int,
) -> Path:
    """Create and verify an online SQLite backup before a schema upgrade."""

    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup_path = (
        backup_dir
        / f"pre-migration-v{from_version}-to-v{target_version}-{timestamp}.sqlite"
    )
    with closing(sqlite3.connect(database_path)) as source:
        with closing(sqlite3.connect(backup_path)) as destination:
            source.backup(destination)
    verify_sqlite_database(
        backup_path,
        expected_schema_version=from_version,
    )
    logger.info("created pre-migration backup: %s", backup_path)
    return backup_path


def restore_pre_migration_backup(
    backup_path: Path,
    database_path: Path,
    *,
    expected_schema_version: int,
) -> None:
    """Atomically restore a verified pre-migration backup."""

    verify_sqlite_database(
        backup_path,
        expected_schema_version=expected_schema_version,
    )
    restore_path = database_path.with_suffix(database_path.suffix + ".restore")
    try:
        shutil.copy2(backup_path, restore_path)
        verify_sqlite_database(
            restore_path,
            expected_schema_version=expected_schema_version,
        )
        os.replace(restore_path, database_path)
    finally:
        restore_path.unlink(missing_ok=True)
    verify_sqlite_database(
        database_path,
        expected_schema_version=expected_schema_version,
    )
    logger.warning("restored database from pre-migration backup: %s", backup_path)


def set_schema_version(session: Session, version: int) -> None:
    existing = session.get(MetaKV, "schema_version")
    if existing is None:
        session.add(MetaKV(key="schema_version", value=str(version)))
    else:
        existing.value = str(version)


def _seed_builtin_prompts(session: Session) -> None:
    from sqlalchemy import select

    existing = session.scalar(select(Prompt).where(Prompt.key == "structure_recognize"))
    if existing:
        return
    session.add(
        Prompt(
            id=new_id("prompt"),
            key="structure_recognize",
            name="题目结构化识别",
            body=STRUCTURE_PROMPT,
            version=1,
            is_builtin=True,
        )
    )


def _migrate_to_v1(engine: Engine) -> None:
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        set_schema_version(session, 1)
        if session.get(MetaKV, "data_format_version") is None:
            session.add(MetaKV(key="data_format_version", value="1"))
        session.commit()
    logger.info("migrated database to schema_version=1")


def _migrate_to_v2(engine: Engine) -> None:
    # 加法：创建阶段 C 新表，并写入内置提示词
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        _seed_builtin_prompts(session)
        set_schema_version(session, 2)
        session.commit()
    logger.info("migrated database to schema_version=2")


def _migrate_to_v3(engine: Engine) -> None:
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        set_schema_version(session, 3)
        session.commit()
    logger.info("migrated database to schema_version=3")


def _migrate_to_v4(engine: Engine) -> None:
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        set_schema_version(session, 4)
        session.commit()
    logger.info("migrated database to schema_version=4")


def _migrate_to_v5(engine: Engine) -> None:
    """Persist normalized source-image regions for AI intake candidates."""

    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        columns = {
            row[1]
            for row in conn.execute(text("PRAGMA table_info(review_items)")).fetchall()
        }
        if "region_json" not in columns:
            conn.execute(
                text(
                    "ALTER TABLE review_items "
                    "ADD COLUMN region_json TEXT NOT NULL DEFAULT '{}'"
                )
            )
    with Session(engine) as session:
        set_schema_version(session, 5)
        session.commit()
    logger.info("migrated database to schema_version=5")


def _migrate_to_v6(engine: Engine) -> None:
    """Add dedicated intake sessions/assets/candidates and AI item linkage."""

    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        columns = {
            row[1]
            for row in conn.execute(text("PRAGMA table_info(ai_job_items)")).fetchall()
        }
        if "intake_asset_id" not in columns:
            conn.execute(
                text(
                    "ALTER TABLE ai_job_items "
                    "ADD COLUMN intake_asset_id VARCHAR(64) "
                    "REFERENCES intake_assets(id)"
                )
            )
    with Session(engine) as session:
        set_schema_version(session, 6)
        session.commit()
    logger.info("migrated database to schema_version=6")


def _migrate_to_v7(engine: Engine) -> None:
    """Add local search projection and trigram FTS5 index."""

    Base.metadata.create_all(engine)
    ensure_search_index_schema(engine)
    with Session(engine) as session:
        set_schema_version(session, 7)
        session.commit()
    logger.info("migrated database to schema_version=7")


def ensure_search_index_schema(engine: Engine) -> None:
    """Create the platform-local FTS table and repair it from the projection."""

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS search_documents_fts
                USING fts5(
                    problem_id UNINDEXED,
                    title,
                    body,
                    tags_text,
                    knowledge_path,
                    tokenize='trigram'
                )
                """
            )
        )
        projection_count = int(
            conn.execute(text("SELECT count(*) FROM search_documents")).scalar_one()
        )
        fts_count = int(
            conn.execute(
                text("SELECT count(*) FROM search_documents_fts")
            ).scalar_one()
        )
        if projection_count != fts_count:
            conn.execute(text("DELETE FROM search_documents_fts"))
            conn.execute(
                text(
                    """
                    INSERT INTO search_documents_fts(
                        problem_id, title, body, tags_text, knowledge_path
                    )
                    SELECT
                        problem_id, title, body, tags_text, knowledge_path
                    FROM search_documents
                    """
                )
            )


MIGRATIONS: dict[int, MigrationFn] = {
    1: _migrate_to_v1,
    2: _migrate_to_v2,
    3: _migrate_to_v3,
    4: _migrate_to_v4,
    5: _migrate_to_v5,
    6: _migrate_to_v6,
    7: _migrate_to_v7,
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
        "prompts",
        "ai_jobs",
        "ai_job_items",
        "review_sessions",
        "review_items",
        "audit_logs",
        "sync_operations",
        "problem_origins",
        "intake_sessions",
        "intake_assets",
        "intake_candidates",
    }
    if get_schema_version(engine) >= 7:
        required.update({"search_documents", "search_documents_fts"})
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table'")
        ).fetchall()
    existing = {row[0] for row in rows}
    return sorted(required - existing)
