"""配置加载与校验测试。"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest
import yancuo_win

from yancuo_win.config.settings import (
    ApplicationConfig,
    ConfigError,
    apply_user_preferences,
    default_toml_path,
    load_settings,
    save_ai_preferences,
    save_theme_preference,
)


def test_default_toml_exists() -> None:
    assert default_toml_path().is_file()


def test_load_default_settings() -> None:
    settings = load_settings(default_toml_path())
    assert settings.application.schema_version == 6
    assert settings.ai.enabled is True
    assert settings.ai.default_provider == "openai_compatible"
    assert settings.cloud.enabled is True
    assert settings.cloud.default_provider == "local_folder"
    provider = settings.ai.providers.get("mock")
    assert provider is not None
    openai = settings.ai.providers.get("openai_compatible")
    assert openai is not None
    assert openai.api_key_env == "FARO_API_KEY"
    assert openai.credential_key == "yancuo_ai_api_key"


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


def test_ai_preferences_roundtrip_without_secret(tmp_path: Path) -> None:
    path = save_ai_preferences(
        tmp_path,
        provider="mock",
        model="offline-test-model",
    )
    text = path.read_text(encoding="utf-8")
    assert "offline-test-model" in text
    assert "api_key" not in text.lower()

    settings = load_settings(default_toml_path())
    apply_user_preferences(settings, tmp_path)
    assert settings.ai.default_provider == "mock"
    assert settings.ai.default_vision_model == "offline-test-model"


def test_theme_preferences_roundtrip_without_ai_settings(tmp_path: Path) -> None:
    path = save_theme_preference(tmp_path, "dark")
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload == {"application": {"theme": "dark"}}

    settings = load_settings(default_toml_path())
    apply_user_preferences(settings, tmp_path)
    assert settings.application.theme == "dark"


def test_theme_preference_preserves_ai_settings(tmp_path: Path) -> None:
    save_ai_preferences(tmp_path, provider="mock", model="offline-test-model")
    path = save_theme_preference(tmp_path, "light")
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["application"]["theme"] == "light"
    assert payload["ai"]["default_vision_model"] == "offline-test-model"


def test_application_rejects_unknown_theme() -> None:
    with pytest.raises(ValueError):
        ApplicationConfig(theme="sepia")


def test_bundled_resources_match_canonical_sources() -> None:
    package_root = Path(yancuo_win.__file__).resolve().parent
    bundled_root = package_root / "resources"
    canonical_config = default_toml_path()
    assert tomllib.loads(
        (bundled_root / "config" / "default.toml").read_text(encoding="utf-8")
    ) == tomllib.loads(canonical_config.read_text(encoding="utf-8"))

    repo = canonical_config.parents[1]
    for name in ("problem.schema.json", "operation.schema.json"):
        canonical = json.loads(
            (repo / "protocol" / "schemas" / name).read_text(encoding="utf-8")
        )
        bundled = json.loads(
            (bundled_root / "protocol" / "schemas" / name).read_text(encoding="utf-8")
        )
        assert bundled == canonical
