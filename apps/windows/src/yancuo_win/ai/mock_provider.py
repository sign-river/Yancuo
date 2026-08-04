"""Mock 提供商：无网络、确定性输出，用于测试与离线演示。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from yancuo_win.ai.base import AIProvider, ChatCompletionResult, JsonCompletionResult, ProviderCapabilities, StructuredResult


class MockProvider(AIProvider):
    name = "mock"
    capabilities = ProviderCapabilities(supports_chat=True)

    def structure_from_image(
        self,
        *,
        image_path: str,
        prompt: str,
        model: str,
        timeout_seconds: int,
    ) -> StructuredResult:
        path = Path(image_path)
        digest = hashlib.sha256(path.name.encode("utf-8")).hexdigest()[:8]
        title = f"识别题目-{path.stem[:24]}"
        fields = {
            "title": title,
            "question_markdown": f"（Mock）根据图片 `{path.name}` 识别的题目正文。hash={digest}",
            "question_latex": r"\int_0^1 x\,dx",
            "user_answer": "（Mock）用户作答占位",
            "correct_answer": "1/2",
            "solution_markdown": "（Mock）标准解析占位。",
            "error_analysis": "（Mock）计算失误",
            "tags": ["AI待确认", "Mock"],
        }
        uncertain = [
            {
                "field": "question_latex",
                "content": r"\int_0^1 x dx 或 \int_0^1 x\,dx",
                "reason": "Mock：演示不确定字段",
            }
        ]
        return StructuredResult(
            fields=fields,
            uncertain_fields=uncertain,
            raw_text=str(fields),
            cost_estimate=0.0,
            model=model or "mock-v1",
            diagnostics={
                "structure_suggestion": {
                    "layout_kind": "single",
                    "subquestion_count": 1,
                    "confidence": 0.98,
                    "rationale": "Mock：单张图片中只检测到一个独立题目。",
                    "signals": ["单一题干", "无连续小题编号"],
                }
            },
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
        del retry_attempts
        if not image_paths:
            raise ValueError("image_paths must not be empty")
        result = self.structure_from_image(
            image_path=image_paths[0],
            prompt=prompt,
            model=model,
            timeout_seconds=timeout_seconds,
        )
        if len(image_paths) > 1:
            result.fields["question_markdown"] += (
                f"（Mock：已按顺序合并 {len(image_paths)} 张来源图片。）"
            )
        return result

    def complete_json(
        self,
        *,
        request: dict[str, Any],
        model: str,
        timeout_seconds: int,
    ) -> JsonCompletionResult:
        del timeout_seconds
        response_name = (
            request.get("response_format", {})
            .get("json_schema", {})
            .get("name", "")
        )
        messages = request.get("messages") or []
        user_content = str(messages[-1].get("content") or "") if messages else ""
        if response_name == "yancuo_search_spec":
            try:
                query = str(json.loads(user_content).get("query") or "").strip()
            except (json.JSONDecodeError, AttributeError):
                query = user_content.strip()
            payload = {
                "keywords": [query] if query else [],
                "semantic_intent": query,
                "limit": 10,
            }
        elif response_name == "yancuo_problem_completion":
            try:
                request_data = json.loads(user_content)
            except (json.JSONDecodeError, TypeError):
                request_data = {}
            current = request_data.get("current_problem", {})
            allowed = request_data.get("allowed_fields", [])
            if not isinstance(current, dict):
                current = {}
            if not isinstance(allowed, list):
                allowed = []
            defaults = {
                "title": "Mock 补全题目",
                "question_markdown": "（Mock）根据现有结构化内容补全的题干",
                "question_latex": "",
                "user_answer": "",
                "correct_answer": "（Mock）待核对答案",
                "solution_markdown": "（Mock）待核对解析",
                "error_analysis": "（Mock）待核对错因",
                "notes": "",
                "tags": ["Mock"],
            }
            payload = {
                field: (
                    f"（Mock）{current[field]}"
                    if field == "question_markdown" and current.get(field)
                    else current.get(field) or defaults.get(field, "")
                )
                for field in allowed
                if field in defaults
            }
            payload["uncertain_fields"] = []
        elif response_name == "yancuo_search_rerank":
            ids: list[str] = []
            for line in user_content.splitlines():
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                candidate_id = item.get("id") if isinstance(item, dict) else None
                if isinstance(candidate_id, str) and candidate_id.startswith("problem_"):
                    ids.append(candidate_id)
            payload = {
                "matches": [
                    {
                        "id": candidate_id,
                        "score": max(0.1, 1.0 - index * 0.05),
                        "reason": "Mock：本地候选与搜索描述匹配",
                    }
                    for index, candidate_id in enumerate(ids)
                ]
            }
        else:
            raise NotImplementedError("Mock 不支持该结构化文本请求")
        return JsonCompletionResult(
            raw_text=json.dumps(payload, ensure_ascii=False),
            model=model or "mock-v1",
        )

    def complete_chat(
        self,
        *,
        messages: list[dict[str, Any]],
        model: str,
        timeout_seconds: int,
        on_text_delta: Callable[[str], None] | None = None,
    ) -> ChatCompletionResult:
        del timeout_seconds
        question = str(messages[-1].get("content") or "") if messages else ""
        reply = f"（Mock）针对当前题目的讨论：{question[:200]}"
        if on_text_delta is not None:
            on_text_delta(reply)
        return ChatCompletionResult(
            content_markdown=reply,
            model=model or "mock-v1",
        )
