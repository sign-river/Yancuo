"""配置加载与校验测试。"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest
import yancuo_win

from yancuo_win.config.settings import ConfigError, default_toml_path, load_settings


def test_default_toml_exists() -> None:
    assert default_toml_path().is_file()


def test_load_default_settings() -> None:
    settings = load_settings(default_toml_path())
    assert settings.application.schema_version == 4
    assert settings.ai.enabled is True
    assert settings.cloud.enabled is True
    assert settings.cloud.default_provider == "local_folder"
    provider = settings.ai.providers.get("mock")
    assert provider is not None
    openai = settings.ai.providers.get("openai_compatible")
    assert openai is not None
    assert openai.api_key_env == "YANCUO_AI_API_KEY"
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
