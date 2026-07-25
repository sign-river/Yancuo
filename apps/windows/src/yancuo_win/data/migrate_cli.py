"""命令行迁移入口：`yancuo-migrate` / `python -m yancuo_win.data.migrate_cli`。"""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    from yancuo_win.application.bootstrap import bootstrap_runtime

    parser = argparse.ArgumentParser(description="研错库数据库迁移")
    parser.add_argument(
        "--target",
        type=int,
        default=None,
        help="目标 schema_version（默认使用程序内置版本）",
    )
    args = parser.parse_args(argv)

    try:
        runtime = bootstrap_runtime(run_migrate=False)
        from yancuo_win.data.migrate import (
            create_pre_migration_backup,
            ensure_search_index_schema,
            get_schema_version,
            migrate,
            restore_pre_migration_backup,
            verify_core_tables,
        )
        from yancuo_win.domain.identity import SCHEMA_VERSION

        target = args.target if args.target is not None else SCHEMA_VERSION
        current = get_schema_version(runtime.engine)
        backup_path = None
        if 0 < current < target:
            backup_path = create_pre_migration_backup(
                runtime.paths.database,
                runtime.paths.backup_dir,
                from_version=current,
                target_version=target,
            )
        try:
            version = migrate(runtime.engine, target_version=target)
            if version >= 7:
                ensure_search_index_schema(runtime.engine)
            missing = verify_core_tables(runtime.engine)
            if missing:
                raise RuntimeError(f"迁移后仍缺少表：{', '.join(missing)}")
        except Exception:
            if backup_path is not None:
                runtime.engine.dispose()
                restore_pre_migration_backup(
                    backup_path,
                    runtime.paths.database,
                    expected_schema_version=current,
                )
            raise
        print(f"OK schema_version={version} database={runtime.paths.database}")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"迁移失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
