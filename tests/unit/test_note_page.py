"""Smoke coverage for the in-shell note reader and block editor."""

from __future__ import annotations

import json
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
from yancuo_win.application.note_intake_service import (
    NoteDraftBlockInput,
    NoteDraftGroupInput,
)
from yancuo_win.application.note_service import NoteService
from yancuo_win.config.settings import default_toml_path
from yancuo_win.ui.note_page import NotePage
from yancuo_win.ui.widgets import ReadingCanvas, SoftItemDelegate


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


def test_note_library_uses_space_and_expanded_list_hierarchy(note_page: NotePage) -> None:
    first = note_page.notes.create_note(title="第一篇", status="active")
    note_page.notes.create_note(title="第二篇", status="active")

    note_page.reload(select_note_id=first.id)

    assert note_page.workspace.count() == 2
    assert note_page.workspace.handleWidth() == 10
    assert note_page.workspace.contentsMargins().left() == 8
    assert isinstance(note_page.collection_list.itemDelegate(), SoftItemDelegate)
    assert isinstance(note_page.note_list.itemDelegate(), SoftItemDelegate)
    assert note_page.collection_list.item(0).text() == "全部笔记"
    assert note_page.collection_list.item(1).text() == "未归入合集"
    assert note_page.note_count_label.text() == "2 篇"
    assert note_page.mode_stack.currentIndex() == 1
    assert note_page.read_button.isHidden()
    assert not note_page.edit_button.isHidden()
    assert isinstance(note_page.reading_canvas, ReadingCanvas)
    assert note_page.reading_canvas.content is note_page.reader
    assert note_page.reading_canvas.maximum_content_width == 920
    assert note_page.more_button.text() == "更多"
    assert not note_page.more_button.icon().isNull()
    assert note_page.collection_list.uniformItemSizes()
    assert note_page.note_list.uniformItemSizes()
    assert note_page.collection_list.accessibleName() == "笔记合集"
    assert note_page.note_list.accessibleName() == "笔记列表"


def test_note_detail_is_opened_by_double_click_and_restores_library_state(
    note_page: NotePage,
) -> None:
    notes = [
        note_page.notes.create_note(title=f"note {index}", status="active")
        for index in range(9)
    ]
    collection = note_page.notes.create_collection("calculus")
    note_page.notes.set_note_collections(notes[4].id, [collection.id])
    note_page.reload(select_note_id=notes[4].id)
    collection_item = next(
        note_page.collection_list.item(row)
        for row in range(note_page.collection_list.count())
        if note_page.collection_list.item(row).data(Qt.ItemDataRole.UserRole)
        == collection.id
    )
    note_page.collection_list.setCurrentItem(collection_item)
    item = note_page.note_list.item(0)
    note_page._open_selected_note_detail(item)

    assert note_page.page_stack.currentWidget() is note_page.note_detail_page
    assert note_page.detail_back_button.accessibleName() == "返回笔记库"
    assert "collection_scroll" in note_page._library_state

    note_page._return_to_library()

    assert note_page.page_stack.currentWidget() is note_page.library_page
    assert note_page.collection_list.currentItem().data(Qt.ItemDataRole.UserRole) == collection.id
    assert note_page.note_list.currentItem().data(Qt.ItemDataRole.UserRole) == notes[4].id


def test_note_secondary_pages_use_icon_back_entries(note_page: NotePage) -> None:
    note_page._show_manual_create()
    assert isinstance(note_page.manual_create_page.findChild(note_page_module.IconButton), note_page_module.IconButton)
    note_page._show_ai_intake()
    assert isinstance(note_page.ai_intake_page.findChild(note_page_module.IconButton), note_page_module.IconButton)


def test_empty_note_filter_explains_the_next_action(note_page: NotePage) -> None:
    note_page.reload()

    assert note_page.note_list.count() == 0
    assert not note_page.note_list_hint.isHidden()
    assert "暂无笔记" in note_page.note_list_hint.text()


def test_note_library_uses_state_specific_empty_feedback_and_no_horizontal_lists(
    note_page: NotePage,
) -> None:
    note_page.status_filter.setCurrentIndex(
        note_page.status_filter.findData("trashed")
    )

    assert "回收站为空" in note_page.note_list_hint.text()
    assert (
        note_page.note_list.horizontalScrollBarPolicy()
        == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    )
    assert (
        note_page.collection_list.horizontalScrollBarPolicy()
        == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    )
    assert not note_page.collection_list_hint.isHidden()


