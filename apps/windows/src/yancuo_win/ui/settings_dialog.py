"""设置对话框：AI 密钥、云端提供商与令牌（密钥进系统凭据）。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from yancuo_win.ai.factory import get_provider
from yancuo_win.application.bootstrap import RuntimeContext
from yancuo_win.cloud.factory import get_cloud_provider
from yancuo_win.config.settings import (
    ConfigError,
    save_ai_preferences,
    save_theme_preference,
)
from yancuo_win.domain.rules import DomainError
from yancuo_win.infrastructure.credentials import (
    delete_secret,
    get_secret,
    mask_secret,
    set_secret,
)
from yancuo_win.ui.widgets import CardFrame, button_row, primary_button
from yancuo_win.ui.theme import apply_app_theme, get_theme_manager


class SettingsDialog(QDialog):
    def __init__(self, runtime: RuntimeContext, parent=None) -> None:
        super().__init__(parent)
        self.runtime = runtime
        self.setWindowTitle("设置")
        self.resize(680, 720)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(12)

        title = QLabel("设置")
        title.setObjectName("PageTitle")
        outer.addWidget(title)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setSpacing(12)
        layout.setContentsMargins(0, 0, 4, 0)

        s = runtime.settings

        appearance = CardFrame()
        appearance.add_title("外观")
        appearance.add_hint("可跟随 Windows，也可固定使用浅色或深色；保存后立即应用。")
        appearance_form = QFormLayout()
        self.theme_mode = QComboBox()
        self.theme_mode.addItem("跟随系统", "system")
        self.theme_mode.addItem("浅色", "light")
        self.theme_mode.addItem("深色", "dark")
        theme_index = self.theme_mode.findData(s.application.theme)
        self.theme_mode.setCurrentIndex(max(0, theme_index))
        appearance_form.addRow("主题", self.theme_mode)
        self.theme_status = QLabel("")
        self.theme_status.setObjectName("MutedLabel")
        appearance_form.addRow("当前状态", self.theme_status)
        appearance.body.addLayout(appearance_form)
        apply_theme = primary_button("保存并应用外观")
        apply_theme.clicked.connect(self._apply_theme)
        appearance.body.addLayout(button_row(apply_theme))
        layout.addWidget(appearance)
        self._refresh_theme_status()

        info = CardFrame()
        info.add_title("本机")
        info_form = QFormLayout()
        info_form.addRow("语言", QLabel(s.application.language))
        path_lbl = QLabel(str(runtime.paths.root))
        path_lbl.setWordWrap(True)
        info_form.addRow("数据根目录", path_lbl)
        info_form.addRow("数据库", QLabel(str(runtime.paths.database)))
        info.body.addLayout(info_form)
        open_btn = QPushButton("打开数据目录")
        open_btn.clicked.connect(self._open_data_root)
        info.body.addLayout(button_row(open_btn))
        layout.addWidget(info)

        # —— AI ——
        ai_card = CardFrame()
        ai_card.add_title("AI（Faro / OpenAI 兼容）")
        ai_card.add_hint(
            "默认直连 Faro API。密钥只进系统凭据；模型 ID 请从 Faro 模型广场复制，并确认支持图片输入。"
        )
        ai_form = QFormLayout()
        self.ai_provider = QComboBox()
        self.ai_provider.addItem("Faro API（真实识图）", "openai_compatible")
        self.ai_provider.addItem("Mock（离线测试数据）", "mock")
        idx = self.ai_provider.findData(s.ai.default_provider)
        self.ai_provider.setCurrentIndex(max(0, idx))
        ai_form.addRow("AI 提供商", self.ai_provider)

        faro_cfg = s.ai.providers.get("openai_compatible")
        ai_form.addRow(
            "API 地址",
            QLabel((faro_cfg.base_url if faro_cfg else "") or "https://faroapi.com/v1"),
        )

        self.ai_model = QLineEdit(s.ai.default_vision_model or "gpt-5.6-sol")
        self.ai_model.setPlaceholderText("从 Faro 模型广场复制支持图片的模型 ID")
        ai_form.addRow("图片模型 ID", self.ai_model)

        self._ai_cred_key = (
            (
                s.ai.providers.get("openai_compatible").credential_key
                if s.ai.providers.get("openai_compatible")
                else None
            )
            or "yancuo_ai_api_key"
        )
        self.ai_token_status = QLabel(mask_secret(get_secret(self._ai_cred_key)))
        ai_form.addRow("AI 密钥状态", self.ai_token_status)
        self.ai_token_edit = QLineEdit()
        self.ai_token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.ai_token_edit.setPlaceholderText("粘贴 Faro sk-faro-… 后点保存")
        ai_form.addRow("新 AI 密钥", self.ai_token_edit)
        ai_card.body.addLayout(ai_form)

        save_ai = primary_button("保存 AI 密钥")
        save_ai.clicked.connect(self._save_ai_token)
        clear_ai = QPushButton("清除 AI 密钥")
        clear_ai.clicked.connect(self._clear_ai_token)
        test_ai = QPushButton("测试 Faro 连接")
        test_ai.clicked.connect(self._test_ai_connection)
        apply_ai = QPushButton("保存并应用 AI 设置")
        apply_ai.clicked.connect(self._apply_ai_session)
        ai_card.body.addLayout(button_row(save_ai, clear_ai, test_ai))
        ai_card.body.addLayout(button_row(apply_ai))
        layout.addWidget(ai_card)

        # —— 云端 ——
        cloud_card = CardFrame()
        cloud_card.add_title("云端备份（非实时同步）")
        cloud_card.add_hint("完整备份/迁移；令牌不写入 TOML。")
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
        self.token_edit.setPlaceholderText("粘贴新令牌后点保存")
        cloud_form.addRow("新令牌", self.token_edit)
        cloud_card.body.addLayout(cloud_form)

        save_tok = primary_button("保存云令牌")
        save_tok.clicked.connect(self._save_token)
        clear_tok = QPushButton("清除云令牌")
        clear_tok.clicked.connect(self._clear_token)
        test_btn = QPushButton("测试云连接")
        test_btn.clicked.connect(self._test_cloud)
        apply_btn = QPushButton("应用云端到当前会话")
        apply_btn.clicked.connect(self._apply_session_provider)
        cloud_card.body.addLayout(button_row(save_tok, clear_tok, test_btn))
        cloud_card.body.addLayout(button_row(apply_btn))
        layout.addWidget(cloud_card)

        tip = QLabel(
            "密钥只进操作系统凭据管理器；TOML 仅保存 credential_key / api_key_env 名称。"
        )
        tip.setObjectName("MutedLabel")
        tip.setWordWrap(True)
        layout.addWidget(tip)
        layout.addStretch(1)

        scroll.setWidget(body)
        outer.addWidget(scroll, stretch=1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).clicked.connect(self.accept)
        outer.addWidget(buttons)

        self._refresh_token_ui()

    def _refresh_theme_status(self) -> None:
        app = QApplication.instance()
        manager = get_theme_manager(app)
        resolved = manager.resolved if manager else "light"
        selected = str(self.theme_mode.currentData())
        label = {"light": "浅色", "dark": "深色"}.get(resolved, resolved)
        if selected == "system":
            self.theme_status.setText(f"跟随系统（当前为{label}）")
        else:
            self.theme_status.setText(f"当前为{label}")

    def _apply_theme(self) -> None:
        mode = str(self.theme_mode.currentData())
        try:
            save_theme_preference(self.runtime.paths.root, mode)
            self.runtime.settings.application.theme = mode
            app = QApplication.instance()
            if app is not None:
                manager = get_theme_manager(app)
                if manager is None:
                    apply_app_theme(app, mode)
                else:
                    manager.set_mode(mode)
            self._refresh_theme_status()
        except (ConfigError, ValueError) as exc:
            QMessageBox.warning(self, "外观设置未保存", str(exc))

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
        model = self.ai_model.text().strip()
        if not model:
            QMessageBox.warning(self, "模型未设置", "请填写 Faro 模型广场中的图片模型 ID")
            return
        try:
            provider = get_provider(self.runtime.settings, name)
            provider.validate_configuration()
            save_ai_preferences(
                self.runtime.paths.root,
                provider=name,
                model=model,
                enabled=True,
            )
            self.runtime.settings.ai.default_provider = name
            self.runtime.settings.ai.enabled = True
            self.runtime.settings.ai.default_vision_model = model
            self.runtime.settings.ai.default_text_model = model
            QMessageBox.information(
                self,
                "已保存并应用",
                f"AI 提供商：{'Faro API' if name == 'openai_compatible' else 'Mock'}\n"
                f"图片模型：{model}\n下次启动仍会保留此选择。",
            )
        except (ConfigError, DomainError) as exc:
            QMessageBox.warning(self, "AI 设置未就绪", str(exc))

    def _test_ai_connection(self) -> None:
        name = self.ai_provider.currentData()
        if name == "mock":
            QMessageBox.information(self, "Mock", "Mock 不访问网络，请选择 Faro API。")
            return
        model = self.ai_model.text().strip()
        try:
            provider = get_provider(self.runtime.settings, name)
            provider.validate_configuration()
            list_models = getattr(provider, "list_models", None)
            if not callable(list_models):
                raise DomainError("当前提供商不支持连接测试")
            models = list_models(timeout_seconds=20)
        except DomainError as exc:
            QMessageBox.warning(self, "Faro 连接失败", str(exc))
            return

        if model and model not in models:
            sample = "、".join(models[:8]) or "（服务未返回模型）"
            QMessageBox.warning(
                self,
                "连接成功，但模型未找到",
                f"Faro 身份验证成功，但模型列表中没有“{model}”。\n"
                f"请从模型广场重新复制 ID。当前返回示例：{sample}",
            )
            return
        QMessageBox.information(
            self,
            "Faro 连接成功",
            f"已通过 Faro 身份验证，并在模型列表中找到“{model}”。",
        )

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
