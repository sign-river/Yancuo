"""AI 密钥：环境变量优先，其次系统凭据。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from yancuo_win.ai.openai_compatible import OpenAICompatibleProvider
from yancuo_win.domain.rules import DomainError


class _Response:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


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


def test_list_models_uses_faro_compatible_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FARO_API_KEY", "sk-faro-test")
    captured = {}

    def fake_urlopen(request, timeout):  # noqa: ANN001
        captured["request"] = request
        captured["timeout"] = timeout
        return _Response({"data": [{"id": "vision-b"}, {"id": "vision-a"}]})

    monkeypatch.setattr(
        "yancuo_win.ai.openai_compatible.urllib.request.urlopen", fake_urlopen
    )
    provider = OpenAICompatibleProvider(
        base_url="https://faroapi.com/v1",
        api_key_env="FARO_API_KEY",
    )
    assert provider.list_models(timeout_seconds=9) == ["vision-a", "vision-b"]
    assert captured["request"].full_url == "https://faroapi.com/v1/models"
    assert captured["request"].get_header("Authorization") == "Bearer sk-faro-test"
    assert captured["timeout"] == 9


def test_structure_from_image_sends_multimodal_chat_completion(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("FARO_API_KEY", "sk-faro-test")
    image = tmp_path / "question.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\nreal-image-payload")
    captured = {}

    def fake_urlopen(request, timeout):  # noqa: ANN001
        captured["request"] = request
        captured["timeout"] = timeout
        return _Response(
            {
                "model": "vision-model",
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "title": "提取结果",
                                    "question_markdown": "题干",
                                    "uncertain_fields": [],
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ],
                "usage": {"total_tokens": 10},
            }
        )

    monkeypatch.setattr(
        "yancuo_win.ai.openai_compatible.urllib.request.urlopen", fake_urlopen
    )
    provider = OpenAICompatibleProvider(
        base_url="https://faroapi.com/v1",
        api_key_env="FARO_API_KEY",
    )
    result = provider.structure_from_image(
        image_path=str(image),
        prompt="只提取红圈题目",
        model="vision-model",
        timeout_seconds=15,
    )
    payload = json.loads(captured["request"].data.decode("utf-8"))
    content = payload["messages"][0]["content"]
    assert captured["request"].full_url == "https://faroapi.com/v1/chat/completions"
    assert payload["model"] == "vision-model"
    assert content[0]["text"] == "只提取红圈题目"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert result.fields["title"] == "提取结果"
    assert result.model == "vision-model"
