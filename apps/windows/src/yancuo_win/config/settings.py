"""配置加载与校验（TOML + Pydantic Settings）。密钥只存引用名（环境变量 / 系统凭据）。"""

from __future__ import annotations

import json
import os
from importlib import resources
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)


class ApplicationConfig(BaseModel):
    language: str = "zh_CN"
    theme: str = "system"
    auto_save_seconds: int = Field(default=30, ge=1)
    confirm_before_delete: bool = True
    schema_version: int = Field(default=1, ge=1)

    @field_validator("theme")
    @classmethod
    def validate_theme(cls, value: str) -> str:
        value = value.strip().lower()
        if value not in {"system", "light", "dark"}:
            raise ValueError("theme 必须是 system、light 或 dark")
        return value


class PathsConfig(BaseModel):
    database: str = "error_book.db"
    asset_dir: str = "assets"
    inbox_dir: str = "inbox"
    cache_dir: str = "cache"
    export_dir: str = "exports"
    backup_dir: str = "backups"
    template_dir: str = "templates"
    workspace_dir: str = "workspaces"
    log_dir: str = "logs"


class ImportConfig(BaseModel):
    scan_subfolders: bool = True
    copy_original_files: bool = True
    skip_duplicates: bool = True
    supported_extensions: list[str] = Field(
        default_factory=lambda: [".jpg", ".jpeg", ".png", ".webp", ".pdf"]
    )

    @field_validator("supported_extensions")
    @classmethod
    def normalize_extensions(cls, value: list[str]) -> list[str]:
        return [item if item.startswith(".") else f".{item}" for item in value]


class AiProviderConfig(BaseModel):
    base_url: str = ""
    api_key_env: str = "FARO_API_KEY"
    # 系统凭据键名；设置页保存的密钥读这里（环境变量优先，配置不存明文）
    credential_key: str = "yancuo_ai_api_key"


class AiConfig(BaseModel):
    enabled: bool = False
    default_provider: str = "openai_compatible"
    default_vision_model: str = ""
    default_text_model: str = ""
    request_timeout_seconds: int = Field(default=120, ge=1)
    max_concurrent_tasks: int = Field(default=2, ge=1)
    save_raw_responses: bool = True
    require_review_before_apply: bool = True
    max_images_per_job: int = Field(default=50, ge=1)
    max_daily_cost_yuan: float = Field(default=20.0, ge=0)
    allow_delete: bool = False
    providers: dict[str, AiProviderConfig] = Field(default_factory=dict)


class ExportConfig(BaseModel):
    default_format: str = "docx"
    paper_size: str = "A4"
    orientation: str = "portrait"
    avoid_problem_page_break: bool = True
    include_original_image: bool = True


class BackupConfig(BaseModel):
    enabled: bool = True
    interval_hours: int = Field(default=24, ge=1)
    keep_count: int = Field(default=30, ge=1)


class PrivacyConfig(BaseModel):
    send_original_images_to_ai: bool = True
    remove_image_metadata: bool = True
    mask_personal_information: bool = False


class CloudRepositoryConfig(BaseModel):
    owner: str = ""
    name: str = "graduate-mistake-book-data"
    branch: str = "sync"
    require_private: bool = True


class CloudProviderEndpointConfig(BaseModel):
    base_url: str = ""
    auth_method: str = "token"
    credential_key: str = ""


class CloudConfig(BaseModel):
    enabled: bool = False
    default_provider: str = "gitlink"
    sync_mode: str = "manual"
    auto_backup: bool = True
    auto_backup_interval_hours: int = Field(default=24, ge=1)
    keep_release_count: int = Field(default=10, ge=1)
    chunk_size_mb: int = Field(default=250, ge=1)
    encrypt_uploads: bool = True
    upload_on_exit: bool = False
    download_on_start: bool = False
    repository: CloudRepositoryConfig = Field(default_factory=CloudRepositoryConfig)
    gitlink: CloudProviderEndpointConfig = Field(
        default_factory=lambda: CloudProviderEndpointConfig(
            base_url="https://www.gitlink.org.cn",
            auth_method="token",
            credential_key="yancuo_gitlink_token",
        )
    )
    github: CloudProviderEndpointConfig = Field(
        default_factory=lambda: CloudProviderEndpointConfig(
            base_url="https://api.github.com",
            auth_method="token",
            credential_key="yancuo_github_token",
        )
    )


