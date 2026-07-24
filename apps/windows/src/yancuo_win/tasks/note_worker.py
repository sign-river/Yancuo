"""Background worker for note image extraction."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Signal

from yancuo_win.application.note_ai_service import NoteAiService


class NoteExtractionWorker(QThread):
    finished_ok = Signal(object)
    failed = Signal(str)

    def __init__(self, service: NoteAiService, image_path: Path, instruction: str, parent=None) -> None:
        super().__init__(parent)
        self.service = service
        self.image_path = image_path
        self.instruction = instruction

    def run(self) -> None:
        try:
            self.finished_ok.emit(
                self.service.extract_from_image(self.image_path, instruction=self.instruction)
            )
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))
