"""Independent local note document and block lifecycle tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from yancuo_win.application.bootstrap import bootstrap_runtime
from yancuo_win.application.note_service import NoteService
from yancuo_win.application.services import AppServices
from yancuo_win.config.settings import default_toml_path
from yancuo_win.domain.rules import DomainError


@pytest.fixture()
def note_bundle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("YANCUO_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("YANCUO_CONFIG_FILE", str(default_toml_path()))
    runtime = bootstrap_runtime()
    app = AppServices(runtime)
    subject = app.create_subject("高等数学")
    chapter = app.create_chapter(subject.id, "极限")
    return runtime, app, NoteService(runtime), subject, chapter


def test_note_documents_keep_independent_fields_and_ordered_blocks(note_bundle) -> None:
    _runtime, app, notes, subject, chapter = note_bundle
    problem = app.create_problem(title="题目不能被笔记影响", status="active")
    note = notes.create_note(
        title="等价无穷小笔记",
        summary="整理泰勒展开",
        subject_id=subject.id,
        chapter_id=chapter.id,
    )
    first = notes.add_block(
        note.id,
        block_type="formula",
        content_latex=r"\\sin x \\sim x",
    )
    second = notes.add_block(
        note.id,
        block_type="concept",
        content_markdown="小角近似的适用条件。",
        source_region={"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.4},
    )
    notes.reorder_blocks(note.id, [second.id, first.id])
    loaded = notes.get_note(note.id)

    assert [block.id for block in loaded.blocks] == [second.id, first.id]
    assert [block.sort_order for block in loaded.blocks] == [0, 1]
    assert loaded.subject_id == subject.id
    assert loaded.chapter_id == chapter.id
    assert loaded.blocks[0].block_type == "concept"
    assert '"width":0.3' in loaded.blocks[0].source_region_json
    assert app.get_problem(problem.id).title == "题目不能被笔记影响"
    assert not hasattr(loaded, "correct_answer")
    assert not hasattr(loaded, "review_count")


def test_note_tags_and_lifecycle_protect_trashed_documents(note_bundle) -> None:
    _runtime, app, notes, _subject, _chapter = note_bundle
    tag = app.create_tag("泰勒展开")
    note = notes.create_note(title="公式")
    notes.set_tags(note.id, [tag.id])
    assert [item.name for item in notes.get_note(note.id).tags] == ["泰勒展开"]

    notes.update_note(note.id, {"status": "active"})
    notes.trash_note(note.id)
    with pytest.raises(DomainError, match="不可编辑"):
        notes.add_block(note.id, block_type="text", content_markdown="不允许")
    with pytest.raises(DomainError, match="不可编辑"):
        notes.set_tags(note.id, [tag.id])
    restored = notes.restore_note(note.id)
    assert restored.status == "active"

    notes.trash_note(note.id)
    notes.delete_note_permanently(note.id)
    assert notes.get_note(note.id) is None


def test_delete_note_block_compacts_the_remaining_order(note_bundle) -> None:
    _runtime, _app, notes, _subject, _chapter = note_bundle
    note = notes.create_note(title="块删除")
    first = notes.add_block(note.id, block_type="text", content_markdown="第一块")
    second = notes.add_block(note.id, block_type="text", content_markdown="第二块")

    notes.delete_block(first.id)

    remaining = notes.get_note(note.id)
    assert remaining is not None
    assert [block.id for block in remaining.blocks] == [second.id]
    assert remaining.blocks[0].sort_order == 0


def test_note_block_validation_and_reorder_must_be_complete(note_bundle) -> None:
    _runtime, _app, notes, _subject, _chapter = note_bundle
    note = notes.create_note(title="边界")
    first = notes.add_block(note.id, block_type="heading", content_markdown="标题")
    second = notes.add_block(note.id, block_type="callout", content_markdown="提示")

    with pytest.raises(DomainError, match="不支持"):
        notes.add_block(note.id, block_type="answer")
    with pytest.raises(DomainError, match="完整包含"):
        notes.reorder_blocks(note.id, [first.id])
    with pytest.raises(DomainError, match="完整包含"):
        notes.reorder_blocks(note.id, [first.id, first.id])

    notes.update_block(second.id, {"content_markdown": "更新"})
    assert notes.get_note(note.id).blocks[1].content_markdown == "更新"
