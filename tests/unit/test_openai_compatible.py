"""OpenAI-compatible response parsing boundaries."""

from __future__ import annotations

import pytest

import yancuo_win.ai.openai_compatible as provider_module
from yancuo_win.ai.openai_compatible import OpenAICompatibleProvider, _extract_json
from yancuo_win.domain.rules import DomainError


def test_extract_json_skips_invalid_braces_before_valid_object() -> None:
    result = _extract_json(
        '模型草稿 {不是 JSON}；最终结果：{"fields":{"title":"极限"}}。'
    )

    assert result == {"fields": {"title": "极限"}}


def test_extract_json_uses_first_complete_object_in_multi_object_output() -> None:
    result = _extract_json('说明 {"first": 1}，补充 {"second": 2}')

    assert result == {"first": 1}


def test_extract_json_bounds_candidate_scanning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(provider_module, "_MAX_EMBEDDED_JSON_CANDIDATES", 2)

    with pytest.raises(DomainError, match="过多 JSON 起始候选"):
        _extract_json('{坏一} {坏二} {"late": true}')


def test_extract_json_converts_excessive_nesting_to_domain_error() -> None:
    payload = "[" * 2000 + "0" + "]" * 2000

    with pytest.raises(DomainError, match="无法从 AI 输出解析 JSON"):
        _extract_json(payload)


def test_stream_reader_bounds_a_line_before_buffering_the_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class OversizedLineResponse:
        headers = {"Content-Type": "text/event-stream"}

        def __init__(self) -> None:
            self.requested_sizes: list[int] = []

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def readline(self, size: int) -> bytes:
            self.requested_sizes.append(size)
            return b"x" * size

    response = OversizedLineResponse()
    monkeypatch.setenv("TEST_AI_KEY", "secret")
    monkeypatch.setattr(provider_module, "_MAX_AI_RESPONSE_BYTES", 8)
    monkeypatch.setattr(provider_module, "safe_urlopen", lambda *_args, **_kwargs: response)
    provider = OpenAICompatibleProvider(
        base_url="https://example.test/v1",
        api_key_env="TEST_AI_KEY",
    )

    with pytest.raises(DomainError, match="流式响应过大"):
        provider._request_stream_json(
            "/chat/completions",
            timeout_seconds=1,
            payload={"model": "test"},
            on_text_delta=lambda _text: None,
            retry_attempts=1,
        )

    assert response.requested_sizes == [9]