class SyncConfig(BaseModel):
    device_name: str = ""
    conflict_policy: str = "ask"
    allow_background_download: bool = True
    allow_background_upload: bool = False
    verify_hash_after_upload: bool = True
    create_snapshot_before_merge: bool = True


class EncryptionConfig(BaseModel):
    enabled: bool = False
    algorithm_version: int = Field(default=1, ge=1)
    key_reference: str = "yancuo_data_key"


class AppSettings(BaseSettings):
    """应用配置。敏感密钥不得出现在 TOML 中。"""

    model_config = SettingsConfigDict(
        env_prefix="YANCUO_",
        env_nested_delimiter="__",
        extra="ignore",
        populate_by_name=True,
    )

    application: ApplicationConfig = Field(default_factory=ApplicationConfig)
    paths: PathsConfig = Field(default_factory=PathsConfig)
    import_cfg: ImportConfig = Field(default_factory=ImportConfig, alias="import")
    ai: AiConfig = Field(default_factory=AiConfig)
    export: ExportConfig = Field(default_factory=ExportConfig)
    backup: BackupConfig = Field(default_factory=BackupConfig)
    privacy: PrivacyConfig = Field(default_factory=PrivacyConfig)
    cloud: CloudConfig = Field(default_factory=CloudConfig)
    sync: SyncConfig = Field(default_factory=SyncConfig)
    encryption: EncryptionConfig = Field(default_factory=EncryptionConfig)


class ConfigError(Exception):
    """配置校验或加载失败。"""


def repo_root() -> Path:
    # apps/windows/src/yancuo_win/config/settings.py → 仓库根
    location = Path(__file__).resolve()
    for parent in (location.parent, *location.parents):
        if (parent / "config" / "default.toml").is_file() and (
            parent / "protocol" / "schemas"
        ).is_dir():
            return parent
    # ``.../site-packages/yancuo_win/config/settings.py`` -> package root.
    return location.parents[1]


def resource_path(*parts: str) -> Path | None:
    """Resolve a resource from a checkout or an installed wheel.

    Source checkouts intentionally use the canonical top-level files so edits
    are picked up immediately. Wheels contain a copy below
    ``yancuo_win/resources``; this lookup is independent of the working
    directory and install prefix. ``None`` means no copy is available.
    """

    relative = Path(*parts)
    root_candidate = repo_root() / relative
    if root_candidate.is_file():
        return root_candidate

    try:
        bundled = resources.files("yancuo_win").joinpath(
            "resources", *relative.parts
        )
        if bundled.is_file():
            try:
                return Path(bundled)
            except TypeError:
                return None
    except (FileNotFoundError, ModuleNotFoundError, TypeError):
        return None
    return None


def default_toml_path() -> Path:
    path = resource_path("config", "default.toml")
    if path is None:
        # Preserve the historical Path return type and provide a useful path
        # for callers that want to report the missing resource.
        return repo_root() / "config" / "default.toml"
    return path


def resolve_config_path() -> Path:
    override = os.environ.get("YANCUO_CONFIG_FILE")
    if override:
        return Path(override).expanduser().resolve()
    return default_toml_path()


