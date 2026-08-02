"""OpenAI 兼容视觉结构化提供商（密钥：环境变量优先，其次系统凭据）。"""

from __future__ import annotations

import base64
import http.client
import json
import os
import re
import socket
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from yancuo_win.ai.base import (
    AIProvider,
    ChatCompletionResult,
    JsonCompletionResult,
    ProviderCapabilities,
    StructuredCandidate,
    StructuredResult,
    normalize_region,
)
from yancuo_win.domain.rules import DomainError
from yancuo_win.infrastructure.credentials import get_secret
from yancuo_win.infrastructure.safe_http import safe_urlopen


_MAX_REQUEST_ATTEMPTS = 3
_MAX_AI_RESPONSE_BYTES = 16 * 1024 * 1024
_MAX_ERROR_RESPONSE_BYTES = 4 * 1024
_MAX_AI_IMAGE_COUNT = 20
_MAX_AI_IMAGE_BYTES = 32 * 1024 * 1024
_MAX_AI_IMAGE_TOTAL_BYTES = 64 * 1024 * 1024
_MAX_EMBEDDED_JSON_CANDIDATES = 128
_RETRYABLE_HTTP_CODES = {408, 425, 429, 500, 502, 503, 504}
_TRANSIENT_NETWORK_ERRORS = (
    urllib.error.URLError,
    http.client.RemoteDisconnected,
    http.client.IncompleteRead,
    ConnectionResetError,
    ConnectionAbortedError,
    BrokenPipeError,
    TimeoutError,
    socket.timeout,
)


def _read_limited(response: Any, limit: int, *, label: str) -> bytes:
    payload = response.read(limit + 1)
    if len(payload) > limit:
        raise DomainError(f"{label}过大（上限 {limit} 字节）")
    return payload


