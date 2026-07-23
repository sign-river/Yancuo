"""应用启动编排。"""

from __future__ import annotations

from dataclasses import dataclass
import logging

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
from yancuo_win.domain.identity import LocalIdentity, load_or_create_identity
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

    engine = make_engine(paths.database)
    schema_version = 0
    if run_migrate:
        target_version = settings.application.schema_version
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
    return runtime
