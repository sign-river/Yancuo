"""Background execution for bounded note AI search."""

from __future__ import annotations

from PySide6.QtCore import QThread, Signal


class NoteAiSearchWorker(QThread):
    finished_ok = Signal(object)
    failed = Signal(str)

    def __init__(self, service, *, query: str, statuses: tuple[str, ...], parent=None) -> None:
        super().__init__(parent)
        self.service = service
        self.query = query
        self.statuses = statuses

    def run(self) -> None:
        try:
            result = self.service.search(self.query, statuses=self.statuses)
            if not self.isInterruptionRequested():
                self.finished_ok.emit(result)
        except Exception as exc:  # noqa: BLE001
            if not self.isInterruptionRequested():
                self.failed.emit(str(exc))
