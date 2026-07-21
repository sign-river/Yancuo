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
        from yancuo_win.data.migrate import migrate, verify_core_tables

        version = migrate(runtime.engine, target_version=args.target)
        missing = verify_core_tables(runtime.engine)
        if missing:
            print(f"迁移后仍缺少表：{', '.join(missing)}", file=sys.stderr)
            return 2
        print(f"OK schema_version={version} database={runtime.paths.database}")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"迁移失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
