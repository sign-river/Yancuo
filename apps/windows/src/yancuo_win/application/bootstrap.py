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
from yancuo_win.data.migrate import migrate, verify_core_tables
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
    session_factory = make_session_factory(engine)

    schema_version = 0
    if run_migrate:
        schema_version = migrate(engine, target_version=settings.application.schema_version)
        missing = verify_core_tables(engine)
        if missing:
            raise RuntimeError(f"数据库缺少核心表：{', '.join(missing)}")
        logger.info("schema_version=%s", schema_version)
    else:
        from yancuo_win.data.migrate import get_schema_version

        schema_version = get_schema_version(engine)

    return RuntimeContext(
        settings=settings,
        paths=paths,
        identity=identity,
        engine=engine,
        session_factory=session_factory,
        schema_version=schema_version,
        logger=logger,
    )
