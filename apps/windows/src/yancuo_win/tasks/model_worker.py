"""Fetch provider model IDs without blocking the settings UI."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QThread, Signal


class AIModelListWorker(QThread):
    finished_ok = Signal(object)
    failed = Signal(str)

    def __init__(self, provider: Any, *, timeout_seconds: int = 20, parent=None) -> None:
        super().__init__(parent)
        self.provider = provider
        self.timeout_seconds = timeout_seconds

    def run(self) -> None:
        try:
            list_models = getattr(self.provider, "list_models", None)
            if not callable(list_models):
                raise RuntimeError("当前 AI 提供商不支持获取模型列表")
            models = list_models(timeout_seconds=self.timeout_seconds)
            self.finished_ok.emit(models)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))
