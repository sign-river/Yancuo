"""配置加载与校验测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from yancuo_win.config.settings import ConfigError, default_toml_path, load_settings


def test_default_toml_exists() -> None:
    assert default_toml_path().is_file()


def test_load_default_settings() -> None:
    settings = load_settings(default_toml_path())
    assert settings.application.schema_version == 2
    assert settings.ai.enabled is True
    assert settings.cloud.enabled is True
    assert settings.cloud.default_provider == "local_folder"
    provider = settings.ai.providers.get("mock")
    assert provider is not None
    openai = settings.ai.providers.get("openai_compatible")
    assert openai is not None
    assert openai.api_key_env == "YANCUO_AI_API_KEY"


def test_invalid_config_raises(tmp_path: Path) -> None:
    bad = tmp_path / "bad.toml"
    bad.write_text(
        """
[application]
auto_save_seconds = -1
""".strip(),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError) as exc_info:
        load_settings(bad)
    assert "配置校验失败" in str(exc_info.value)


def test_default_toml_has_no_plaintext_secrets() -> None:
    text = default_toml_path().read_text(encoding="utf-8")
    lowered = text.lower()
    assert "sk-" not in lowered
    assert "api_key_env" in text
    assert "credential_key" in text
