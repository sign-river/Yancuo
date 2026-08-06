"""应用启动编排。"""

from __future__ import annotations

from dataclasses import dataclass
import logging
import os
from pathlib import Path
import shutil
import tempfile

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from yancuo_win.config.settings import (
    AppSettings,
    ConfigError,
    apply_user_preferences,
    load_settings,
)
from yancuo_win.data.db import make_engine, make_session_factory
from yancuo_win.data.migrate import (
    create_pre_migration_backup,
    ensure_search_index_schema,
    get_schema_version,
    migrate,
    restore_pre_migration_backup,
    verify_core_tables,
)
from yancuo_win.domain.identity import (
    SCHEMA_VERSION,
    LocalIdentity,
    load_or_create_identity,
)
from yancuo_win.infrastructure.paths import (
    DataPaths,
    build_data_paths,
    resolve_data_root,
    setup_logging,
)


@dataclass
class RuntimeContext:
    settings: AppSettings
    paths: DataPaths
    identity: LocalIdentity
    engine: Engine
    session_factory: sessionmaker[Session]
    schema_version: int
    logger: logging.Logger


def _test_fast_enabled() -> bool:
    """Test-only fast bootstrap: reuse one fully migrated golden database.

    pytest sets YANCUO_TEST_FAST=1 (see tests/conftest.py).  A fresh test data
    root then copies a pre-migrated SQLite file instead of replaying every
    schema migration, while all other bootstrap behaviour stays identical.
    """
    return os.environ.get("YANCUO_TEST_FAST") == "1"


def _test_golden_database() -> Path:
    return Path(tempfile.gettempdir()) / f"yancuo-test-golden-v{SCHEMA_VERSION}.sqlite"


def _build_test_golden(golden: Path) -> None:
    """Migrate one pristine database and cache it for the whole pytest session."""
    staging = Path(tempfile.mkdtemp(prefix="yancuo-test-golden-"))
    try:
        database = staging / "golden.sqlite"
        engine = make_engine(database)
        migrate(engine, target_version=SCHEMA_VERSION)
        ensure_search_index_schema(engine)
        engine.dispose()
        staged = golden.with_name(golden.name + f".{os.getpid()}.tmp")
        shutil.copy2(database, staged)
        os.replace(staged, golden)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _ensure_test_database(database_path: Path) -> None:
    golden = _test_golden_database()
    if not golden.is_file():
        _build_test_golden(golden)
    shutil.copy2(golden, database_path)


def bootstrap_runtime(*, run_migrate: bool = True) -> RuntimeContext:
    """加载配置、创建目录、身份、数据库；可选执行迁移。"""
    try:
        settings = load_settings()
    except ConfigError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ConfigError(f"配置加载失败：{exc}") from exc

    root = resolve_data_root()
    settings = apply_user_preferences(settings, root)
    paths = build_data_paths(root, settings.paths)
    paths.ensure_directories()

    logger = setup_logging(paths.log_dir)
    logger.info("data root: %s", paths.root)

    identity = load_or_create_identity(paths.identity_file)
    logger.info(
        "identity user=%s device=%s database=%s",
        identity.user_id,
        identity.device_id,
        identity.database_id,
    )

    if _test_fast_enabled() and run_migrate and not paths.database.is_file():
        _ensure_test_database(paths.database)
    engine = make_engine(paths.database)
    schema_version = 0
    if run_migrate:
        target_version = SCHEMA_VERSION
        if settings.application.schema_version != target_version:
            logger.warning(
                "configured schema target %s is stale; using application target %s",
                settings.application.schema_version,
                target_version,
            )
            settings.application.schema_version = target_version
        current_version = get_schema_version(engine)
        backup_path = None
        if 0 < current_version < target_version:
            backup_path = create_pre_migration_backup(
                paths.database,
                paths.backup_dir,
                from_version=current_version,
                target_version=target_version,
            )
        try:
            schema_version = migrate(engine, target_version=target_version)
            if schema_version >= 7:
                ensure_search_index_schema(engine)
            missing = verify_core_tables(engine)
            if missing:
                raise RuntimeError(f"数据库缺少核心表：{', '.join(missing)}")
        except Exception:
            if backup_path is not None:
                engine.dispose()
                restore_pre_migration_backup(
                    backup_path,
                    paths.database,
                    expected_schema_version=current_version,
                )
            raise
        logger.info("schema_version=%s", schema_version)
    else:
        schema_version = get_schema_version(engine)
    session_factory = make_session_factory(engine)

    runtime = RuntimeContext(
        settings=settings,
        paths=paths,
        identity=identity,
        engine=engine,
        session_factory=session_factory,
        schema_version=schema_version,
        logger=logger,
    )
    if run_migrate and schema_version >= 7:
        from yancuo_win.application.search_service import (
            SearchIndexService,
            install_search_index_hooks,
        )

        install_search_index_hooks(session_factory)
        search_health = SearchIndexService(runtime).repair_if_needed()
        logger.info("search index: %s", search_health.summary)
    if run_migrate and schema_version >= 11:
        from yancuo_win.application.unified_search_service import (
            UnifiedSearchIndexService,
            install_unified_search_index_hooks,
        )

        install_unified_search_index_hooks(session_factory)
        note_count = UnifiedSearchIndexService(runtime).repair_notes_if_needed()
        logger.info("unified note search index: %s documents", note_count)
    if run_migrate and schema_version >= 9:
        from yancuo_win.application.note_intake_service import NoteIntakeService

        interrupted = NoteIntakeService(runtime).recover_interrupted_sessions()
        if interrupted:
            logger.warning("recovered %s interrupted note intake session(s)", interrupted)
    return runtime
