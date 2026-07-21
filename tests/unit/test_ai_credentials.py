"""AI 密钥：环境变量优先，其次系统凭据。"""

from __future__ import annotations

import pytest

from yancuo_win.ai.openai_compatible import OpenAICompatibleProvider
from yancuo_win.domain.rules import DomainError


def test_api_key_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("YANCUO_AI_API_KEY", "sk-from-env")
    monkeypatch.setattr(
        "yancuo_win.ai.openai_compatible.get_secret",
        lambda _k: "sk-from-keyring",
    )
    p = OpenAICompatibleProvider(
        base_url="https://example.com/v1",
        api_key_env="YANCUO_AI_API_KEY",
        credential_key="yancuo_ai_api_key",
    )
    assert p._api_key() == "sk-from-env"


def test_api_key_from_keyring_when_env_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("YANCUO_AI_API_KEY", raising=False)
    monkeypatch.setattr(
        "yancuo_win.ai.openai_compatible.get_secret",
        lambda _k: "sk-from-keyring",
    )
    p = OpenAICompatibleProvider(
        base_url="https://example.com/v1",
        api_key_env="YANCUO_AI_API_KEY",
        credential_key="yancuo_ai_api_key",
    )
    assert p._api_key() == "sk-from-keyring"


def test_api_key_missing_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("YANCUO_AI_API_KEY", raising=False)
    monkeypatch.setattr(
        "yancuo_win.ai.openai_compatible.get_secret",
        lambda _k: None,
    )
    p = OpenAICompatibleProvider(
        base_url="https://example.com/v1",
        api_key_env="YANCUO_AI_API_KEY",
        credential_key="yancuo_ai_api_key",
    )
    with pytest.raises(DomainError, match="未配置 AI 密钥"):
        p._api_key()
