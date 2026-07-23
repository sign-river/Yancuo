"""AI 密钥：环境变量优先，其次系统凭据。"""

from __future__ import annotations

import json
import http.client
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
    assert {"image_encode", "request", "response_parse"} <= result.timings_ms.keys()
    assert result.diagnostics["request_attempts"] == 1


def test_structure_from_image_accepts_multi_problem_envelope(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("FARO_API_KEY", "sk-faro-test")
    image = tmp_path / "multi.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\nmulti-problem")

    def fake_urlopen(_request, timeout=None):  # noqa: ANN001, ARG001
        return _Response(
            {
                "model": "vision-model",
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "problems": [
                                        {
                                            "title": "第一题",
                                            "question_markdown": "题目一",
                                            "region": {
                                                "x": 0.1,
                                                "y": 0.2,
                                                "width": 0.8,
                                                "height": 0.3,
                                            },
                                            "uncertain_fields": [],
                                        },
                                        {
                                            "title": "第二题",
                                            "question_markdown": "题目二",
                                            "uncertain_fields": [
                                                {
                                                    "field": "title",
                                                    "content": "第二题",
                                                    "reason": "字迹模糊",
                                                }
                                            ],
                                        },
                                    ]
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ],
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
        prompt="提取全部题目",
        model="vision-model",
        timeout_seconds=15,
    )

    assert len(result.candidate_results()) == 2
    assert result.candidate_results()[0].fields["title"] == "第一题"
    assert result.candidate_results()[0].region == {
        "x": 0.1,
        "y": 0.2,
        "width": 0.8,
        "height": 0.3,
    }
    assert result.candidate_results()[1].fields["title"] == "第二题"
    assert result.candidate_results()[1].uncertain_fields[0]["reason"] == "字迹模糊"


def test_remote_disconnect_is_retried_before_succeeding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FARO_API_KEY", "sk-faro-test")
    attempts = 0
    delays: list[float] = []

    def fake_urlopen(_request, timeout=None):  # noqa: ANN001, ARG001
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise http.client.RemoteDisconnected("remote closed")
        return _Response({"data": [{"id": "vision-model"}]})

    monkeypatch.setattr(
        "yancuo_win.ai.openai_compatible.urllib.request.urlopen", fake_urlopen
    )
    monkeypatch.setattr(
        "yancuo_win.ai.openai_compatible.time.sleep", delays.append
    )
    provider = OpenAICompatibleProvider(
        base_url="https://faroapi.com/v1",
        api_key_env="FARO_API_KEY",
    )

    assert provider.list_models() == ["vision-model"]
    assert attempts == 3
    assert delays == [0.6, 1.2]
    assert provider._last_request_attempts == 3


def test_remote_disconnect_exhaustion_uses_actionable_chinese_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FARO_API_KEY", "sk-faro-test")
    def always_disconnect(*_args, **_kwargs):  # noqa: ANN002, ANN003
        raise http.client.RemoteDisconnected("remote closed")

    monkeypatch.setattr(
        "yancuo_win.ai.openai_compatible.urllib.request.urlopen", always_disconnect
    )
    monkeypatch.setattr("yancuo_win.ai.openai_compatible.time.sleep", lambda _delay: None)
    provider = OpenAICompatibleProvider(
        base_url="https://faroapi.com/v1",
        api_key_env="FARO_API_KEY",
    )

    with pytest.raises(DomainError, match="自动重试 2 次.*重新尝试失败项"):
        provider.list_models()
