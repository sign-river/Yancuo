"""可重复执行的 schema 迁移。"""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import shutil
import sqlite3
from collections.abc import Callable

from sqlalchemy import select, text, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from yancuo_win.data.ids import new_id
from yancuo_win.data.models import (
    AiJobItem,
    Asset,
    AuditLog,
    Base,
    MetaKV,
    NoteAsset,
    NoteBlock,
    Problem,
    ProblemSet,
    ProblemSetAsset,
    Prompt,
)
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


def _migrate_to_v8(engine: Engine) -> None:
    """Add independent note documents, ordered blocks, tags, and assets."""

    Base.metadata.create_all(engine)
    with Session(engine) as session:
        set_schema_version(session, 8)
        session.commit()
    logger.info("migrated database to schema_version=8")


def _migrate_to_v9(engine: Engine) -> None:
    """Add recoverable note-intake sessions, source assets, groups, and blocks."""

    Base.metadata.create_all(engine)
    with Session(engine) as session:
        set_schema_version(session, 9)
        session.commit()
    logger.info("migrated database to schema_version=9")


def _migrate_to_v10(engine: Engine) -> None:
    """Add independent personal note collections and ordered membership."""

    Base.metadata.create_all(engine)
    with Session(engine) as session:
        set_schema_version(session, 10)
        session.commit()
    logger.info("migrated database to schema_version=10")


