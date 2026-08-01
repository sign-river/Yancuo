"""AI 提供商统一接口。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


def normalize_region(value: Any) -> dict[str, float]:
    """Return a safe normalized rectangle or an empty whole-image marker."""

    if not isinstance(value, dict):
        return {}
    try:
        x = float(value.get("x", 0))
        y = float(value.get("y", 0))
        width = float(value.get("width", 0))
        height = float(value.get("height", 0))
    except (TypeError, ValueError):
        return {}
    x = min(1.0, max(0.0, x))
    y = min(1.0, max(0.0, y))
    width = min(1.0 - x, max(0.0, width))
    height = min(1.0 - y, max(0.0, height))
    if width <= 0 or height <= 0:
        return {}
    return {"x": x, "y": y, "width": width, "height": height}


def normalize_content_blocks(value: Any) -> list[dict[str, Any]]:
    """Keep only explicit blocks; do not infer unrecognized cells or figures."""
    if not isinstance(value, list):
        return []
    result: list[dict[str, Any]] = []
    for raw in value[:100]:
        if not isinstance(raw, dict) or raw.get("type") not in {"text", "formula", "table", "figure"}:
            continue
        block = {"type": raw["type"], "content": raw.get("content", ""), "source_region": normalize_region(raw.get("source_region"))}
        if isinstance(raw.get("source_image_index"), int) and raw["source_image_index"] >= 0:
            block["source_image_index"] = raw["source_image_index"]
        if block["type"] == "table":
            if not isinstance(raw.get("rows"), list) or not all(isinstance(row, list) for row in raw["rows"]):
                continue
            block["rows"] = raw["rows"]
        if block["type"] == "figure" and not block["source_region"]:
            continue
        result.append(block)
    return result


@dataclass
class StructuredCandidate:
    fields: dict[str, Any]
    uncertain_fields: list[dict[str, str]] = field(default_factory=list)
    region: dict[str, float] = field(default_factory=dict)


@dataclass
class StructuredResult:
    fields: dict[str, Any]
    uncertain_fields: list[dict[str, str]] = field(default_factory=list)
    candidates: list[StructuredCandidate] = field(default_factory=list)
    raw_text: str = ""
    cost_estimate: float = 0.0
    model: str = ""
    timings_ms: dict[str, float] = field(default_factory=dict)
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def candidate_results(self) -> list[StructuredCandidate]:
        """Return multi-problem output with single-problem compatibility."""

        return self.candidates or [
            StructuredCandidate(
                fields=self.fields,
                uncertain_fields=self.uncertain_fields,
            )
        ]


@dataclass(frozen=True)
class JsonCompletionResult:
    """Raw structured-chat response plus provider metadata."""

    raw_text: str
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_estimate: float = 0.0
    diagnostics: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderCapabilities:
    supports_vision_structure: bool = True
    supports_chat: bool = False
    supports_chat_images: bool = False


@dataclass(frozen=True)
class ChatCompletionResult:
    content_markdown: str
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_estimate: float = 0.0
    diagnostics: dict[str, Any] = field(default_factory=dict)


class AIProvider(ABC):
    name: str
    capabilities = ProviderCapabilities()

    def validate_configuration(self) -> None:
        """Fail before a workflow creates staging data when setup is incomplete."""

        return None

    def complete_json(
        self,
        *,
        request: dict[str, Any],
        model: str,
        timeout_seconds: int,
    ) -> JsonCompletionResult:
        """Run a schema-constrained text request when the provider supports it."""

        raise NotImplementedError(f"{self.name} 不支持结构化文本请求")

    def complete_chat(
        self,
        *,
        messages: list[dict[str, Any]],
        model: str,
        timeout_seconds: int,
    ) -> ChatCompletionResult:
        raise NotImplementedError(f"{self.name} 不支持题目对话")

    @abstractmethod
    def structure_from_image(
        self,
        *,
        image_path: str,
        prompt: str,
        model: str,
        timeout_seconds: int,
        retry_attempts: int | None = None,
    ) -> StructuredResult:
        raise NotImplementedError

    def structure_from_images(
        self,
        *,
        image_paths: list[str],
        prompt: str,
        model: str,
        timeout_seconds: int,
        retry_attempts: int | None = None,
    ) -> StructuredResult:
        """Recognize one ordered image group in a single provider request."""

        if len(image_paths) == 1:
            return self.structure_from_image(
                image_path=image_paths[0],
                prompt=prompt,
                model=model,
                timeout_seconds=timeout_seconds,
                retry_attempts=retry_attempts,
            )
        raise NotImplementedError(f"{self.name} 不支持单次多图识别")
