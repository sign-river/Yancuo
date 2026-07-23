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


class AIProvider(ABC):
    name: str

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

    @abstractmethod
    def structure_from_image(
        self,
        *,
        image_path: str,
        prompt: str,
        model: str,
        timeout_seconds: int,
    ) -> StructuredResult:
        raise NotImplementedError
