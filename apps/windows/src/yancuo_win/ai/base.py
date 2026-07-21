"""AI 提供商统一接口。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class StructuredResult:
    fields: dict[str, Any]
    uncertain_fields: list[dict[str, str]] = field(default_factory=list)
    raw_text: str = ""
    cost_estimate: float = 0.0
    model: str = ""


class AIProvider(ABC):
    name: str

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
