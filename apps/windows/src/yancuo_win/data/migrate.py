"""可重复执行的 schema 迁移。"""

from __future__ import annotations

import logging
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


MIGRATIONS: dict[int, MigrationFn] = {
    1: _migrate_to_v1,
    2: _migrate_to_v2,
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
    }
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table'")
        ).fetchall()
    existing = {row[0] for row in rows}
    return sorted(required - existing)
