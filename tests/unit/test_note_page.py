"""Smoke coverage for the in-shell note reader and block editor."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from yancuo_win.application.bootstrap import bootstrap_runtime
from yancuo_win.application.note_service import NoteService
from yancuo_win.config.settings import default_toml_path
from yancuo_win.ui.note_page import NotePage


@pytest.fixture()
def note_page(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> NotePage:
    monkeypatch.setenv("YANCUO_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("YANCUO_CONFIG_FILE", str(default_toml_path()))
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
    assert "极限笔记" in note_page.reader.toHtml()
    assert "sin x" in note_page.reader.toHtml()


def test_note_page_moves_a_note_to_the_recycle_bin(note_page: NotePage) -> None:
    note_page._create_note()
    assert note_page._note is not None

    note_page._set_note_status("trashed")

    assert note_page._note is not None
    assert note_page._note.status == "trashed"
    assert not note_page.restore_button.isHidden()
