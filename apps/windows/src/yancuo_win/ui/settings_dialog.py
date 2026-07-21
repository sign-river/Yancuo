"""设置对话框：AI 密钥、云端提供商与令牌（密钥进系统凭据）。"""

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

from yancuo_win.ai.factory import get_provider
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
        self.resize(660, 640)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        s = runtime.settings
        form.addRow("语言", QLabel(s.application.language))
        form.addRow("数据根目录", QLabel(str(runtime.paths.root)))
        form.addRow("数据库", QLabel(str(runtime.paths.database)))
        layout.addLayout(form)

        # —— AI ——
        layout.addWidget(QLabel("—— AI（Faro / OpenAI 兼容）——"))
        ai_form = QFormLayout()
        self.ai_provider = QComboBox()
        self.ai_provider.addItem("Mock（本地假数据）", "mock")
        self.ai_provider.addItem("OpenAI 兼容（Faro 等）", "openai_compatible")
        idx = self.ai_provider.findData(s.ai.default_provider)
        self.ai_provider.setCurrentIndex(max(0, idx))
        ai_form.addRow("AI 提供商", self.ai_provider)

        self.ai_model = QLineEdit(s.ai.default_vision_model or "gpt-5.6-sol")
        ai_form.addRow("视觉模型 ID", self.ai_model)

        self._ai_cred_key = (
            (s.ai.providers.get("openai_compatible").credential_key if s.ai.providers.get("openai_compatible") else None)
            or "yancuo_ai_api_key"
        )
        self.ai_token_status = QLabel(mask_secret(get_secret(self._ai_cred_key)))
        ai_form.addRow("AI 密钥状态", self.ai_token_status)
        self.ai_token_edit = QLineEdit()
        self.ai_token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.ai_token_edit.setPlaceholderText("粘贴 Faro sk-faro-… 后点保存（不进 TOML/仓库）")
        ai_form.addRow("新 AI 密钥", self.ai_token_edit)

        ai_tok_row = QHBoxLayout()
        save_ai = QPushButton("保存 AI 密钥到系统凭据")
        save_ai.clicked.connect(self._save_ai_token)
        clear_ai = QPushButton("清除 AI 密钥")
        clear_ai.clicked.connect(self._clear_ai_token)
        apply_ai = QPushButton("应用 AI 到当前会话")
        apply_ai.clicked.connect(self._apply_ai_session)
        ai_tok_row.addWidget(save_ai)
        ai_tok_row.addWidget(clear_ai)
        ai_tok_row.addWidget(apply_ai)
        ai_form.addRow(ai_tok_row)
        layout.addLayout(ai_form)

        # —— 云端 ——
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
        cloud_form.addRow("云端提供商", self.provider)

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
        save_tok = QPushButton("保存云令牌到系统凭据")
        save_tok.clicked.connect(self._save_token)
        clear_tok = QPushButton("清除云令牌")
        clear_tok.clicked.connect(self._clear_token)
        test_btn = QPushButton("测试云连接")
        test_btn.clicked.connect(self._test_cloud)
        tok_row.addWidget(save_tok)
        tok_row.addWidget(clear_tok)
        tok_row.addWidget(test_btn)
        cloud_form.addRow(tok_row)
        layout.addLayout(cloud_form)

        tip = QLabel(
            "密钥只进操作系统凭据管理器；TOML 仅保存 credential_key / api_key_env 名称。\n"
            "AI：选「OpenAI 兼容」并保存 Faro Key 后点「应用 AI」。\n"
            "云：完整备份/迁移，不是每题实时同步。"
        )
        tip.setWordWrap(True)
        layout.addWidget(tip)

        open_btn = QPushButton("打开数据目录")
        open_btn.clicked.connect(self._open_data_root)
        layout.addWidget(open_btn)

        apply_btn = QPushButton("应用云端提供商到当前会话")
        apply_btn.clicked.connect(self._apply_session_provider)
        layout.addWidget(apply_btn)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).clicked.connect(self.accept)
        layout.addWidget(buttons)

        self._refresh_token_ui()

    def _ai_credential_key(self) -> str:
        cfg = self.runtime.settings.ai.providers.get("openai_compatible")
        return (cfg.credential_key if cfg else None) or "yancuo_ai_api_key"

    def _save_ai_token(self) -> None:
        key = self._ai_credential_key()
        token = self.ai_token_edit.text().strip()
        if not token:
            QMessageBox.warning(self, "提示", "请先粘贴 AI 密钥")
            return
        try:
            set_secret(key, token)
            self.ai_token_edit.clear()
            self.ai_token_status.setText(mask_secret(get_secret(key)))
            QMessageBox.information(self, "已保存", "AI 密钥已写入系统凭据，未写入配置文件。")
        except DomainError as exc:
            QMessageBox.warning(self, "失败", str(exc))

    def _clear_ai_token(self) -> None:
        delete_secret(self._ai_credential_key())
        self.ai_token_status.setText(mask_secret(None))
        QMessageBox.information(self, "已清除", "系统凭据中的 AI 密钥已删除。")

    def _apply_ai_session(self) -> None:
        name = self.ai_provider.currentData()
        self.runtime.settings.ai.default_provider = name
        self.runtime.settings.ai.enabled = True
        model = self.ai_model.text().strip()
        if model:
            self.runtime.settings.ai.default_vision_model = model
            self.runtime.settings.ai.default_text_model = model
        try:
            if name != "mock":
                provider = get_provider(self.runtime.settings, name)
                if hasattr(provider, "_api_key"):
                    provider._api_key()  # type: ignore[attr-defined]
            QMessageBox.information(
                self,
                "已应用",
                f"当前会话 AI：{name}\n模型：{self.runtime.settings.ai.default_vision_model}",
            )
        except DomainError as exc:
            QMessageBox.warning(self, "密钥未就绪", str(exc))

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
            QMessageBox.information(self, "已保存", "云令牌已写入系统凭据，未写入配置文件。")
        except DomainError as exc:
            QMessageBox.warning(self, "失败", str(exc))

    def _clear_token(self) -> None:
        key = self._credential_key_for_provider()
        if not key:
            return
        delete_secret(key)
        self.token_status.setText(mask_secret(None))
        QMessageBox.information(self, "已清除", "系统凭据中的云令牌已删除。")

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
        QMessageBox.information(self, "已应用", f"当前会话云端提供商：{name}")

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
