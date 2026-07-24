"""Smoke coverage for the in-shell note reader and block editor."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication, QWidget

import yancuo_win.ui.note_page as note_page_module
from yancuo_win.application.bootstrap import bootstrap_runtime
from yancuo_win.application.note_ai_service import (
    NoteBlockDraft,
    NoteExtractionDraft,
)
from yancuo_win.application.note_service import NoteService
from yancuo_win.config.settings import default_toml_path
from yancuo_win.ui.note_page import NotePage


class _ReaderStub(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.last_fields: dict = {}
        self.last_blocks: list[dict] = []
        self.last_tags: list[str] = []

    def set_note(self, fields, *, blocks=(), tag_names=()) -> None:
        self.last_fields = dict(fields)
        self.last_blocks = list(blocks)
        self.last_tags = list(tag_names)


@pytest.fixture()
def note_page(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> NotePage:
    monkeypatch.setenv("YANCUO_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("YANCUO_CONFIG_FILE", str(default_toml_path()))
    monkeypatch.setattr(note_page_module, "MathContentView", _ReaderStub)
    app = QApplication.instance() or QApplication([])
    runtime = bootstrap_runtime()
    page = NotePage(NoteService(runtime))
    app.processEvents()
    yield page
    page.close()


def test_note_page_creates_edits_and_reads_blocks(note_page: NotePage) -> None:
    note_page._create_note()
    assert note_page._note is not None
    assert note_page.note_list.count() == 1

    note_page.title_edit.setText("极限笔记")
    note_page.summary_edit.setPlainText("等价无穷小的常用结论")
    note_page._save_note()
    note_page._add_block("formula")
    assert note_page._block is not None
    note_page.block_content.setPlainText(r"\\sin x \\sim x")
    note_page._save_block()

    note_page._set_mode("read")
    assert note_page.mode_stack.currentIndex() == 1
    assert note_page.reader.last_fields["title"] == "极限笔记"
    assert "sin x" in note_page.reader.last_blocks[0]["content_latex"]


def test_note_page_moves_a_note_to_the_recycle_bin(note_page: NotePage) -> None:
    note_page._create_note()
    assert note_page._note is not None

    note_page._set_note_status("trashed")

    assert note_page._note is not None
    assert note_page._note.status == "trashed"
    assert not note_page.restore_button.isHidden()


def test_note_page_opens_original_on_demand_with_source_regions(
    note_page: NotePage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_path = note_page.notes.runtime.paths.root / "note-source.png"
    source = QPixmap(24, 24)
    source.fill(Qt.GlobalColor.white)
    assert source.save(str(source_path))
    draft = NoteExtractionDraft(
        source_path=source_path,
        title="有来源的笔记",
        summary="",
        blocks=[
            NoteBlockDraft(
                block_type="concept",
                content_markdown="概念",
                source_region={
                    "x": 0.1,
                    "y": 0.2,
                    "width": 0.3,
                    "height": 0.4,
                },
            )
        ],
    )
    note = note_page.note_ai.commit_draft(draft)
    opened: dict = {}

    class _ViewerStub:
        def __init__(self, pixmap, parent=None, *, source_regions=()) -> None:
            opened["valid"] = not pixmap.isNull()
            opened["regions"] = list(source_regions)

        def exec(self) -> None:
            opened["executed"] = True

    monkeypatch.setattr(note_page_module, "ImageViewerDialog", _ViewerStub)
    note_page.reload(select_note_id=note.id)

    assert not note_page.original_button.isHidden()
    assert note_page.original_button.isEnabled()
    note_page._open_original()
    assert opened == {
        "valid": True,
        "regions": [
            {"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.4}
        ],
        "executed": True,
    }
