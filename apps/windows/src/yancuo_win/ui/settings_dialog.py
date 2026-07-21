"""设置对话框：路径、云端提供商与令牌（令牌进系统凭据）。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from yancuo_win.application.bootstrap import RuntimeContext
from yancuo_win.cloud.factory import get_cloud_provider
from yancuo_win.domain.rules import DomainError
from yancuo_win.infrastructure.credentials import (
    delete_secret,
    get_secret,
    mask_secret,
    set_secret,
)


class SettingsDialog(QDialog):
    def __init__(self, runtime: RuntimeContext, parent=None) -> None:
        super().__init__(parent)
        self.runtime = runtime
        self.setWindowTitle("设置")
        self.resize(640, 560)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        s = runtime.settings
        form.addRow("语言", QLabel(s.application.language))
        form.addRow("数据根目录", QLabel(str(runtime.paths.root)))
        form.addRow("数据库", QLabel(str(runtime.paths.database)))
        form.addRow("AI", QLabel("开启" if s.ai.enabled else "关闭"))

        layout.addWidget(QLabel("—— 云端备份（非实时同步）——"))
        cloud_form = QFormLayout()
        self.provider = QComboBox()
        self.provider.addItem("本地文件夹（推荐先测通）", "local_folder")
        self.provider.addItem("GitLink", "gitlink")
        self.provider.addItem("GitHub", "github")
        idx = self.provider.findData(s.cloud.default_provider)
        if idx < 0:
            idx = self.provider.findData("local_folder")
        self.provider.setCurrentIndex(max(0, idx))
        self.provider.currentIndexChanged.connect(self._on_provider_changed)
        cloud_form.addRow("默认提供商", self.provider)

        self.owner_edit = QLineEdit(s.cloud.repository.owner)
        self.repo_edit = QLineEdit(s.cloud.repository.name)
        cloud_form.addRow("仓库 owner", self.owner_edit)
        cloud_form.addRow("仓库 name", self.repo_edit)

        self.local_root = QLineEdit(_default_local_root(runtime))
        cloud_form.addRow("本地云目录", self.local_root)
        browse = QPushButton("浏览…")
        browse.clicked.connect(self._browse_local)
        cloud_form.addRow("", browse)

        self.token_label = QLabel("令牌")
        self.token_status = QLabel("")
        cloud_form.addRow(self.token_label, self.token_status)
        self.token_edit = QLineEdit()
        self.token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.token_edit.setPlaceholderText("粘贴新令牌后点保存（不会写入仓库/TOML）")
        cloud_form.addRow("新令牌", self.token_edit)

        tok_row = QHBoxLayout()
        save_tok = QPushButton("保存令牌到系统凭据")
        save_tok.clicked.connect(self._save_token)
        clear_tok = QPushButton("清除令牌")
        clear_tok.clicked.connect(self._clear_token)
        test_btn = QPushButton("测试连接")
        test_btn.clicked.connect(self._test_cloud)
        tok_row.addWidget(save_tok)
        tok_row.addWidget(clear_tok)
        tok_row.addWidget(test_btn)
        cloud_form.addRow(tok_row)

        layout.addLayout(form)
        layout.addLayout(cloud_form)

        tip = QLabel(
            "API 密钥/令牌只保存在操作系统凭据管理器，配置文件仅保存 credential_key 名称。\n"
            "云功能是完整备份与迁移，不是每题实时同步。\n"
            "GitLink：Bearer + 先附件后 Release；GitHub：PAT + 先建 Release 再传 asset。\n"
            "切换提供商后点「应用提供商到当前会话」。"
        )
        tip.setWordWrap(True)
        layout.addWidget(tip)

        open_btn = QPushButton("打开数据目录")
        open_btn.clicked.connect(self._open_data_root)
        layout.addWidget(open_btn)

        apply_btn = QPushButton("应用提供商到当前会话")
        apply_btn.clicked.connect(self._apply_session_provider)
        layout.addWidget(apply_btn)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).clicked.connect(self.accept)
        layout.addWidget(buttons)

        self._refresh_token_ui()

    def _credential_key_for_provider(self) -> str | None:
        name = self.provider.currentData()
        s = self.runtime.settings
        if name == "gitlink":
            return s.cloud.gitlink.credential_key or "yancuo_gitlink_token"
        if name == "github":
            return s.cloud.github.credential_key or "yancuo_github_token"
        return None

    def _refresh_token_ui(self) -> None:
        name = self.provider.currentData()
        key = self._credential_key_for_provider()
        if name == "gitlink":
            self.token_label.setText("GitLink 令牌")
            self.token_status.setText(mask_secret(get_secret(key) if key else None))
            self.token_edit.setEnabled(True)
        elif name == "github":
            self.token_label.setText("GitHub PAT")
            self.token_status.setText(mask_secret(get_secret(key) if key else None))
            self.token_edit.setEnabled(True)
        else:
            self.token_label.setText("令牌（本地文件夹无需）")
            self.token_status.setText("—")
            self.token_edit.setEnabled(False)

    def _on_provider_changed(self) -> None:
        self._refresh_token_ui()

    def _browse_local(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择本地云同步目录")
        if path:
            self.local_root.setText(path)

    def _save_token(self) -> None:
        key = self._credential_key_for_provider()
        if not key:
            QMessageBox.information(self, "提示", "当前提供商不需要令牌")
            return
        token = self.token_edit.text().strip()
        if not token:
            QMessageBox.warning(self, "提示", "请先粘贴令牌")
            return
        try:
            set_secret(key, token)
            self.token_edit.clear()
            self.token_status.setText(mask_secret(get_secret(key)))
            QMessageBox.information(self, "已保存", "令牌已写入系统凭据，未写入配置文件。")
        except DomainError as exc:
            QMessageBox.warning(self, "失败", str(exc))

    def _clear_token(self) -> None:
        key = self._credential_key_for_provider()
        if not key:
            return
        delete_secret(key)
        self.token_status.setText(mask_secret(None))
        QMessageBox.information(self, "已清除", "系统凭据中的令牌已删除。")

    def _apply_session_provider(self) -> None:
        name = self.provider.currentData()
        self.runtime.settings.cloud.default_provider = name
        self.runtime.settings.cloud.repository.owner = self.owner_edit.text().strip()
        self.runtime.settings.cloud.repository.name = (
            self.repo_edit.text().strip() or "graduate-mistake-book-data"
        )
        self.runtime.settings.cloud.enabled = True
        if name == "local_folder":
            os.environ["YANCUO_CLOUD_LOCAL_ROOT"] = self.local_root.text().strip()
        QMessageBox.information(self, "已应用", f"当前会话提供商：{name}")

    def _test_cloud(self) -> None:
        self._apply_session_provider()
        try:
            root = (
                Path(self.local_root.text().strip())
                if self.provider.currentData() == "local_folder"
                else None
            )
            provider = get_cloud_provider(self.runtime.settings, local_root=root)
            result = provider.test_connection()
            QMessageBox.information(
                self,
                "连接成功",
                json.dumps(result, ensure_ascii=False, indent=2),
            )
        except DomainError as exc:
            QMessageBox.warning(self, "连接失败", str(exc))

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


def _default_local_root(runtime: RuntimeContext) -> str:
    env = os.environ.get("YANCUO_CLOUD_LOCAL_ROOT")
    if env:
        return env
    return str(runtime.paths.backup_dir / "cloud_local")