def _migrate_to_v11(engine: Engine) -> None:
    """Add a generic, rebuildable projection for problem and note search."""
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE VIRTUAL TABLE IF NOT EXISTS unified_search_documents_fts
            USING fts5(entity_type UNINDEXED, entity_id UNINDEXED, title, body,
                       tags_text, collections_text, knowledge_path, tokenize='trigram')
        """))
    with Session(engine) as session:
        set_schema_version(session, 11)
        session.commit()
    logger.info("migrated database to schema_version=11")


def _migrate_to_v12(engine: Engine) -> None:
    """Add durable successful-recognition cache entries."""

    Base.metadata.create_all(engine)
    with Session(engine) as session:
        set_schema_version(session, 12)
        session.commit()
    logger.info("migrated database to schema_version=12")


def _migrate_to_v13(engine: Engine) -> None:
    """Add ordered multi-image recognition units without rewriting old intake data."""

    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        columns = {
            row[1]
            for row in conn.execute(text("PRAGMA table_info(ai_job_items)"))
        }
        if "recognition_unit_id" not in columns:
            conn.execute(
                text(
                    "ALTER TABLE ai_job_items ADD COLUMN recognition_unit_id "
                    "VARCHAR(64) REFERENCES intake_recognition_units(id)"
                )
            )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_ai_job_items_recognition_unit_id "
                    "ON ai_job_items (recognition_unit_id)"
                )
            )
    with Session(engine) as session:
        set_schema_version(session, 13)
        session.commit()
    logger.info("migrated database to schema_version=13")


def _migrate_to_v14(engine: Engine) -> None:
    """Add composite problem containers without changing existing problems."""

    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        columns = {row[1] for row in conn.execute(text("PRAGMA table_info(problems)"))}
        if "problem_set_id" not in columns:
            conn.execute(text("ALTER TABLE problems ADD COLUMN problem_set_id VARCHAR(64) REFERENCES problem_sets(id)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_problems_problem_set_id ON problems (problem_set_id)"))
        if "item_order" not in columns:
            conn.execute(text("ALTER TABLE problems ADD COLUMN item_order INTEGER"))
    with Session(engine) as session:
        set_schema_version(session, 14)
        session.commit()
    logger.info("migrated database to schema_version=14")


def _migrate_to_v15(engine: Engine) -> None:
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        set_schema_version(session, 15)
        session.commit()
    logger.info("migrated database to schema_version=15")


def _migrate_to_v16(engine: Engine) -> None:
    """Add study history and explicit review enrollment without fabricating history."""

    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        columns = {row[1] for row in conn.execute(text("PRAGMA table_info(problems)"))}
        if "review_enabled" not in columns:
            conn.execute(
                text("ALTER TABLE problems ADD COLUMN review_enabled BOOLEAN NOT NULL DEFAULT 1")
            )
        # Existing rows preserve their prior behavior: a null due date is still an
        # enabled, first-review item. New detailed records start from this version.
        conn.execute(text("UPDATE problems SET review_enabled = 1 WHERE review_enabled IS NULL"))
    with Session(engine) as session:
        set_schema_version(session, 16)
        session.commit()
    logger.info("migrated database to schema_version=16")


def _migrate_to_v17(engine: Engine) -> None:
    """Add durable problem-scoped conversations and messages."""

    Base.metadata.create_all(engine)
    with Session(engine) as session:
        set_schema_version(session, 17)
        session.commit()
    logger.info("migrated database to schema_version=17")


def _migrate_to_v18(engine: Engine) -> None:
    """Add named review plans and the explicit waiting queue."""

    Base.metadata.create_all(engine)
    with Session(engine) as session:
        set_schema_version(session, 18)
        session.commit()
    logger.info("migrated database to schema_version=18")


def _migrate_to_v19(engine: Engine) -> None:
    """Add immutable completion records for read-only note review."""

    Base.metadata.create_all(engine)
    with Session(engine) as session:
        set_schema_version(session, 19)
        session.commit()
    logger.info("migrated database to schema_version=19")


def _migrate_to_v20(engine: Engine) -> None:
    """Add ordered question content blocks without rewriting legacy questions."""
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        columns = {row[1] for row in conn.execute(text("PRAGMA table_info(problems)")).fetchall()}
        if "question_content_json" not in columns:
            conn.execute(text("ALTER TABLE problems ADD COLUMN question_content_json TEXT NOT NULL DEFAULT '[]'"))
    with Session(engine) as session:
        set_schema_version(session, 20)
        session.commit()
    logger.info("migrated database to schema_version=20")


def _migrate_to_v21(engine: Engine) -> None:
    """Store immutable per-message visual reference snapshots."""

    with engine.begin() as conn:
        columns = {row[1] for row in conn.execute(text("PRAGMA table_info(problem_messages)"))}
        if "reference_snapshot_json" not in columns:
            conn.execute(text("ALTER TABLE problem_messages ADD COLUMN reference_snapshot_json TEXT NOT NULL DEFAULT '[]'"))
    with Session(engine) as session:
        set_schema_version(session, 21)
        session.commit()
    logger.info("migrated database to schema_version=21")


def _migrate_to_v22(engine: Engine) -> None:
    """Persist resumable AI task ownership, configuration, and streamed events."""

    Base.metadata.create_all(engine)
    additions = {
        "domain": "VARCHAR(32) NOT NULL DEFAULT 'generic'",
        "context_id": "VARCHAR(64) NOT NULL DEFAULT ''",
        "config_json": "TEXT NOT NULL DEFAULT '{}'",
        "response_text": "TEXT NOT NULL DEFAULT ''",
        "result_json": "TEXT NOT NULL DEFAULT '{}'",
        "attempt_count": "INTEGER NOT NULL DEFAULT 0",
        "started_at": "DATETIME",
        "heartbeat_at": "DATETIME",
    }
    with engine.begin() as conn:
        columns = {
            row[1] for row in conn.execute(text("PRAGMA table_info(ai_jobs)"))
        }
        for name, declaration in additions.items():
            if name not in columns:
                conn.execute(
                    text(f"ALTER TABLE ai_jobs ADD COLUMN {name} {declaration}")
                )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_ai_jobs_domain_status "
                "ON ai_jobs(domain, status)"
            )
        )
    with Session(engine) as session:
        set_schema_version(session, 22)
        session.commit()
    logger.info("migrated database to schema_version=22")


def _migrate_to_v23(engine: Engine) -> None:
    """Retire provably redundant formal originals and scrub source coordinates."""

    retired_paths: set[str] = set()
    with Session(engine) as session:
        assets_by_id = {
            asset.id: asset
            for asset in session.scalars(select(Asset)).all()
        }
        for problem in session.scalars(select(Problem)).all():
            try:
                blocks = json.loads(problem.question_content_json or "[]")
            except json.JSONDecodeError:
                blocks = None
            safe = isinstance(blocks, list)
            normalized: list[object] = []
            if safe:
                for value in blocks:
                    if not isinstance(value, dict):
                        safe = False
                        normalized.append(value)
                        continue
                    block = dict(value)
                    if block.get("type") == "figure":
                        derived_id = str(block.get("derived_asset_id") or "")
                        derived = assets_by_id.get(derived_id)
                        if derived is None or derived.role != "derived_figure":
                            safe = False
                        elif derived.problem_id != problem.id:
                            safe = False
                        else:
                            block.pop("source_asset_id", None)
                            block.pop("source_image_index", None)
                            block.pop("source_region", None)
                    normalized.append(block)
                problem.question_content_json = json.dumps(
                    normalized, ensure_ascii=False
                )

            originals = [
                value
                for value in assets_by_id.values()
                if value.problem_id == problem.id and value.role == "original"
            ]
            if not originals:
                continue
            if safe:
                original_ids = [value.id for value in originals]
                session.execute(
                    update(AiJobItem)
                    .where(AiJobItem.asset_id.in_(original_ids))
                    .values(asset_id=None)
                )
                for original in originals:
                    if original.relative_path:
                        retired_paths.add(original.relative_path)
                    session.delete(original)
            else:
                session.add(
                    AuditLog(
                        id=new_id("audit"),
                        action="legacy_original_cleanup_blocked",
                        entity_type="problem",
                        entity_id=problem.id,
                        detail_json=json.dumps(
                            {
                                "reason": "figure_without_verified_derived_asset",
                                "original_count": len(originals),
                            },
                            ensure_ascii=False,
                        ),
                        actor="migration_v23",
                    )
                )

        for note_asset in session.scalars(
            select(NoteAsset).where(NoteAsset.role == "original")
        ).all():
            if note_asset.relative_path:
                retired_paths.add(note_asset.relative_path)
            session.delete(note_asset)
        session.execute(update(NoteBlock).values(source_region_json="{}"))

        for problem_set in session.scalars(select(ProblemSet)).all():
            originals = session.scalars(
                select(ProblemSetAsset).where(
                    ProblemSetAsset.problem_set_id == problem_set.id
                )
            ).all()
            if not originals:
                continue
            if problem_set.material_markdown.strip():
                for original in originals:
                    if original.relative_path:
                        retired_paths.add(original.relative_path)
                    session.delete(original)
            else:
                session.add(
                    AuditLog(
                        id=new_id("audit"),
                        action="legacy_original_cleanup_blocked",
                        entity_type="problem_set",
                        entity_id=problem_set.id,
                        detail_json=json.dumps(
                            {"reason": "material_text_missing"}, ensure_ascii=False
                        ),
                        actor="migration_v23",
                    )
                )

        session.merge(
            MetaKV(
                key="retired_original_paths_v23",
                value=json.dumps(sorted(retired_paths), ensure_ascii=False),
            )
        )
        set_schema_version(session, 23)
        session.commit()
    logger.info(
        "migrated database to schema_version=23; retired %s original object references",
        len(retired_paths),
    )


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
    8: _migrate_to_v8,
    9: _migrate_to_v9,
    10: _migrate_to_v10,
    11: _migrate_to_v11,
    12: _migrate_to_v12,
    13: _migrate_to_v13,
    14: _migrate_to_v14,
    15: _migrate_to_v15,
    16: _migrate_to_v16,
    17: _migrate_to_v17,
    18: _migrate_to_v18,
    19: _migrate_to_v19,
    20: _migrate_to_v20,
    21: _migrate_to_v21,
    22: _migrate_to_v22,
    23: _migrate_to_v23,
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
        "ai_job_events",
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
    if get_schema_version(engine) >= 8:
        required.update({"note_documents", "note_blocks", "note_assets", "note_tags"})
    if get_schema_version(engine) >= 9:
        required.update(
            {
                "note_intake_sessions",
                "note_intake_assets",
                "note_draft_groups",
                "note_draft_blocks",
            }
        )
    if get_schema_version(engine) >= 10:
        required.update({"note_collections", "note_collection_documents"})
    if get_schema_version(engine) >= 11:
        required.update({"unified_search_documents", "unified_search_documents_fts"})
    if get_schema_version(engine) >= 12:
        required.add("ai_recognition_cache")
    if get_schema_version(engine) >= 13:
        required.update(
            {
                "intake_recognition_units",
                "intake_recognition_unit_assets",
                "intake_candidate_units",
            }
        )
    if get_schema_version(engine) >= 14:
        required.update({"problem_sets", "problem_set_assets"})
    if get_schema_version(engine) >= 16:
        required.update({"study_sessions", "study_records"})
    if get_schema_version(engine) >= 17:
        required.update({"problem_conversations", "problem_messages"})
    if get_schema_version(engine) >= 18:
        required.update({"review_plans", "review_plan_items", "review_waiting_items"})
    if get_schema_version(engine) >= 19:
        required.add("note_study_records")
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table'")
        ).fetchall()
    existing = {row[0] for row in rows}
    return sorted(required - existing)