def test_note_library_narrow_breakpoint_keeps_list_and_discloses_space(
    note_page: NotePage,
) -> None:
    app = QApplication.instance()
    assert app is not None

    note_page.show()
    note_page.resize(760, 640)
    app.processEvents()

    assert note_page._narrow_layout
    assert note_page.space_pane.isHidden()
    assert not note_page.note_library_pane.isHidden()
    assert note_page.workspace.count() == 2
    assert isinstance(note_page.space_back_button, note_page_module.IconButton)


def test_narrow_note_layout_discloses_space_separately(note_page: NotePage) -> None:
    app = QApplication.instance()
    assert app is not None

    note_page._set_narrow_layout(True)
    app.processEvents()
    assert note_page.space_pane.isHidden()
    assert not note_page.note_library_pane.isHidden()
    assert note_page.workspace.count() == 2
    assert not note_page.space_toggle_button.isHidden()

    note_page._show_narrow_space()
    app.processEvents()
    assert not note_page.space_pane.isHidden()
    assert note_page.note_library_pane.isHidden()

    note_page._show_narrow_content()
    assert note_page.space_pane.isHidden()


def test_note_collection_navigation_filters_the_middle_list(note_page: NotePage) -> None:
    included = note_page.notes.create_note(title="合集内", status="active")
    note_page.notes.create_note(title="合集外", status="active")
    collection = note_page.notes.create_collection("高数")
    note_page.notes.set_note_collections(included.id, [collection.id])
    note_page.reload()

    collection_item = next(
        note_page.collection_list.item(row)
        for row in range(note_page.collection_list.count())
        if note_page.collection_list.item(row).data(Qt.ItemDataRole.UserRole)
        == collection.id
    )
    note_page.collection_list.setCurrentItem(collection_item)

    assert note_page.note_list.count() == 1
    assert "合集内" in note_page.note_list.item(0).text()
    assert note_page.note_count_label.text() == "1 篇"


def test_note_editor_discloses_content_and_metadata_separately(
    note_page: NotePage,
) -> None:
    note_page._create_note()

    assert note_page.mode_stack.currentIndex() == 0
    assert note_page.editor_section_stack.currentIndex() == 0
    assert note_page.edit_button.isHidden()
    assert not note_page.read_button.isHidden()

    note_page._set_editor_section("info")
    assert note_page.editor_section_stack.currentIndex() == 1

    note_page._set_mode("read")
    assert note_page.mode_stack.currentIndex() == 1
    assert note_page.read_button.isHidden()
    assert not note_page.edit_button.isHidden()


def test_note_page_reorders_blocks_and_disables_invalid_directions(note_page: NotePage) -> None:
    note_page._create_note()
    note_page._add_block("text")
    note_page._add_block("formula")
    assert note_page._note is not None
    original_ids = [block.id for block in note_page._note.blocks]

    note_page.block_list.setCurrentRow(0)
    assert not note_page.move_block_up_button.isEnabled()
    assert note_page.move_block_down_button.isEnabled()
    note_page._persist_block_order(list(reversed(original_ids)))

    reloaded = note_page.notes.get_note(note_page._note.id)
    assert reloaded is not None
    assert [block.id for block in reloaded.blocks] == list(reversed(original_ids))


def test_note_page_reveals_bulk_actions_only_for_multiple_selection(note_page: NotePage) -> None:
    first = note_page.notes.create_note(title="first", status="active")
    note_page.notes.create_note(title="second", status="active")
    note_page.reload(select_note_id=first.id)

    note_page.note_list.item(0).setSelected(True)
    note_page.note_list.item(1).setSelected(True)

    assert not note_page.bulk_actions.isHidden()
    assert "2" in note_page.bulk_selection_label.text()


def test_note_intake_actions_open_dedicated_workflow_pages(note_page: NotePage) -> None:
    note_page._show_manual_create()
    assert note_page.page_stack.currentWidget() is note_page.manual_create_page

    note_page._show_ai_intake()
    assert note_page.page_stack.currentWidget() is note_page.ai_intake_page

    note_page._show_library()
    assert note_page.page_stack.currentWidget() is note_page.library_page


