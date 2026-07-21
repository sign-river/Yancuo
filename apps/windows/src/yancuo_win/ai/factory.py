"""按配置构造 AI 提供商。"""

from __future__ import annotations

from yancuo_win.ai.base import AIProvider
from yancuo_win.ai.mock_provider import MockProvider
from yancuo_win.ai.openai_compatible import OpenAICompatibleProvider
from yancuo_win.config.settings import AppSettings
from yancuo_win.domain.rules import DomainError


def get_provider(settings: AppSettings, name: str | None = None) -> AIProvider:
    provider_name = name or settings.ai.default_provider
    if provider_name == "mock":
        return MockProvider()
    cfg = settings.ai.providers.get(provider_name)
    if provider_name == "openai_compatible" or (
        cfg and provider_name in settings.ai.providers
    ):
        if cfg is None:
            raise DomainError(f"未配置提供商：{provider_name}")
        return OpenAICompatibleProvider(
            base_url=cfg.base_url or "https://api.openai.com/v1",
            api_key_env=cfg.api_key_env,
        )
    raise DomainError(f"未知 AI 提供商：{provider_name}")