def load_settings(config_file: Path | None = None) -> AppSettings:
    """加载并校验配置。失败时抛出 ConfigError，信息可直接展示给用户。"""
    path = config_file or resolve_config_path()
    if not path.is_file():
        raise ConfigError(f"找不到配置文件：{path}")

    try:

        class _TomlSettings(AppSettings):
            @classmethod
            def settings_customise_sources(
                cls,
                settings_cls: type[BaseSettings],
                init_settings: PydanticBaseSettingsSource,
                env_settings: PydanticBaseSettingsSource,
                dotenv_settings: PydanticBaseSettingsSource,
                file_secret_settings: PydanticBaseSettingsSource,
            ) -> tuple[PydanticBaseSettingsSource, ...]:
                return (
                    init_settings,
                    env_settings,
                    TomlConfigSettingsSource(settings_cls, toml_file=path),
                    dotenv_settings,
                    file_secret_settings,
                )

        return _TomlSettings()
    except ValidationError as exc:
        raise ConfigError(_format_validation_error(exc)) from exc
    except ConfigError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ConfigError(f"配置加载失败：{exc}") from exc


def apply_user_preferences(settings: AppSettings, data_root: Path) -> AppSettings:
    """Apply non-sensitive per-user overrides stored beside the local database."""

    path = Path(data_root) / "preferences.json"
    if not path.is_file():
        return settings
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"本地偏好设置无法读取：{path}") from exc
    if not isinstance(payload, dict):
        raise ConfigError(f"本地偏好设置格式无效：{path}")

    application = payload.get("application")
    if isinstance(application, dict) and "theme" in application:
        try:
            settings.application.theme = ApplicationConfig.validate_theme(
                str(application["theme"])
            )
        except ValueError as exc:
            raise ConfigError(f"本地偏好设置包含无效主题：{application['theme']}") from exc

    ai = payload.get("ai")
    if not isinstance(ai, dict):
        return settings

    provider = str(ai.get("default_provider") or "").strip()
    if provider:
        if provider != "mock" and provider not in settings.ai.providers:
            raise ConfigError(f"本地偏好设置包含未知 AI 提供商：{provider}")
        settings.ai.default_provider = provider
    model = str(ai.get("default_vision_model") or "").strip()
    if model:
        settings.ai.default_vision_model = model
        settings.ai.default_text_model = model
    if "enabled" in ai:
        settings.ai.enabled = bool(ai["enabled"])
    return settings


def save_ai_preferences(
    data_root: Path,
    *,
    provider: str,
    model: str,
    enabled: bool = True,
) -> Path:
    """Persist AI selection without ever writing an API key to disk."""

    provider = provider.strip()
    model = model.strip()
    if not provider:
        raise ConfigError("AI 提供商不能为空")
    if not model:
        raise ConfigError("视觉模型 ID 不能为空")

    root = Path(data_root)
    root.mkdir(parents=True, exist_ok=True)
    path = root / "preferences.json"
    payload: dict[str, Any] = {}
    if path.is_file():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(existing, dict):
                payload = existing
        except (OSError, json.JSONDecodeError):
            payload = {}
    payload["ai"] = {
        "enabled": enabled,
        "default_provider": provider,
        "default_vision_model": model,
    }
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    return path


def save_theme_preference(data_root: Path, theme: str) -> Path:
    """Persist the selected appearance without discarding other preferences."""

    try:
        normalized = ApplicationConfig.validate_theme(theme)
    except ValueError as exc:
        raise ConfigError(f"无效主题：{theme}") from exc

    root = Path(data_root)
    root.mkdir(parents=True, exist_ok=True)
    path = root / "preferences.json"
    payload: dict[str, Any] = {}
    if path.is_file():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(existing, dict):
                payload = existing
        except (OSError, json.JSONDecodeError):
            payload = {}
    application = payload.get("application")
    if not isinstance(application, dict):
        application = {}
    application["theme"] = normalized
    payload["application"] = application
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    return path


def _format_validation_error(exc: ValidationError) -> str:
    lines = ["配置校验失败："]
    for err in exc.errors():
        loc = ".".join(str(part) for part in err.get("loc", ()))
        msg = err.get("msg", "未知错误")
        lines.append(f"- {loc}: {msg}")
    return "\n".join(lines)


def settings_to_public_dict(settings: AppSettings) -> dict[str, Any]:
    """导出可展示配置（不含任何密钥值，仅含引用名）。"""
    return settings.model_dump(by_alias=True)
