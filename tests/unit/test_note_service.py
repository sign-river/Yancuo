"""Independent local note document and block lifecycle tests."""

from __future__ import annotations

from pathlib import Path

import pytest

import yancuo_win.application.unified_search_service as unified_search_module
from yancuo_win.application.bootstrap import bootstrap_runtime
from yancuo_win.application.note_service import NoteService
from yancuo_win.application.services import AppServices
from yancuo_win.application.unified_search_service import UnifiedSearchIndexService
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


def test_note_list_projection_uses_summary_or_first_block_without_relationships(
    note_bundle,
) -> None:
    _runtime, _app, notes, subject, chapter = note_bundle
    summarized = notes.create_note(
        title="摘要笔记",
        summary="列表摘要",
        subject_id=subject.id,
        chapter_id=chapter.id,
        status="active",
    )
    block_only = notes.create_note(title="公式笔记", status="active")
    notes.add_block(
        block_only.id,
        block_type="formula",
        content_latex=r"x^2+1",
    )

    rows = notes.list_note_summaries(status="active")
    by_id = {row.id: row for row in rows}

    assert by_id[summarized.id].summary == "列表摘要"
    assert by_id[block_only.id].summary == r"x^2+1"
    assert by_id[summarized.id].subject_id == subject.id
    assert not hasattr(by_id[summarized.id], "blocks")


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


def test_unified_note_search_tracks_blocks_tags_and_collections(note_bundle) -> None:
    runtime, app, notes, _subject, _chapter = note_bundle
    tag = app.create_tag("泰勒")
    note = notes.create_note(title="极限")
    notes.add_block(note.id, block_type="concept", content_markdown="等价无穷小")
    notes.set_tags(note.id, [tag.id])
    collection = notes.create_collection("冲刺")
    notes.set_note_collections(note.id, [collection.id])

    search = UnifiedSearchIndexService(runtime)
    assert [row["entity_id"] for row in search.search_notes("无穷小", statuses=("inbox",))] == [note.id]
    assert [row["entity_id"] for row in search.search_notes("冲刺", statuses=("inbox",))] == [note.id]


def test_unified_note_search_rebuilds_in_bounded_batches(
    note_bundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, _app, notes, _subject, _chapter = note_bundle
    for index in range(5):
        notes.create_note(title=f"分批索引 {index}")
    inserted_batch_sizes: list[int] = []
    original_insert = UnifiedSearchIndexService._insert_documents

    def record_insert(session, documents):
        inserted_batch_sizes.append(len(documents))
        original_insert(session, documents)

    monkeypatch.setattr(unified_search_module, "_NOTE_REBUILD_BATCH_SIZE", 2)
    monkeypatch.setattr(
        UnifiedSearchIndexService, "_insert_documents", staticmethod(record_insert)
    )

    search = UnifiedSearchIndexService(runtime)
    assert search.rebuild_notes() == 5
    assert inserted_batch_sizes == [2, 2, 1]
    assert len(search.search_notes("分批索引", statuses=("inbox",))) == 5


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


def test_personal_collections_are_independent_from_knowledge_categories(note_bundle) -> None:
    _runtime, _app, notes, _subject, _chapter = note_bundle
    first = notes.create_note(title="极限错因")
    second = notes.create_note(title="本周总结")
    collection = notes.create_collection("考研冲刺", "最后两周复盘")

    notes.set_note_collections(first.id, [collection.id])
    notes.set_note_collections(second.id, [collection.id])

    assert [item.title for item in notes.get_note(first.id).collections] == ["考研冲刺"]
    assert [item.title for item in notes.list_collections()] == ["考研冲刺"]
    assert {note.id for note in notes.list_collections()[0].notes} == {first.id, second.id}
    notes.delete_collection(collection.id)
    assert notes.get_note(first.id) is not None
    assert notes.get_note(second.id) is not None
    assert notes.list_collections() == []


def test_note_collection_validates_titles_and_membership(note_bundle) -> None:
    _runtime, _app, notes, _subject, _chapter = note_bundle
    note = notes.create_note(title="边界")
    with pytest.raises(DomainError, match="不能为空"):
        notes.create_collection("  ")
    collection = notes.create_collection("复盘")
    with pytest.raises(DomainError, match="同名"):
        notes.create_collection("复盘")
    with pytest.raises(DomainError, match="重复"):
        notes.set_note_collections(note.id, [collection.id, collection.id])
    notes.trash_note(note.id)
    with pytest.raises(DomainError, match="不可编辑"):
        notes.set_note_collections(note.id, [collection.id])


def test_bulk_note_actions_commit_a_complete_selection(note_bundle) -> None:
    _runtime, _app, notes, _subject, _chapter = note_bundle
    first = notes.create_note(title="first", status="active")
    second = notes.create_note(title="second", status="active")
    collection = notes.create_collection("batch")

    moved = notes.move_notes_to_collection([first.id, second.id], collection.id)

    assert [note.id for note in moved] == [first.id, second.id]
    assert [item.id for item in notes.get_note(first.id).collections] == [collection.id]
    assert [item.id for item in notes.get_note(second.id).collections] == [collection.id]
    archived = notes.update_notes_status([first.id, second.id], "archived")
    assert [note.status for note in archived] == ["archived", "archived"]


def test_bulk_note_action_does_not_partially_apply_invalid_selection(note_bundle) -> None:
    _runtime, _app, notes, _subject, _chapter = note_bundle
    editable = notes.create_note(title="editable", status="active")
    trashed = notes.create_note(title="trashed", status="active")
    notes.trash_note(trashed.id)
    collection = notes.create_collection("batch")

    with pytest.raises(DomainError, match="不可编辑"):
        notes.move_notes_to_collection([editable.id, trashed.id], collection.id)

    assert notes.get_note(editable.id).collections == []
