"""设置对话框：AI 密钥、云端提供商与令牌（密钥进系统凭据）。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QDateTime, Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from yancuo_win.ai.factory import get_provider
from yancuo_win.application.bootstrap import RuntimeContext
from yancuo_win.cloud.factory import get_cloud_provider
from yancuo_win.config.settings import (
    ConfigError,
    save_ai_preferences,
    save_cloud_preferences,
    save_preview_zoom_preference,
    save_theme_preference,
)
from yancuo_win.domain.rules import DomainError
from yancuo_win.infrastructure.credentials import (
    delete_secret,
    get_secret,
    mask_secret,
    set_secret,
)
from yancuo_win.tasks.model_worker import AIModelListWorker
from yancuo_win.tasks.worker import CallableWorker
from yancuo_win.ui.widgets import (
    CardFrame,
    PageHeader,
    StateNotice,
    button_row,
    danger_button,
    describe_field,
    primary_button,
    set_tab_order_chain,
)
from yancuo_win.ui.theme import apply_app_theme, current_theme_name, get_theme_manager
from yancuo_win.ui.math_content import set_preview_zoom_scale


class ServiceSettingsPage(QWidget):
    """One focused settings page for AI, appearance, or cloud configuration."""

    status_message = Signal(str)

    _SECTIONS = {
        "ai": ("AI 服务", "配置 AI 提供商、模型与凭据。"),
        "appearance": ("外观与显示", "调整应用主题与界面呈现方式。"),
        "cloud": ("云端同步", "配置用于备份和同步的云端提供商与凭据。"),
    }

    @staticmethod
    def _settings_form() -> QFormLayout:
        form = QFormLayout()
        form.setLabelAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
        )
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(10)
        return form

    @staticmethod
    def _save_row(button: QPushButton) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 2, 0, 0)
        row.addStretch(1)
        row.addWidget(button)
        return row

    def __init__(self, runtime: RuntimeContext, section: str, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("SettingsPage")
        if section not in self._SECTIONS:
            raise ValueError(f"unsupported settings section: {section}")
        self.runtime = runtime
        self.section = section
        self._ai_model_worker: AIModelListWorker | None = None
        self._connection_worker: CallableWorker | None = None
        self._dirty = False
        self._last_connection_test = "尚未测试"
        self._field_errors: dict[str, QLabel] = {}
        self._save_button: QPushButton | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 20, 24, 20)
        outer.setSpacing(12)

        title, hint = self._SECTIONS[section]
        outer.addWidget(PageHeader(title, hint))

        scroll = QScrollArea()
        scroll.setObjectName("SettingsScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        body = QWidget()
        body.setObjectName("SettingsContent")
        body.setMaximumWidth(800)
        self.settings_content = body
        layout = QVBoxLayout(body)
        layout.setSpacing(14)
        layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout = layout

        s = runtime.settings

        if section == "appearance":
            appearance = CardFrame()
            appearance.setProperty("surfaceRole", "settings")
            appearance.add_title("主题")
            appearance.add_hint("可跟随 Windows，也可固定使用浅色或深色；保存后立即应用。")
            appearance_form = self._settings_form()
            self.theme_mode = QComboBox()
            describe_field(self.theme_mode, "主题模式")
            self.theme_mode.addItem("跟随系统", "system")
            self.theme_mode.addItem("浅色", "light")
            self.theme_mode.addItem("深色", "dark")
            theme_index = self.theme_mode.findData(s.application.theme)
            self.theme_mode.setCurrentIndex(max(0, theme_index))
            self.theme_mode.setVisible(False)
            theme_choices = QWidget()
            theme_row = QHBoxLayout(theme_choices)
            theme_row.setContentsMargins(0, 0, 0, 0)
            theme_row.setSpacing(6)
            self.theme_button_group = QButtonGroup(self)
            self.theme_buttons: dict[str, QPushButton] = {}
            for label, mode in (("跟随系统", "system"), ("浅色", "light"), ("深色", "dark")):
                button = QPushButton(label)
                button.setObjectName("ThemeModeButton")
                button.setCheckable(True)
                button.setProperty("themeMode", mode)
                button.setProperty("themeSelected", False)
                button.setAccessibleDescription("选择此主题模式")
                self.theme_button_group.addButton(button)
                self.theme_buttons[mode] = button
                theme_row.addWidget(button)
            self.theme_status = QLabel()
            self.theme_status.setObjectName("ThemeModeStatus")
            self.theme_status.setAccessibleName("当前生效主题")
            self.theme_button_group.idClicked.connect(self._select_theme_button)
            theme_row.addStretch(1)
            appearance_form.addRow("主题", theme_choices)
            appearance_form.addRow("当前显示", self.theme_status)
            self.preview_zoom = QSpinBox()
            describe_field(
                self.preview_zoom,
                "预览缩放",
                "调整题目、答案和解析等阅读预览的缩放比例",
            )
            self.preview_zoom.setRange(80, 150)
            self.preview_zoom.setSingleStep(1)
            self.preview_zoom.setSuffix("%")
            self.preview_zoom.setValue(round(s.application.preview_zoom_scale * 100))
            self.preview_zoom.valueChanged.connect(
                lambda value: set_preview_zoom_scale(value / 100)
            )
            zoom_control = QWidget()
            zoom_row = QHBoxLayout(zoom_control)
            zoom_row.setContentsMargins(0, 0, 0, 0)
            zoom_row.setSpacing(6)
            zoom_down = QPushButton("−")
            zoom_down.setAccessibleName("减小预览缩放")
            zoom_down.clicked.connect(lambda: self.preview_zoom.stepDown())
            zoom_up = QPushButton("+")
            zoom_up.setAccessibleName("增大预览缩放")
            zoom_up.clicked.connect(lambda: self.preview_zoom.stepUp())
            zoom_reset = QPushButton("恢复默认")
            zoom_reset.clicked.connect(lambda: self.preview_zoom.setValue(100))
            zoom_row.addWidget(zoom_down)
            zoom_row.addWidget(self.preview_zoom)
            zoom_row.addWidget(zoom_up)
            zoom_row.addWidget(zoom_reset)
            zoom_row.addStretch(1)
            appearance_form.addRow("所有预览缩放", zoom_control)
            appearance.body.addLayout(appearance_form)
            self.apply_theme_button = primary_button("保存更改")
            self.apply_theme_button.clicked.connect(self._apply_theme)
            self._save_button = self.apply_theme_button
            appearance.body.addLayout(self._save_row(self.apply_theme_button))
            self._refresh_theme_status()
            set_tab_order_chain(
                self.theme_buttons["system"],
                self.theme_buttons["light"],
                self.theme_buttons["dark"],
                self.preview_zoom,
                self.apply_theme_button,
            )
            layout.addWidget(appearance)

        # —— AI ——
        ai_card = CardFrame()
        ai_card.setProperty("surfaceRole", "settings")
        ai_card.add_title("服务商配置")
        ai_card.add_hint(
            "默认直连 Faro API。密钥只进系统凭据；可从 API 获取模型列表，"
            "也可手动输入模型 ID，并请确认支持图片输入。"
        )
        ai_form = self._settings_form()
        self.ai_provider = QComboBox()
        describe_field(self.ai_provider, "AI 提供商")
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

        self.ai_model = QComboBox()
        describe_field(
            self.ai_model,
            "图片模型 ID",
            "可以从 API 获取可用模型，也可以手动输入",
        )
        self.ai_model.setEditable(True)
        self.ai_model.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        current_model = s.ai.default_vision_model or "gpt-5.6-sol"
        self.ai_model.addItem(current_model)
        self.ai_model.setCurrentText(current_model)
        self.ai_model.lineEdit().setPlaceholderText(
            "从 API 获取或手动输入支持图片的模型 ID"
        )
        self.fetch_ai_models = QPushButton("刷新模型")
        self.fetch_ai_models.clicked.connect(self._fetch_ai_models)
        ai_model_control = QWidget()
        ai_model_row = QHBoxLayout(ai_model_control)
        ai_model_row.setContentsMargins(0, 0, 0, 0)
        ai_model_row.setSpacing(6)
        ai_model_row.addWidget(self.ai_model, stretch=1)
        ai_model_row.addWidget(self.fetch_ai_models)
        ai_form.addRow("图片模型 ID", ai_model_control)
        self._add_field_error(ai_form, "ai_model")
        self.ai_model_status = StateNotice(
            "可从 API 获取账户可用模型；请自行确认支持图片输入。",
            "info",
        )
        self.ai_model_status.setObjectName("CompactStateNotice")
        self.ai_model_status.layout().setContentsMargins(8, 4, 8, 4)
        ai_form.addRow("", self.ai_model_status)

        self._ai_cred_key = (
            (
                s.ai.providers.get("openai_compatible").credential_key
                if s.ai.providers.get("openai_compatible")
                else None
            )
            or "yancuo_ai_api_key"
        )
        self.ai_token_status = QLabel(mask_secret(get_secret(self._ai_cred_key)))
        self.clear_ai_button = danger_button("清除 AI 密钥")
        self.clear_ai_button.clicked.connect(self._clear_ai_token)
        ai_token_status_control = QWidget()
        ai_token_status_row = QHBoxLayout(ai_token_status_control)
        ai_token_status_row.setContentsMargins(0, 0, 0, 0)
        ai_token_status_row.setSpacing(6)
        ai_token_status_row.addWidget(self.ai_token_status, stretch=1)
        ai_token_status_row.addWidget(self.clear_ai_button)
        ai_form.addRow("密钥配置", ai_token_status_control)
        self.ai_token_edit = QLineEdit()
        describe_field(self.ai_token_edit, "新 AI 密钥")
        self.ai_token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.ai_token_edit.setPlaceholderText("粘贴 Faro sk-faro-… 后点保存")
        ai_token_control = QWidget()
        ai_token_row = QHBoxLayout(ai_token_control)
        ai_token_row.setContentsMargins(0, 0, 0, 0)
        ai_token_row.setSpacing(6)
        self.ai_token_visibility_button = QPushButton("显示")
        self.ai_token_visibility_button.setAccessibleName("显示或隐藏 AI 密钥")
        self.ai_token_visibility_button.clicked.connect(
            lambda: self._toggle_secret_visibility(
                self.ai_token_edit, self.ai_token_visibility_button
            )
        )
        ai_token_row.addWidget(self.ai_token_edit, stretch=1)
        ai_token_row.addWidget(self.ai_token_visibility_button)
        ai_form.addRow("新 AI 密钥", ai_token_control)
        self._add_field_error(ai_form, "ai_token")
        ai_card.body.addLayout(ai_form)

        self.test_ai_button = QPushButton("测试 Faro 连接")
        self.test_ai_button.clicked.connect(self._test_ai_connection)
        self.apply_ai_button = primary_button("保存更改")
        self.apply_ai_button.clicked.connect(self._apply_ai_session)
        if section == "ai":
            self._save_button = self.apply_ai_button
        self.ai_connection_notice = StateNotice("尚未测试连接。", "disabled")
        self.ai_connection_notice.setObjectName("CompactStateNotice")
        self.ai_connection_notice.layout().setContentsMargins(8, 4, 8, 4)
        ai_connection_row = QHBoxLayout()
        ai_connection_row.setSpacing(8)
        ai_connection_row.addWidget(self.ai_connection_notice, stretch=1)
        ai_connection_row.addWidget(self.test_ai_button)
        ai_card.body.addLayout(ai_connection_row)
        ai_card.body.addLayout(self._save_row(self.apply_ai_button))
        set_tab_order_chain(
            self.ai_provider,
            self.ai_model,
            self.ai_token_edit,
            self.clear_ai_button,
            self.fetch_ai_models,
            self.test_ai_button,
            self.apply_ai_button,
        )
        if section == "ai":
            layout.addWidget(ai_card)

        # —— 云端 ——
        cloud_card = CardFrame()
        cloud_card.setProperty("surfaceRole", "settings")
        cloud_card.add_title("连接设置")
        cloud_card.add_hint("完整备份/迁移；令牌不写入 TOML。")
        cloud_form = self._settings_form()
        self.cloud_form = cloud_form
        self.provider = QComboBox()
        describe_field(self.provider, "云端提供商")
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
        self.branch_edit = QLineEdit(s.cloud.repository.branch)
        describe_field(self.owner_edit, "仓库 owner")
        describe_field(self.repo_edit, "仓库 name")
        describe_field(self.branch_edit, "同步分支")
        cloud_form.addRow("仓库 owner", self.owner_edit)
        self._add_field_error(cloud_form, "cloud_owner")
        cloud_form.addRow("仓库 name", self.repo_edit)
        self._add_field_error(cloud_form, "cloud_repo")
        cloud_form.addRow("同步分支", self.branch_edit)

        self.local_root = QLineEdit(_default_local_root(runtime))
        describe_field(self.local_root, "本地云目录")
        self.browse_local_button = QPushButton("浏览…")
        self.browse_local_button.clicked.connect(self._browse_local)
        local_root_control = QWidget()
        local_root_row = QHBoxLayout(local_root_control)
        local_root_row.setContentsMargins(0, 0, 0, 0)
        local_root_row.setSpacing(6)
        local_root_row.addWidget(self.local_root, stretch=1)
        local_root_row.addWidget(self.browse_local_button)
        cloud_form.addRow("本地云目录", local_root_control)
        self._add_field_error(cloud_form, "cloud_root")

        self.token_label = QLabel("令牌")
        self.token_status = QLabel("")
        cloud_form.addRow(self.token_label, self.token_status)
        self.token_edit = QLineEdit()
        describe_field(self.token_edit, "新云端令牌")
        self.token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.token_edit.setPlaceholderText("粘贴新令牌后点保存")
        self.token_control = QWidget()
        token_row = QHBoxLayout(self.token_control)
        token_row.setContentsMargins(0, 0, 0, 0)
        token_row.setSpacing(6)
        self.token_visibility_button = QPushButton("显示")
        self.token_visibility_button.setAccessibleName("显示或隐藏云端令牌")
        self.token_visibility_button.clicked.connect(
            lambda: self._toggle_secret_visibility(
                self.token_edit, self.token_visibility_button
            )
        )
        token_row.addWidget(self.token_edit, stretch=1)
        token_row.addWidget(self.token_visibility_button)
        cloud_form.addRow("新令牌", self.token_control)
        self._add_field_error(cloud_form, "cloud_token")
        self.cloud_permission_notice = StateNotice()
        cloud_form.addRow("", self.cloud_permission_notice)
        cloud_card.body.addLayout(cloud_form)

        self.clear_cloud_token_button = danger_button("清除云令牌")
        self.clear_cloud_token_button.clicked.connect(self._clear_token)
        self.test_cloud_button = QPushButton("测试云连接")
        self.test_cloud_button.clicked.connect(self._test_cloud)
        self.apply_cloud_button = primary_button("保存更改")
        self.apply_cloud_button.clicked.connect(self._save_cloud_settings)
        if section == "cloud":
            self._save_button = self.apply_cloud_button
        cloud_actions = QHBoxLayout()
        cloud_actions.setSpacing(8)
        cloud_actions.addWidget(self.clear_cloud_token_button)
        cloud_actions.addWidget(self.test_cloud_button)
        cloud_actions.addStretch(1)
        cloud_actions.addWidget(self.apply_cloud_button)
        cloud_card.body.addLayout(cloud_actions)
        set_tab_order_chain(
            self.provider,
            self.owner_edit,
            self.repo_edit,
            self.branch_edit,
            self.local_root,
            self.browse_local_button,
            self.token_edit,
            self.clear_cloud_token_button,
            self.test_cloud_button,
            self.apply_cloud_button,
        )
        if section == "cloud":
            layout.addWidget(cloud_card)

        if section in {"ai", "cloud"}:
            tip = QLabel(
                "密钥只进操作系统凭据管理器；TOML 仅保存 credential_key / api_key_env 名称。"
            )
            tip.setObjectName("MutedLabel")
            tip.setWordWrap(True)
            layout.addWidget(tip)
        layout.addStretch(1)

        scroll.setWidget(body)
        scroll.setAlignment(
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop
        )
        outer.addWidget(scroll, stretch=1)

        if section == "cloud":
            self._refresh_token_ui()

        tracked = (
            (self.theme_mode, self.preview_zoom)
            if section == "appearance"
            else (self.ai_provider, self.ai_model, self.ai_token_edit)
            if section == "ai"
            else (self.provider, self.owner_edit, self.repo_edit, self.branch_edit, self.local_root, self.token_edit)
        )
        for field in tracked:
            for signal_name in (
                "textChanged",
                "currentTextChanged",
                "currentIndexChanged",
                "valueChanged",
            ):
                signal = getattr(field, signal_name, None)
                if signal is not None:
                    signal.connect(self._mark_dirty)
        self._set_save_enabled(False)

    def _mark_dirty(self, *_args: object) -> None:
        self._dirty = True
        self._set_save_enabled(True)

    def _add_field_error(self, form: QFormLayout, name: str) -> None:
        label = QLabel()
        label.setObjectName("FieldError")
        label.setWordWrap(True)
        label.setVisible(False)
        self._field_errors[name] = label
        form.addRow("", label)

    def _clear_field_errors(self) -> None:
        for label in self._field_errors.values():
            try:
                label.clear()
                label.setVisible(False)
            except RuntimeError:
                # Other settings sections are constructed but not attached to
                # this page, so Qt may already have reclaimed their labels.
                continue

    def _set_field_error(self, name: str, message: str) -> None:
        label = self._field_errors[name]
        label.setText(message)
        label.setVisible(True)

    def _validate_ai_fields(self) -> bool:
        self._clear_field_errors()
        if self.ai_model.currentText().strip():
            return True
        self._set_field_error("ai_model", "请填写支持图片输入的模型 ID。")
        self.ai_model.setFocus(Qt.FocusReason.OtherFocusReason)
        return False

    def _validate_cloud_fields(self, *, require_token: bool) -> bool:
        self._clear_field_errors()
        provider = str(self.provider.currentData())
        if provider == "local_folder":
            if self.local_root.text().strip():
                return True
            self._set_field_error("cloud_root", "请选择本地云同步目录。")
            self.local_root.setFocus(Qt.FocusReason.OtherFocusReason)
            return False
        for name, field, message in (
            ("cloud_owner", self.owner_edit, "请填写仓库所有者。"),
            ("cloud_repo", self.repo_edit, "请填写仓库名称。"),
        ):
            if not field.text().strip():
                self._set_field_error(name, message)
                field.setFocus(Qt.FocusReason.OtherFocusReason)
                return False
        key = self._credential_key_for_provider()
        if require_token and not self.token_edit.text().strip() and not (
            key and get_secret(key)
        ):
            self._set_field_error("cloud_token", "测试连接前请粘贴访问令牌。")
            self.token_edit.setFocus(Qt.FocusReason.OtherFocusReason)
            return False
        return True

    @staticmethod
    def _toggle_secret_visibility(field: QLineEdit, button: QPushButton) -> None:
        showing = field.echoMode() == QLineEdit.EchoMode.Normal
        field.setEchoMode(
            QLineEdit.EchoMode.Password if showing else QLineEdit.EchoMode.Normal
        )
        button.setText("显示" if showing else "隐藏")

    @property
    def has_unsaved_changes(self) -> bool:
        return self._dirty

    def discard_unsaved_changes(self) -> None:
        """Mark the current in-memory form as intentionally abandoned."""

        self._dirty = False
        self._set_save_enabled(False)

    def _set_save_enabled(self, enabled: bool) -> None:
        if self._save_button is not None:
            self._save_button.setEnabled(enabled)

    def _refresh_theme_status(self) -> None:
        selected_mode = str(self.theme_mode.currentData())
        resolved_mode = current_theme_name()
        # The highlighted choice represents the saved/pending preference; in
        # system mode the resolved light/dark palette is only a status.
        for mode, button in self.theme_buttons.items():
            button.setProperty("themeSelected", mode == selected_mode)
            button.style().unpolish(button)
            button.style().polish(button)
        source = "跟随系统" if selected_mode == "system" else "固定选择"
        label = "浅色" if resolved_mode == "light" else "深色"
        self.theme_status.setText(f"{label}（{source}）")
        self.theme_status.setAccessibleDescription(
            f"当前实际显示为{label}，主题来源为{source}"
        )

    def _select_theme_button(self, button_id: int) -> None:
        button = self.theme_button_group.button(button_id)
        if button is None:
            return
        mode = str(button.property("themeMode"))
        self.theme_mode.setCurrentIndex(self.theme_mode.findData(mode))
        self._refresh_theme_status()
        app = QApplication.instance()
        if app is not None:
            manager = get_theme_manager(app)
            if manager is not None:
                manager.set_mode(mode)

    def _apply_theme(self) -> None:
        mode = str(self.theme_mode.currentData())
        zoom_scale = self.preview_zoom.value() / 100
        try:
            save_theme_preference(self.runtime.paths.root, mode)
            save_preview_zoom_preference(self.runtime.paths.root, zoom_scale)
            self.runtime.settings.application.theme = mode
            self.runtime.settings.application.preview_zoom_scale = zoom_scale
            set_preview_zoom_scale(zoom_scale)
            app = QApplication.instance()
            if app is not None:
                manager = get_theme_manager(app)
                if manager is None:
                    apply_app_theme(app, mode)
                else:
                    manager.set_mode(mode)
            self._refresh_theme_status()
            self.discard_unsaved_changes()
            self.status_message.emit("外观设置已保存并应用")
        except (ConfigError, ValueError) as exc:
            QMessageBox.warning(self, "外观设置未保存", str(exc))

    def _ai_credential_key(self) -> str:
        cfg = self.runtime.settings.ai.providers.get("openai_compatible")
        return (cfg.credential_key if cfg else None) or "yancuo_ai_api_key"

    def _save_ai_token(self) -> bool:
        key = self._ai_credential_key()
        token = self.ai_token_edit.text().strip()
        if not token:
            return True
        try:
            set_secret(key, token)
            self.ai_token_edit.clear()
            self.ai_token_status.setText(mask_secret(get_secret(key)))
            self.status_message.emit("AI 密钥已保存到系统凭据")
            return True
        except DomainError as exc:
            QMessageBox.warning(self, "失败", str(exc))
            return False

    def _clear_ai_token(self) -> None:
        if QMessageBox.question(self, "清除 AI 密钥", "确定要从系统凭据中清除 AI 密钥吗？") != QMessageBox.StandardButton.Yes:
            return
        delete_secret(self._ai_credential_key())
        self.ai_token_status.setText(mask_secret(None))
        self._mark_dirty()
        self.status_message.emit("AI 密钥已从系统凭据中清除")

    def _apply_ai_session(self) -> None:
        name = self.ai_provider.currentData()
        model = self.ai_model.currentText().strip()
        if not self._validate_ai_fields():
            return
        try:
            if not self._save_ai_token():
                return
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
            self.discard_unsaved_changes()
            provider_label = "Faro API" if name == "openai_compatible" else "Mock"
            self.status_message.emit(f"AI 设置已保存：{provider_label} / {model}")
        except (ConfigError, DomainError) as exc:
            self._set_field_error("ai_token", str(exc))
            QMessageBox.warning(self, "AI 设置未就绪", str(exc))

    def _test_ai_connection(self) -> None:
        name = self.ai_provider.currentData()
        if name == "mock":
            self.status_message.emit("Mock 不访问网络；连接测试请先选择 Faro API")
            return
        if not self._validate_ai_fields():
            return
        model = self.ai_model.currentText().strip()
        self.test_ai_button.setEnabled(False)
        self.test_ai_button.setText("正在测试…")
        try:
            provider = get_provider(self.runtime.settings, name)
            provider.validate_configuration()
            list_models = getattr(provider, "list_models", None)
            if not callable(list_models):
                raise DomainError("当前提供商不支持连接测试")
        except DomainError as exc:
            self.test_ai_button.setEnabled(True)
            self.test_ai_button.setText("测试 Faro 连接")
            self._on_ai_connection_failed(str(exc))
            return
        self._connection_worker = CallableWorker(
            lambda: list_models(timeout_seconds=20), self
        )
        self._connection_worker.finished_ok.connect(
            lambda models: self._on_ai_connection_finished(model, models)
        )
        self._connection_worker.failed.connect(self._on_ai_connection_failed)
        self._connection_worker.finished.connect(self._on_connection_worker_finished)
        self._connection_worker.start()

    def _on_ai_connection_finished(self, model: str, value: object) -> None:
        models = [str(item) for item in value] if isinstance(value, list) else []
        self._last_connection_test = f"连接成功，最后测试：{QDateTime.currentDateTime().toString('yyyy-MM-dd HH:mm')}"
        self.ai_connection_notice.set_state(
            "连接成功，模型列表获取正常。" if not model or model in models else "连接成功，但当前模型不在返回列表中。",
            "success" if not model or model in models else "info",
        )
        if model and model not in models:
            sample = "、".join(models[:8]) or "（服务未返回模型）"
            QMessageBox.warning(self, "连接成功，但模型未找到", f"Faro 身份验证成功，但模型列表中没有“{model}”。\n请从模型广场重新复制 ID。当前返回示例：{sample}")
        else:
            self.status_message.emit(f"Faro 连接成功，已在模型列表中找到“{model}”")

    def _on_ai_connection_failed(self, error: str) -> None:
        self._set_field_error("ai_token", error)
        self._last_connection_test = f"连接失败：{error}"
        self.ai_connection_notice.set_state(self._last_connection_test, "error")
        QMessageBox.warning(self, "Faro 连接失败", error)

    def _on_connection_worker_finished(self) -> None:
        worker = self._connection_worker
        self._connection_worker = None
        self.test_ai_button.setEnabled(True)
        self.test_ai_button.setText("测试 Faro 连接")
        if worker is not None:
            worker.deleteLater()

    def _fetch_ai_models(self) -> None:
        name = self.ai_provider.currentData()
        if name == "mock":
            self.status_message.emit("Mock 不访问网络，也没有远端模型列表")
            return
        if self._ai_model_worker is not None:
            return
        try:
            provider = get_provider(self.runtime.settings, name)
            provider.validate_configuration()
        except DomainError as exc:
            QMessageBox.warning(self, "无法获取模型", str(exc))
            return

        self.fetch_ai_models.setEnabled(False)
        self.fetch_ai_models.setText("正在获取…")
        self.ai_model_status.set_state(
            "正在从 Faro API 获取模型列表…",
            "loading",
        )
        worker = AIModelListWorker(provider, timeout_seconds=20, parent=self)
        worker.finished_ok.connect(self._on_ai_models_loaded)
        worker.failed.connect(self._on_ai_models_failed)
        worker.finished.connect(self._on_ai_model_worker_finished)
        self._ai_model_worker = worker
        worker.start()

    def _on_ai_models_loaded(self, models: object) -> None:
        choices = sorted(
            {
                str(model).strip()
                for model in (models if isinstance(models, list) else [])
                if str(model).strip()
            }
        )
        current = self.ai_model.currentText().strip()
        self.ai_model.blockSignals(True)
        self.ai_model.clear()
        self.ai_model.addItems(choices)
        if current and current not in choices:
            self.ai_model.insertItem(0, current)
        if current:
            self.ai_model.setCurrentText(current)
        elif choices:
            self.ai_model.setCurrentIndex(0)
        self.ai_model.blockSignals(False)

        if choices:
            self.ai_model_status.set_state(
                f"已获取 {len(choices)} 个可用模型。列表不标注视觉能力，"
                "请选择确认支持图片输入的模型。",
                "success",
            )
        else:
            self.ai_model_status.set_state(
                "API 身份验证成功，但没有返回模型；仍可手动输入模型 ID。",
                "disabled",
            )

    def _on_ai_models_failed(self, error: str) -> None:
        self.ai_model_status.set_state(
            "获取失败；仍可手动输入模型 ID。",
            "error",
        )
        QMessageBox.warning(self, "获取模型失败", error)

    def _on_ai_model_worker_finished(self) -> None:
        worker = self._ai_model_worker
        self._ai_model_worker = None
        self.fetch_ai_models.setEnabled(True)
        self.fetch_ai_models.setText("获取可用模型")
        if worker is not None:
            worker.deleteLater()

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
            self._set_cloud_permission_state(key)
        elif name == "github":
            self.token_label.setText("GitHub PAT")
            self.token_status.setText(mask_secret(get_secret(key) if key else None))
            self.token_edit.setEnabled(True)
            self._set_cloud_permission_state(key)
        else:
            self.token_label.setText("令牌（本地文件夹无需）")
            self.token_status.setText("—")
            self.token_edit.setEnabled(False)
            self.token_edit.setToolTip("本地文件夹提供商不使用云端令牌")
            self.cloud_permission_notice.set_state(
                "本地文件夹提供商不需要云端令牌。",
                "disabled",
            )
        remote = name in {"gitlink", "github"}
        for field in (self.owner_edit, self.repo_edit, self.branch_edit, self.token_status, self.token_control, self.cloud_permission_notice):
            self.cloud_form.setRowVisible(field, remote)
        self.clear_cloud_token_button.setVisible(remote)

    def _set_cloud_permission_state(self, key: str | None) -> None:
        self.token_edit.setToolTip("")
        if key and get_secret(key):
            self.cloud_permission_notice.set_state(
                "云令牌已保存在操作系统凭据中。",
                "success",
            )
        else:
            self.cloud_permission_notice.set_state(
                "尚未保存云令牌；连接测试和远端操作当前不可用。",
                "permission",
            )

    def _on_provider_changed(self) -> None:
        self._refresh_token_ui()

    def _browse_local(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择本地云同步目录")
        if path:
            self.local_root.setText(path)

    def _save_token(self) -> None:
        key = self._credential_key_for_provider()
        if not key:
            self.status_message.emit("当前提供商不需要令牌")
            return
        token = self.token_edit.text().strip()
        if not token:
            QMessageBox.warning(self, "提示", "请先粘贴令牌")
            return
        try:
            set_secret(key, token)
            self.token_edit.clear()
            self.token_status.setText(mask_secret(get_secret(key)))
            self._set_cloud_permission_state(key)
            self.status_message.emit("云令牌已保存到系统凭据")
        except DomainError as exc:
            QMessageBox.warning(self, "失败", str(exc))

    def _clear_token(self) -> None:
        key = self._credential_key_for_provider()
        if not key:
            return
        delete_secret(key)
        self.token_status.setText(mask_secret(None))
        self._set_cloud_permission_state(key)
        self.status_message.emit("云令牌已从系统凭据中清除")

    def _save_cloud_settings(self) -> None:
        if not self._validate_cloud_fields(require_token=False):
            return
        key = self._credential_key_for_provider()
        token = self.token_edit.text().strip()
        if key and token:
            try:
                set_secret(key, token)
                self.token_edit.clear()
                self.token_status.setText(mask_secret(get_secret(key)))
                self._set_cloud_permission_state(key)
            except DomainError as exc:
                QMessageBox.warning(self, "云令牌未保存", str(exc))
                return
        self._apply_session_provider(notify=False)
        try:
            save_cloud_preferences(
                self.runtime.paths.root,
                provider=str(self.provider.currentData()),
                owner=self.owner_edit.text(),
                repository=self.repo_edit.text(),
                local_root=self.local_root.text(),
                branch=self.branch_edit.text(),
                enabled=True,
            )
        except (ConfigError, OSError) as exc:
            QMessageBox.warning(self, "云端设置未保存", str(exc))
            return
        self.discard_unsaved_changes()
        self.status_message.emit("云端设置已保存")

    def _apply_session_provider(self, *, notify: bool = True) -> None:
        name = self.provider.currentData()
        self.runtime.settings.cloud.default_provider = name
        self.runtime.settings.cloud.repository.owner = self.owner_edit.text().strip()
        self.runtime.settings.cloud.repository.name = (
            self.repo_edit.text().strip() or "graduate-mistake-book-data"
        )
        self.runtime.settings.cloud.repository.branch = self.branch_edit.text().strip() or "sync"
        self.runtime.settings.cloud.enabled = True
        local_root = self.local_root.text().strip()
        self.runtime.settings.cloud.local_root = local_root
        if name == "local_folder":
            os.environ["YANCUO_CLOUD_LOCAL_ROOT"] = local_root
        if notify:
            self.status_message.emit(f"当前会话已应用云端提供商：{name}")

    def _test_cloud(self) -> None:
        if not self._validate_cloud_fields(require_token=True):
            return
        self.test_cloud_button.setEnabled(False)
        self.test_cloud_button.setText("正在测试…")
        self._apply_session_provider(notify=False)
        try:
            root = (
                Path(self.local_root.text().strip())
                if self.provider.currentData() == "local_folder"
                else None
            )
            provider = get_cloud_provider(self.runtime.settings, local_root=root)
        except DomainError as exc:
            self.test_cloud_button.setEnabled(True)
            self.test_cloud_button.setText("测试云连接")
            self._on_cloud_connection_failed(str(exc))
            return
        self._connection_worker = CallableWorker(provider.test_connection, self)
        self._connection_worker.finished_ok.connect(self._on_cloud_connection_finished)
        self._connection_worker.failed.connect(self._on_cloud_connection_failed)
        self._connection_worker.finished.connect(self._on_cloud_connection_worker_finished)
        self._connection_worker.start()

    def _on_cloud_connection_finished(self, _result: object) -> None:
        try:
            save_cloud_preferences(
                self.runtime.paths.root,
                provider=str(self.provider.currentData()),
                owner=self.owner_edit.text(),
                repository=self.repo_edit.text(),
                local_root=self.local_root.text(),
                branch=self.branch_edit.text(),
                enabled=True,
            )
        except (ConfigError, OSError) as exc:
            QMessageBox.warning(self, "连接成功但保存失败", f"云端连接有效，但当前字段未能写入本地偏好：{exc}")
            return
        self.cloud_permission_notice.set_state(
            "连接测试成功，设置已保存。最后测试："
            + QDateTime.currentDateTime().toString("yyyy-MM-dd HH:mm"),
            "success",
        )
        self.status_message.emit("云端连接测试成功")

    def _on_cloud_connection_failed(self, error: str) -> None:
        self._set_field_error(
            "cloud_root" if self.provider.currentData() == "local_folder" else "cloud_token",
            error,
        )
        self.cloud_permission_notice.set_state(f"连接失败：{error}", "error")
        QMessageBox.warning(self, "连接失败", error)

    def _on_cloud_connection_worker_finished(self) -> None:
        worker = self._connection_worker
        self._connection_worker = None
        self.test_cloud_button.setEnabled(True)
        self.test_cloud_button.setText("测试云连接")
        if worker is not None:
            worker.deleteLater()

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
    if runtime.settings.cloud.local_root:
        return runtime.settings.cloud.local_root
    return str(runtime.paths.backup_dir / "cloud_local")
