"""OpenAI 兼容视觉结构化提供商（密钥：环境变量优先，其次系统凭据）。"""

from __future__ import annotations

import base64
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from yancuo_win.ai.base import AIProvider, StructuredResult
from yancuo_win.domain.rules import DomainError
from yancuo_win.infrastructure.credentials import get_secret


class OpenAICompatibleProvider(AIProvider):
    name = "openai_compatible"

    def __init__(
        self,
        *,
        base_url: str,
        api_key_env: str,
        credential_key: str = "yancuo_ai_api_key",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key_env = api_key_env
        self.credential_key = credential_key or "yancuo_ai_api_key"

    def _api_key(self) -> str:
        if self.api_key_env:
            key = os.environ.get(self.api_key_env, "").strip()
            if key:
                return key
        secret = get_secret(self.credential_key)
        if secret:
            return secret.strip()
        raise DomainError(
            f"未配置 AI 密钥：请在设置中保存，或设置环境变量 {self.api_key_env or 'FARO_API_KEY'}"
        )

    def validate_configuration(self) -> None:
        if not self.base_url.startswith(("https://", "http://")):
            raise DomainError("AI Base URL 无效")
        self._api_key()

    def list_models(self, *, timeout_seconds: int = 20) -> list[str]:
        """Validate Faro/OpenAI-compatible authentication and return model IDs."""

        body = self._request_json(
            "/models",
            method="GET",
            timeout_seconds=timeout_seconds,
        )
        data = body.get("data")
        if not isinstance(data, list):
            raise DomainError("AI 模型列表响应格式无效")
        models = [
            str(item.get("id")).strip()
            for item in data
            if isinstance(item, dict) and str(item.get("id") or "").strip()
        ]
        return sorted(set(models))

    def _request_json(
        self,
        endpoint: str,
        *,
        method: str,
        timeout_seconds: int,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        key = self._api_key()
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(
            f"{self.base_url}{endpoint}",
            data=data,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {key}",
            },
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            detail = detail.replace(key, "***")
            hints = {
                400: "请检查模型 ID 与请求兼容性",
                401: "请检查 Faro API Key 是否完整、启用且未过期",
                404: "请检查 Base URL 是否为 https://faroapi.com/v1",
                429: "请检查 Faro 余额、令牌额度或稍后重试",
            }
            hint = hints.get(exc.code, "请稍后重试")
            raise DomainError(
                f"AI 请求失败 HTTP {exc.code}：{hint}。服务返回：{detail[:240]}"
            ) from exc
        except urllib.error.URLError as exc:
            raise DomainError(f"无法连接 AI 服务：{exc.reason}") from exc
        except TimeoutError as exc:
            raise DomainError("连接 AI 服务超时") from exc
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise DomainError("AI 服务返回了无法解析的响应") from exc
        if not isinstance(body, dict):
            raise DomainError("AI 响应格式无效")
        return body

    def structure_from_image(
        self,
        *,
        image_path: str,
        prompt: str,
        model: str,
        timeout_seconds: int,
    ) -> StructuredResult:
        path = Path(image_path)
        if not path.is_file():
            raise DomainError(f"图片不存在：{path}")
        mime = "image/jpeg"
        suffix = path.suffix.lower()
        if suffix == ".png":
            mime = "image/png"
        elif suffix == ".webp":
            mime = "image/webp"
        b64 = base64.b64encode(path.read_bytes()).decode("ascii")
        payload = {
            "model": model or "gpt-4o-mini",
            "temperature": 0,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime};base64,{b64}"},
                        },
                    ],
                }
            ],
        }
        body = self._request_json(
            "/chat/completions",
            method="POST",
            timeout_seconds=timeout_seconds,
            payload=payload,
        )

        raw_text = ""
        try:
            raw_text = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise DomainError("AI 响应格式无效") from exc

        parsed = _extract_json(raw_text)
        uncertain = parsed.pop("uncertain_fields", []) or []
        if not isinstance(uncertain, list):
            uncertain = []
        usage = body.get("usage") or {}
        # 粗略费用：按 token 估算（可配置化前的占位）
        total_tokens = int(usage.get("total_tokens") or 0)
        cost = round(total_tokens * 0.00002, 6)
        return StructuredResult(
            fields=parsed,
            uncertain_fields=[u for u in uncertain if isinstance(u, dict)],
            raw_text=raw_text,
            cost_estimate=cost,
            model=str(body.get("model") or model),
        )


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        raise DomainError("无法从 AI 输出解析 JSON")
    data = json.loads(match.group(0))
    if not isinstance(data, dict):
        raise DomainError("AI JSON 根节点必须是对象")
    return data
