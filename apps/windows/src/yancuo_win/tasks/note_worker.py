"""Background worker for note image extraction."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Signal

from yancuo_win.application.note_ai_service import NoteAiService


class NoteExtractionWorker(QThread):
    finished_ok = Signal(object)
    failed = Signal(object)

    def __init__(
        self,
        service: NoteAiService,
        session_id: str,
        image_path: Path,
        instruction: str,
        classification_mode: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.service = service
        self.session_id = session_id
        self.image_path = image_path
        self.instruction = instruction
        self.classification_mode = classification_mode

    def run(self) -> None:
        try:
            draft = self.service.extract_from_image(
                self.image_path,
                instruction=self.instruction,
                classification_mode=self.classification_mode,
            )
            self.finished_ok.emit((self.session_id, draft))
        except Exception as exc:  # noqa: BLE001
            self.failed.emit((self.session_id, str(exc)))
