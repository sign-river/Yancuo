"""Mock 提供商：无网络、确定性输出，用于测试与离线演示。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from yancuo_win.ai.base import AIProvider, JsonCompletionResult, StructuredResult


class MockProvider(AIProvider):
    name = "mock"

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
        )

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