class OpenAICompatibleProvider(AIProvider):
    name = "openai_compatible"
    capabilities = ProviderCapabilities(supports_chat=True, supports_chat_images=True)

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
        self._last_request_attempts = 0
        self._last_server_timing: dict[str, str] = {}

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
        self._validate_base_url()
        self._api_key()

    def _validate_base_url(self) -> None:
        parsed = urlparse(self.base_url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise DomainError("AI Base URL 必须是无内嵌凭据、查询参数或片段的 HTTPS 地址")

    @staticmethod
    def _encode_image_content(image_paths: list[str]) -> list[dict[str, Any]]:
        if not image_paths:
            raise DomainError("未选择图片")
        if len(image_paths) > _MAX_AI_IMAGE_COUNT:
            raise DomainError(f"单次 AI 请求最多 {_MAX_AI_IMAGE_COUNT} 张图片")
        image_content: list[dict[str, Any]] = []
        total_bytes = 0
        for image_path in image_paths:
            path = Path(image_path)
            if not path.is_file():
                raise DomainError(f"图片不存在：{path}")
            try:
                size = path.stat().st_size
            except OSError as exc:
                raise DomainError(f"无法读取图片：{path}") from exc
            if size <= 0 or size > _MAX_AI_IMAGE_BYTES:
                raise DomainError("单张 AI 图片必须在 1 字节到 32 MiB 之间")
            total_bytes += size
            if total_bytes > _MAX_AI_IMAGE_TOTAL_BYTES:
                raise DomainError("单次 AI 请求的图片总大小不能超过 64 MiB")
            try:
                with path.open("rb") as stream:
                    payload = stream.read(_MAX_AI_IMAGE_BYTES + 1)
            except OSError as exc:
                raise DomainError(f"无法读取图片：{path}") from exc
            if len(payload) != size or len(payload) > _MAX_AI_IMAGE_BYTES:
                raise DomainError("图片在读取期间发生变化或超过 32 MiB 上限")
            mime = {".png": "image/png", ".webp": "image/webp"}.get(
                path.suffix.lower(), "image/jpeg"
            )
            encoded = base64.b64encode(payload).decode("ascii")
            image_content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{encoded}"},
                }
            )
        return image_content

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
        retry_attempts: int | None = None,
        retry_instruction: str = "请检查网络后点击“重新尝试失败项”",
    ) -> dict[str, Any]:
        self._validate_base_url()
        key = self._api_key()
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        body: Any = None
        self._last_request_attempts = 0
        self._last_server_timing = {}
        max_attempts = retry_attempts or _MAX_REQUEST_ATTEMPTS
        max_attempts = max(1, min(max_attempts, _MAX_REQUEST_ATTEMPTS))
        for attempt in range(1, max_attempts + 1):
            self._last_request_attempts = attempt
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
                with safe_urlopen(request, timeout=timeout_seconds) as response:
                    headers = getattr(response, "headers", None)
                    if headers is not None:
                        for header in (
                            "Server-Timing",
                            "OpenAI-Processing-Ms",
                            "X-Request-Duration-Ms",
                        ):
                            value = headers.get(header)
                            if value:
                                self._last_server_timing[header.lower()] = str(value)
                    body = json.loads(
                        _read_limited(
                            response,
                            _MAX_AI_RESPONSE_BYTES,
                            label="AI 响应",
                        ).decode("utf-8")
                    )
                break
            except urllib.error.HTTPError as exc:
                if exc.code in _RETRYABLE_HTTP_CODES and attempt < max_attempts:
                    time.sleep(0.6 * attempt)
                    continue
                detail = _read_limited(
                    exc,
                    _MAX_ERROR_RESPONSE_BYTES,
                    label="AI 错误响应",
                ).decode("utf-8", errors="replace")
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
            except _TRANSIENT_NETWORK_ERRORS as exc:
                if attempt < max_attempts:
                    time.sleep(0.6 * attempt)
                    continue
                reason = exc.reason if isinstance(exc, urllib.error.URLError) else str(exc)
                raise DomainError(
                    "AI 服务连接中断，程序已自动重试 2 次仍未恢复。"
                    f"{retry_instruction}。详情：{reason}"
                ) from exc
            except (json.JSONDecodeError, UnicodeDecodeError, RecursionError) as exc:
                raise DomainError("AI 服务返回了无法解析的响应") from exc
        if not isinstance(body, dict):
            raise DomainError("AI 响应格式无效")
        return body

    def _response_diagnostics(self, body: dict[str, Any]) -> dict[str, Any]:
        """Return privacy-safe request metadata exposed by compatible providers."""

        diagnostics: dict[str, Any] = {
            "request_attempts": self._last_request_attempts,
        }
        usage = body.get("usage")
        if isinstance(usage, dict):
            token_usage: dict[str, int] = {}
            for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                value = usage.get(key)
                if isinstance(value, int) and not isinstance(value, bool):
                    token_usage[key] = value
            if token_usage:
                diagnostics["token_usage"] = token_usage
        if self._last_server_timing:
            diagnostics["server_timing"] = dict(self._last_server_timing)
        return diagnostics

    def _request_stream_json(
        self,
        endpoint: str,
        *,
        timeout_seconds: int,
        payload: dict[str, Any],
        on_text_delta: Callable[[str], None],
        retry_attempts: int | None = None,
    ) -> dict[str, Any]:
        """Read an OpenAI-compatible SSE response and rebuild its final body."""

        self._validate_base_url()
        key = self._api_key()
        stream_payload = dict(payload)
        stream_payload["stream"] = True
        data = json.dumps(stream_payload).encode("utf-8")
        max_attempts = max(1, min(retry_attempts or _MAX_REQUEST_ATTEMPTS, _MAX_REQUEST_ATTEMPTS))
        self._last_request_attempts = 0
        self._last_server_timing = {}
        emitted = False
        for attempt in range(1, max_attempts + 1):
            self._last_request_attempts = attempt
            request = urllib.request.Request(
                f"{self.base_url}{endpoint}",
                data=data,
                headers={
                    "Accept": "text/event-stream",
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {key}",
                },
                method="POST",
            )
            try:
                with safe_urlopen(request, timeout=timeout_seconds) as response:
                    headers = getattr(response, "headers", None)
                    content_type = str(headers.get("Content-Type") if headers else "")
                    if "text/event-stream" not in content_type.lower():
                        body = json.loads(
                            _read_limited(
                                response,
                                _MAX_AI_RESPONSE_BYTES,
                                label="AI 响应",
                            ).decode("utf-8")
                        )
                        content = body.get("choices", [{}])[0].get("message", {}).get("content", "")
                        if isinstance(content, str) and content:
                            on_text_delta(content)
                        return body

                    parts: list[str] = []
                    model_name = str(payload.get("model") or "")
                    usage: dict[str, Any] = {}
                    received_bytes = 0
                    while raw_line := response.readline(
                        _MAX_AI_RESPONSE_BYTES - received_bytes + 1
                    ):
                        received_bytes += len(raw_line)
                        if received_bytes > _MAX_AI_RESPONSE_BYTES:
                            raise DomainError(
                                "AI 流式响应过大"
                                f"（上限 {_MAX_AI_RESPONSE_BYTES} 字节）"
                            )
                        line = raw_line.decode("utf-8").strip()
                        if not line.startswith("data:"):
                            continue
                        event_data = line[5:].strip()
                        if event_data == "[DONE]":
                            break
                        if not event_data:
                            continue
                        event = json.loads(event_data)
                        if isinstance(event.get("model"), str):
                            model_name = event["model"]
                        if isinstance(event.get("usage"), dict):
                            usage = event["usage"]
                        choices = event.get("choices")
                        if not isinstance(choices, list) or not choices:
                            continue
                        delta = choices[0].get("delta") if isinstance(choices[0], dict) else None
                        content = delta.get("content") if isinstance(delta, dict) else None
                        if isinstance(content, str) and content:
                            emitted = True
                            parts.append(content)
                            on_text_delta(content)
                    if not parts:
                        raise DomainError("AI 流式响应未返回可用内容")
                    return {
                        "model": model_name,
                        "usage": usage,
                        "choices": [{"message": {"content": "".join(parts)}}],
                    }
            except urllib.error.HTTPError as exc:
                if exc.code in _RETRYABLE_HTTP_CODES and attempt < max_attempts and not emitted:
                    time.sleep(0.6 * attempt)
                    continue
                detail = _read_limited(
                    exc,
                    _MAX_ERROR_RESPONSE_BYTES,
                    label="AI 错误响应",
                ).decode("utf-8", errors="replace").replace(key, "***")
                raise DomainError(f"AI 流式请求失败 HTTP {exc.code}：{detail[:240]}") from exc
            except _TRANSIENT_NETWORK_ERRORS as exc:
                if attempt < max_attempts and not emitted:
                    time.sleep(0.6 * attempt)
                    continue
                raise DomainError("AI 流式连接中断，已接收内容会保留，可重新尝试任务") from exc
            except (json.JSONDecodeError, UnicodeDecodeError, RecursionError) as exc:
                raise DomainError("AI 流式响应格式无效") from exc
        raise DomainError("AI 流式请求未完成")

    def structure_from_image(
        self,
        *,
        image_path: str,
        prompt: str,
        model: str,
        timeout_seconds: int,
        retry_attempts: int | None = None,
    ) -> StructuredResult:
        return self.structure_from_images(
            image_paths=[image_path],
            prompt=prompt,
            model=model,
            timeout_seconds=timeout_seconds,
            retry_attempts=retry_attempts,
        )

    def structure_from_images(
        self,
        *,
        image_paths: list[str],
        prompt: str,
        model: str,
        timeout_seconds: int,
        retry_attempts: int | None = None,
    ) -> StructuredResult:
        encode_started = time.perf_counter()
        image_content = self._encode_image_content(image_paths)
        image_encode_ms = (time.perf_counter() - encode_started) * 1000
        payload = {
            "model": model or "gpt-4o-mini",
            "temperature": 0,
            "messages": [
                {
                    "role": "user",
                    "content": [{"type": "text", "text": prompt}, *image_content],
                }
            ],
        }
        request_started = time.perf_counter()
        body = self._request_json(
            "/chat/completions",
            method="POST",
            timeout_seconds=timeout_seconds,
            payload=payload,
            retry_attempts=retry_attempts,
        )
        request_ms = (time.perf_counter() - request_started) * 1000

        return self._parse_structured_response(
            body,
            requested_model=model,
            image_encode_ms=image_encode_ms,
            request_ms=request_ms,
        )

    def stream_structure_from_images(
        self,
        *,
        image_paths: list[str],
        prompt: str,
        model: str,
        timeout_seconds: int,
        on_text_delta: Callable[[str], None],
        retry_attempts: int | None = None,
    ) -> StructuredResult:
        encode_started = time.perf_counter()
        image_content = self._encode_image_content(image_paths)
        image_encode_ms = (time.perf_counter() - encode_started) * 1000
        payload = {
            "model": model or "gpt-4o-mini",
            "temperature": 0,
            "messages": [
                {
                    "role": "user",
                    "content": [{"type": "text", "text": prompt}, *image_content],
                }
            ],
        }
        request_started = time.perf_counter()
        body = self._request_stream_json(
            "/chat/completions",
            timeout_seconds=timeout_seconds,
            payload=payload,
            on_text_delta=on_text_delta,
            retry_attempts=retry_attempts,
        )
        request_ms = (time.perf_counter() - request_started) * 1000
        return self._parse_structured_response(
            body,
            requested_model=model,
            image_encode_ms=image_encode_ms,
            request_ms=request_ms,
        )

    def _parse_structured_response(
        self,
        body: dict[str, Any],
        *,
        requested_model: str,
        image_encode_ms: float,
        request_ms: float,
    ) -> StructuredResult:
        parse_started = time.perf_counter()
        raw_text = ""
        try:
            raw_text = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise DomainError("AI 响应格式无效") from exc

        parsed = _extract_json(raw_text)
        suggestion = {
            "layout_kind": parsed.pop("layout_kind", None),
            "subquestion_count": parsed.pop("subquestion_count", None),
            "confidence": parsed.pop("confidence", None),
            "rationale": parsed.pop("rationale", None),
            "signals": parsed.pop("signals", None),
        }
        candidate_payloads = parsed.get("problems")
        if isinstance(candidate_payloads, list):
            raw_candidates = [item for item in candidate_payloads if isinstance(item, dict)]
        else:
            raw_candidates = [parsed]
        if not raw_candidates:
            raise DomainError("AI 没有返回可用题目")

        candidates: list[StructuredCandidate] = []
        for raw_candidate in raw_candidates:
            fields = dict(raw_candidate)
            uncertain = fields.pop("uncertain_fields", []) or []
            region = fields.pop("region", {}) or {}
            if not isinstance(uncertain, list):
                uncertain = []
            if not isinstance(region, dict):
                region = {}
            candidates.append(
                StructuredCandidate(
                    fields=fields,
                    uncertain_fields=[item for item in uncertain if isinstance(item, dict)],
                    region=normalize_region(region),
                )
            )
        first = candidates[0]
        usage = body.get("usage") or {}
        # 粗略费用：按 token 估算（可配置化前的占位）
        total_tokens = int(usage.get("total_tokens") or 0)
        cost = round(total_tokens * 0.00002, 6)
        response_parse_ms = (time.perf_counter() - parse_started) * 1000
        return StructuredResult(
            fields=first.fields,
            uncertain_fields=first.uncertain_fields,
            candidates=candidates,
            raw_text=raw_text,
            cost_estimate=cost,
            model=str(body.get("model") or requested_model),
            timings_ms={
                "image_encode": image_encode_ms,
                "request": request_ms,
                "response_parse": response_parse_ms,
            },
            diagnostics={
                **self._response_diagnostics(body),
                "structure_suggestion": suggestion,
            },
        )

    def complete_json(
        self,
        *,
        request: dict[str, Any],
        model: str,
        timeout_seconds: int,
    ) -> JsonCompletionResult:
        payload = dict(request)
        payload["model"] = model
        body = self._request_json(
            "/chat/completions",
            method="POST",
            timeout_seconds=timeout_seconds,
            payload=payload,
        )
        try:
            raw_text = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise DomainError("AI 结构化文本响应格式无效") from exc
        if not isinstance(raw_text, str):
            raise DomainError("AI 结构化文本响应内容必须是 JSON 文本")
        usage = body.get("usage") or {}
        prompt_tokens = int(usage.get("prompt_tokens") or 0)
        completion_tokens = int(usage.get("completion_tokens") or 0)
        total_tokens = int(
            usage.get("total_tokens") or prompt_tokens + completion_tokens
        )
        return JsonCompletionResult(
            raw_text=raw_text,
            model=str(body.get("model") or model),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            cost_estimate=round(total_tokens * 0.00002, 6),
            diagnostics=self._response_diagnostics(body),
        )

    def complete_chat(
        self,
        *,
        messages: list[dict[str, Any]],
        model: str,
        timeout_seconds: int,
    ) -> ChatCompletionResult:
        body = self._request_json(
            "/chat/completions",
            method="POST",
            timeout_seconds=timeout_seconds,
            payload={"model": model or "gpt-4o-mini", "temperature": 0.2, "messages": messages},
            retry_instruction="请检查网络后重新发送问题",
        )
        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise DomainError("AI 对话响应格式无效") from exc
        if not isinstance(content, str) or not content.strip():
            raise DomainError("AI 对话未返回正文")
        usage = body.get("usage") or {}
        prompt_tokens = int(usage.get("prompt_tokens") or 0)
        completion_tokens = int(usage.get("completion_tokens") or 0)
        total_tokens = int(usage.get("total_tokens") or prompt_tokens + completion_tokens)
        return ChatCompletionResult(
            content_markdown=content.strip(),
            model=str(body.get("model") or model),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            cost_estimate=round(total_tokens * 0.00002, 6),
            diagnostics=self._response_diagnostics(body),
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
    except (json.JSONDecodeError, RecursionError):
        pass
    decoder = json.JSONDecoder()
    for candidate_index, match in enumerate(re.finditer(r"\{", text), start=1):
        if candidate_index > _MAX_EMBEDDED_JSON_CANDIDATES:
            raise DomainError("AI 输出包含过多 JSON 起始候选")
        try:
            data, _end = decoder.raw_decode(text, match.start())
        except (json.JSONDecodeError, RecursionError):
            continue
        if isinstance(data, dict):
            return data
    raise DomainError("无法从 AI 输出解析 JSON")