def test_note_page_moves_a_note_to_the_recycle_bin(note_page: NotePage) -> None:
    note_page._create_note()
    assert note_page._note is not None

    note_page._set_note_status("trashed")

    assert note_page._note is None
    assert note_page.note_list.count() == 0
    assert note_page.status_filter.currentData() == "active"


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


def test_draft_preview_moves_a_block_between_groups(note_page: NotePage) -> None:
    source_path = note_page.notes.runtime.paths.root / "draft-source.png"
    source = QPixmap(24, 24)
    source.fill(Qt.GlobalColor.white)
    assert source.save(str(source_path))
    intake = note_page.note_intake.start_session([source_path], classification_mode="custom")
    intake = note_page.note_intake.save_extraction(
        intake.id,
        metadata={},
        groups=[
            NoteDraftGroupInput(
                title="first",
                blocks=(
                    NoteDraftBlockInput(
                        block_type="concept",
                        content_markdown="keep source metadata",
                        source_asset_id=intake.assets[0].id,
                        source_region={"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.4},
                        uncertain_fields=[{"field": "content", "reason": "blur"}],
                    ),
                ),
            ),
            NoteDraftGroupInput(title="second"),
        ],
    )
    dialog = note_page_module.NoteDraftPreviewPage(intake, note_page.note_intake)
    dialog._move_block(intake.groups[0].blocks[0].id, intake.groups[1].id, 0)

    assert dialog.groups.topLevelItem(0).childCount() == 0
    assert dialog.groups.topLevelItem(1).childCount() == 1
    moved = dialog.intake.groups[1].blocks[0]
    assert moved.content_markdown == "keep source metadata"
    assert json.loads(moved.source_region_json)["width"] == 0.3
    assert json.loads(moved.uncertain_json) == [{"field": "content", "reason": "blur"}]
    dialog.close()


def test_draft_preview_supports_compact_concept_grid_and_context_move(note_page: NotePage) -> None:
    source_path = note_page.notes.runtime.paths.root / "concept-grid-source.png"
    source = QPixmap(24, 24)
    source.fill(Qt.GlobalColor.white)
    assert source.save(str(source_path))
    intake = note_page.note_intake.start_session([source_path], classification_mode="custom")
    intake = note_page.note_intake.save_extraction(
        intake.id,
        metadata={},
        groups=[
            NoteDraftGroupInput(
                title="first",
                blocks=(
                    NoteDraftBlockInput(block_type="concept", content_markdown="short concept"),
                    NoteDraftBlockInput(block_type="concept", content_markdown="long " * 30),
                ),
            ),
            NoteDraftGroupInput(title="second"),
        ],
    )
    dialog = note_page_module.NoteDraftPreviewPage(intake, note_page.note_intake)

    dialog.block_layout.setCurrentIndex(1)
    assert dialog.block_views.currentWidget() is dialog.concept_grid
    assert dialog.concept_grid.count() == 2
    assert dialog.concept_grid.item(0).sizeHint().width() < dialog.concept_grid.item(1).sizeHint().width()

    dialog._move_block_to_group(intake.groups[0].blocks[0].id, intake.groups[1].id)

    assert [block.content_markdown for block in dialog.intake.groups[1].blocks] == ["short concept"]
    dialog.close()


def test_draft_preview_allows_editing_a_block_sequence(note_page: NotePage) -> None:
    source_path = note_page.notes.runtime.paths.root / "sequence-source.png"
    source = QPixmap(24, 24)
    source.fill(Qt.GlobalColor.white)
    assert source.save(str(source_path))
    intake = note_page.note_intake.start_session([source_path], classification_mode="custom")
    intake = note_page.note_intake.save_extraction(
        intake.id,
        metadata={},
        groups=[
            NoteDraftGroupInput(
                title="first",
                blocks=(
                    NoteDraftBlockInput(block_type="concept", content_markdown="one"),
                    NoteDraftBlockInput(block_type="concept", content_markdown="two"),
                ),
            )
        ],
    )
    dialog = note_page_module.NoteDraftPreviewPage(intake, note_page.note_intake)

    second = dialog.groups.topLevelItem(0).child(1)
    assert second.flags() & Qt.ItemFlag.ItemIsEditable
    second.setText(1, "1")

    assert [block.content_markdown for block in dialog.intake.groups[0].blocks] == ["two", "one"]
    dialog.close()
