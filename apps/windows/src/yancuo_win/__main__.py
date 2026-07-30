"""应用入口：加载配置 → 初始化路径/日志/身份 → 迁移数据库 → 打开主窗口。"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys


def _run_packaging_smoke_test() -> int:
    """Verify bundled resources and startup without opening the desktop UI."""

    from yancuo_win.application.bootstrap import bootstrap_runtime
    from yancuo_win.config.settings import default_toml_path, resource_path
    from yancuo_win.domain.identity import SCHEMA_VERSION

    runtime = bootstrap_runtime()
    default_config = default_toml_path()
    problem_schema = resource_path("protocol", "schemas", "problem.schema.json")
    checks = {
        "schema_version": runtime.schema_version,
        "expected_schema_version": SCHEMA_VERSION,
        "database": str(runtime.paths.database),
        "data_root": str(runtime.paths.root),
        "default_config": str(default_config),
        "problem_schema": str(problem_schema) if problem_schema else None,
        "default_config_exists": default_config.is_file(),
        "problem_schema_exists": bool(problem_schema and problem_schema.is_file()),
    }
    passed = (
        runtime.schema_version == SCHEMA_VERSION
        and checks["default_config_exists"]
        and checks["problem_schema_exists"]
    )
    checks["passed"] = passed
    report_path = os.environ.get("YANCUO_PACKAGING_SMOKE_REPORT")
    if report_path:
        report = Path(report_path).expanduser().resolve()
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(
            json.dumps(checks, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    runtime.engine.dispose()
    return 0 if passed else 2


def main() -> int:
    if "--packaging-smoke-test" in sys.argv:
        return _run_packaging_smoke_test()

    from yancuo_win.application.bootstrap import bootstrap_runtime
    from yancuo_win.ui.main_window import MainWindow

    from PySide6.QtWidgets import QApplication, QMessageBox

    try:
        runtime = bootstrap_runtime()
    except Exception as exc:  # noqa: BLE001 — 启动失败需对用户可见
        app = QApplication(sys.argv)
        QMessageBox.critical(None, "研错库启动失败", str(exc))
        return 1

    app = QApplication(sys.argv)
    app.setApplicationName("研错库")
    app.setOrganizationName("Yancuo")

    from yancuo_win.ui.theme import apply_app_theme

    apply_app_theme(app, runtime.settings.application.theme)

    window = MainWindow(runtime)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
