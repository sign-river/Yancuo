"""OpenAI 兼容视觉结构化提供商（密钥仅来自环境变量）。"""

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


class OpenAICompatibleProvider(AIProvider):
    name = "openai_compatible"

    def __init__(self, *, base_url: str, api_key_env: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key_env = api_key_env

    def _api_key(self) -> str:
        if not self.api_key_env:
            raise DomainError("未配置 api_key_env")
        key = os.environ.get(self.api_key_env, "").strip()
        if not key:
            raise DomainError(
                f"环境变量 {self.api_key_env} 未设置；禁止在配置文件中明文存放密钥"
            )
        return key

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
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key()}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            # 绝不回显完整 Authorization
            raise DomainError(f"AI 请求失败 HTTP {exc.code}: {detail[:300]}") from exc
        except Exception as exc:  # noqa: BLE001
            raise DomainError(f"AI 请求失败：{exc}") from exc

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
