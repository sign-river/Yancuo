"""Classification-row edits keep recoverable note draft blocks intact."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from yancuo_win.application.bootstrap import bootstrap_runtime
from yancuo_win.application.note_intake_service import (
    NoteDraftBlockInput,
    NoteDraftGroupInput,
    NoteIntakeService,
)
from yancuo_win.application.services import AppServices
from yancuo_win.config.settings import default_toml_path
from yancuo_win.data.models import NoteDocument
from yancuo_win.domain.rules import DomainError


@pytest.fixture()
def intake_service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("YANCUO_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("YANCUO_CONFIG_FILE", str(default_toml_path()))
    image = tmp_path / "source.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\nclassification-draft")
    runtime = bootstrap_runtime()
    return AppServices(runtime), NoteIntakeService(runtime), image


def test_update_group_preserves_blocks_and_accepts_existing_path(intake_service) -> None:
    app, service, image = intake_service
    subject = app.create_subject("math")
    chapter = app.create_chapter(subject.id, "limits")
    intake = service.start_session([image], classification_mode="custom")
    saved = service.save_extraction(
        intake.id,
        metadata={},
        groups=[
            NoteDraftGroupInput(
                title="suggested",
                blocks=(
                    NoteDraftBlockInput(
                        block_type="concept",
                        content_markdown="equivalent infinitesimals",
                        source_asset_id=intake.assets[0].id,
                        source_region={"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.4},
                        uncertain_fields=[{"field": "content", "reason": "blur"}],
                    ),
                ),
            )
        ],
    )

    updated = service.update_group(
        saved.id,
        saved.groups[0].id,
        title="limits",
        summary="",
        category_resolution="existing",
        subject_id=subject.id,
        chapter_id=chapter.id,
    )

    group = updated.groups[0]
    assert (group.title, group.category_resolution) == ("limits", "existing")
    assert (group.subject_id, group.chapter_id) == (subject.id, chapter.id)
    assert group.blocks[0].content_markdown == "equivalent infinitesimals"
    assert json.loads(group.blocks[0].source_region_json)["width"] == 0.3
    assert json.loads(group.blocks[0].uncertain_json) == [{"field": "content", "reason": "blur"}]


def test_group_add_delete_and_merge_preserve_draft_blocks(intake_service) -> None:
    _app, service, image = intake_service
    intake = service.start_session([image], classification_mode="custom")
    saved = service.save_extraction(
        intake.id,
        metadata={},
        groups=[
            NoteDraftGroupInput(
                title="first",
                blocks=(NoteDraftBlockInput(block_type="text", content_markdown="one"),),
            ),
            NoteDraftGroupInput(
                title="second",
                blocks=(NoteDraftBlockInput(block_type="formula", content_latex="x^2"),),
            ),
        ],
    )
    first, second = saved.groups
    with pytest.raises(DomainError, match="不能删除"):
        service.delete_group(saved.id, first.id)

    merged = service.merge_groups(
        saved.id, source_group_id=first.id, target_group_id=second.id
    )
    assert [group.title for group in merged.groups] == ["second"]
    assert [block.content_markdown or block.content_latex for block in merged.groups[0].blocks] == [
        "x^2",
        "one",
    ]
    empty = service.add_group(merged.id, title="empty")
    assert [group.sort_order for group in empty.groups] == [1, 2]
    deleted = service.delete_group(empty.id, empty.groups[-1].id)
    assert [group.title for group in deleted.groups] == ["second"]


def test_new_category_requires_proposal_and_rejects_foreign_group(intake_service) -> None:
    _app, service, image = intake_service
    first = service.start_session([image], classification_mode="custom")
    second = service.start_session([image], classification_mode="custom")
    first = service.save_extraction(first.id, metadata={}, groups=[NoteDraftGroupInput()])
    second = service.save_extraction(second.id, metadata={}, groups=[NoteDraftGroupInput()])

    with pytest.raises(DomainError, match="新分类"):
        service.update_group(
            first.id,
            first.groups[0].id,
            title="",
            summary="",
            category_resolution="create_new",
        )
    with pytest.raises(DomainError, match="不属于"):
        service.update_group(
            first.id,
            second.groups[0].id,
            title="",
            summary="",
            category_resolution="unresolved",
        )

    updated = service.update_group(
        first.id,
        first.groups[0].id,
        title="proposal",
        summary="",
        category_resolution="create_new",
        proposed_subject="new subject",
        proposed_chapter="new chapter",
    )
    assert updated.groups[0].category_resolution == "create_new"
    assert updated.groups[0].proposed_subject == "new subject"


def test_move_block_reorders_within_and_across_groups_without_losing_metadata(
    intake_service,
) -> None:
    _app, service, image = intake_service
    intake = service.start_session([image], classification_mode="custom")
    saved = service.save_extraction(
        intake.id,
        metadata={},
        groups=[
            NoteDraftGroupInput(
                title="first",
                blocks=(
                    NoteDraftBlockInput(block_type="text", content_markdown="one"),
                    NoteDraftBlockInput(
                        block_type="concept",
                        content_markdown="two",
                        source_asset_id=intake.assets[0].id,
                        source_region={"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.4},
                        uncertain_fields=[{"field": "content", "reason": "blur"}],
                    ),
                    NoteDraftBlockInput(block_type="formula", content_latex="x^2"),
                ),
            ),
            NoteDraftGroupInput(title="second"),
        ],
    )
    first, second = saved.groups
    moved = service.move_block(
        saved.id,
        first.blocks[0].id,
        target_group_id=first.id,
        target_index=3,
    )
    assert [block.content_markdown or block.content_latex for block in moved.groups[0].blocks] == [
        "two",
        "x^2",
        "one",
    ]

    moved = service.move_block(
        moved.id,
        moved.groups[0].blocks[0].id,
        target_group_id=second.id,
        target_index=0,
    )
    assert [block.content_markdown or block.content_latex for block in moved.groups[0].blocks] == [
        "x^2",
        "one",
    ]
    target_block = moved.groups[1].blocks[0]
    assert target_block.content_markdown == "two"
    assert json.loads(target_block.source_region_json)["width"] == 0.3
    assert json.loads(target_block.uncertain_json) == [{"field": "content", "reason": "blur"}]
    assert [block.sort_order for group in moved.groups for block in group.blocks] == [0, 1, 0]


def test_move_block_rejects_foreign_block_and_invalid_position(intake_service) -> None:
    _app, service, image = intake_service
    first = service.start_session([image], classification_mode="custom")
    second = service.start_session([image], classification_mode="custom")
    first = service.save_extraction(
        first.id,
        metadata={},
        groups=[NoteDraftGroupInput(blocks=(NoteDraftBlockInput(block_type="text"),))],
    )
    second = service.save_extraction(second.id, metadata={}, groups=[NoteDraftGroupInput()])

    with pytest.raises(DomainError, match="不属于"):
        service.move_block(
            first.id,
            first.groups[0].blocks[0].id,
            target_group_id=second.groups[0].id,
        )
    with pytest.raises(DomainError, match="目标位置"):
        service.move_block(
            first.id,
            first.groups[0].blocks[0].id,
            target_group_id=first.groups[0].id,
            target_index=2,
        )


def test_confirm_groups_creates_independent_notes_with_shared_source(intake_service) -> None:
    app, service, image = intake_service
    subject = app.create_subject("math")
    chapter = app.create_chapter(subject.id, "limits")
    tag = app.create_tag("important")
    intake = service.start_session([image], classification_mode="ai")
    saved = service.save_extraction(
        intake.id,
        metadata={"title": "fallback"},
        groups=[
            NoteDraftGroupInput(
                title="existing",
                category_resolution="existing",
                subject_id=subject.id,
                chapter_id=chapter.id,
                target_status="active",
                tag_ids=(tag.id,),
                blocks=(NoteDraftBlockInput(block_type="concept", content_markdown="one"),),
            ),
            NoteDraftGroupInput(
                title="unresolved",
                blocks=(
                    NoteDraftBlockInput(
                        block_type="formula",
                        content_latex="x^2",
                        source_region={"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.4},
                        uncertain_fields=[{"field": "latex", "reason": "blur"}],
                    ),
                ),
            ),
            NoteDraftGroupInput(title="empty"),
        ],
    )

    notes = service.confirm_groups(saved.id)

    assert [note.title for note in notes] == ["existing", "unresolved"]
    assert [note.status for note in notes] == ["active", "inbox"]
    assert (notes[0].subject_id, notes[0].chapter_id) == (subject.id, chapter.id)
    assert notes[0].tags[0].id == tag.id
    assert notes[0].assets[0].relative_path == notes[1].assets[0].relative_path
    assert notes[0].assets[0].sha256 == notes[1].assets[0].sha256
    assert json.loads(notes[1].blocks[0].source_region_json)["width"] == 0.3
    assert json.loads(notes[1].blocks[0].uncertain_json)[0]["field"] == "latex"
    assert service.get_session(saved.id).status == "completed"
    assert [group.note_document_id is not None for group in service.get_session(saved.id).groups] == [
        True,
        True,
        False,
    ]
    with service.runtime.session_factory() as session:
        assert session.query(NoteDocument).count() == 2


def test_confirm_groups_is_atomic_when_a_category_is_invalid(intake_service) -> None:
    _app, service, image = intake_service
    intake = service.start_session([image], classification_mode="custom")
    saved = service.save_extraction(
        intake.id,
        metadata={},
        groups=[
            NoteDraftGroupInput(blocks=(NoteDraftBlockInput(block_type="text"),)),
            NoteDraftGroupInput(
                category_resolution="create_new",
                proposed_subject="new subject",
                blocks=(NoteDraftBlockInput(block_type="text"),),
            ),
        ],
    )
    with service.runtime.session_factory() as session:
        session.get(type(saved.groups[1]), saved.groups[1].id).target_status = "trashed"
        session.commit()

    with pytest.raises(DomainError, match="目标状态"):
        service.confirm_groups(saved.id)
    assert service.get_session(saved.id).status == "review"
    with service.runtime.session_factory() as session:
        assert session.query(NoteDocument).count() == 0
