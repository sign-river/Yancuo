"""应用入口：加载配置 → 初始化路径/日志/身份 → 迁移数据库 → 打开主窗口。"""

from __future__ import annotations

import sys


def main() -> int:
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

    apply_app_theme(app)

    window = MainWindow(runtime)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
