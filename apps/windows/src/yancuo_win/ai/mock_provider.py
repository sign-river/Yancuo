"""Mock 提供商：无网络、确定性输出，用于测试与离线演示。"""

from __future__ import annotations

import hashlib
from pathlib import Path

from yancuo_win.ai.base import AIProvider, StructuredResult


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
