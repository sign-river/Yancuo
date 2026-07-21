"""设置对话框（路径只读展示 + 打开数据目录）。"""

from __future__ import annotations

import os
import subprocess
import sys

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from yancuo_win.application.bootstrap import RuntimeContext


class SettingsDialog(QDialog):
    def __init__(self, runtime: RuntimeContext, parent=None) -> None:
        super().__init__(parent)
        self.runtime = runtime
        self.setWindowTitle("设置")
        self.resize(560, 360)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        s = runtime.settings
        form.addRow("语言", QLabel(s.application.language))
        form.addRow("主题", QLabel(s.application.theme))
        form.addRow("自动保存(秒)", QLabel(str(s.application.auto_save_seconds)))
        form.addRow("数据根目录", QLabel(str(runtime.paths.root)))
        form.addRow("数据库", QLabel(str(runtime.paths.database)))
        form.addRow("资源目录", QLabel(str(runtime.paths.asset_dir)))
        form.addRow("备份目录", QLabel(str(runtime.paths.backup_dir)))
        form.addRow("配置文件", QLabel(os.environ.get("YANCUO_CONFIG_FILE", "(默认 config/default.toml)")))
        form.addRow("AI", QLabel("已关闭（阶段 B）" if not s.ai.enabled else "开启"))
        form.addRow("云端", QLabel("已关闭（阶段 B）" if not s.cloud.enabled else "开启"))
        layout.addLayout(form)

        tip = QLabel(
            "修改路径请设置环境变量 YANCUO_DATA_ROOT / YANCUO_CONFIG_FILE 后重启。\n"
            "API 密钥不得写入配置文件，仅使用环境变量名引用。"
        )
        tip.setWordWrap(True)
        layout.addWidget(tip)

        open_btn = QPushButton("打开数据目录")
        open_btn.clicked.connect(self._open_data_root)
        layout.addWidget(open_btn)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        buttons.button(QDialogButtonBox.StandardButton.Close).clicked.connect(self.accept)
        layout.addWidget(buttons)

    def _open_data_root(self) -> None:
        path = self.runtime.paths.root
        try:
            if sys.platform.startswith("win"):
                os.startfile(path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.run(["open", str(path)], check=False)
            else:
                subprocess.run(["xdg-open", str(path)], check=False)
        except OSError as exc:
            QMessageBox.warning(self, "无法打开", str(exc))
