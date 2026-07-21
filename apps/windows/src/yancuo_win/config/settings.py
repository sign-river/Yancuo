"""配置加载与校验（TOML + Pydantic Settings）。密钥仅引用环境变量名。"""

from __future__ import annotations

import os
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
    api_key_env: str = "YANCUO_AI_API_KEY"


class AiConfig(BaseModel):
    enabled: bool = False
    default_provider: str = "provider_1"
    default_vision_model: str = ""
    default_text_model: str = ""
    request_timeout_seconds: int = Field(default=120, ge=1)
    max_concurrent_tasks: int = Field(default=2, ge=1)
    save_raw_responses: bool = True
    require_review_before_apply: bool = True
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
            auth_method="github_app",
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
    return Path(__file__).resolve().parents[5]


def default_toml_path() -> Path:
    return repo_root() / "config" / "default.toml"


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
