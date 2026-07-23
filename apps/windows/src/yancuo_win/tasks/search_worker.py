"""Background AI search execution that keeps Qt's main thread responsive."""

from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from yancuo_win.application.ai_search_service import (
    AiSearchDisclosure,
    AiSearchService,
)
from yancuo_win.application.search_spec import SearchBoundary


class AiSearchWorker(QThread):
    finished_ok = Signal(object)
    failed = Signal(str)
    progress = Signal(str)

    def __init__(
        self,
        service: AiSearchService,
        *,
        query: str,
        boundary: SearchBoundary,
        disclosure: AiSearchDisclosure | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.service = service
        self.query = query
        self.boundary = boundary
        self.disclosure = disclosure or AiSearchDisclosure()

    def cancel(self) -> None:
        self.requestInterruption()

    def run(self) -> None:
        try:
            result = self.service.search(
                self.query,
                boundary=self.boundary,
                disclosure=self.disclosure,
                progress=self.progress.emit,
                should_cancel=self.isInterruptionRequested,
            )
            if not self.isInterruptionRequested():
                self.finished_ok.emit(result)
        except Exception as exc:  # noqa: BLE001
            if not self.isInterruptionRequested():
                self.failed.emit(str(exc))
