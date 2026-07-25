"""Recoverable note-intake drafts remain outside the formal note library."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import func, select

from yancuo_win.application.bootstrap import bootstrap_runtime
from yancuo_win.application.note_ai_service import (
    NoteAiService,
    NoteBlockDraft,
    NoteDraftGroupDraft,
    NoteExtractionDraft,
)
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
def note_intake_bundle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    data_root = tmp_path / "data"
    monkeypatch.setenv("YANCUO_DATA_ROOT", str(data_root))
    monkeypatch.setenv("YANCUO_CONFIG_FILE", str(default_toml_path()))
    runtime = bootstrap_runtime()
    app = AppServices(runtime)
    service = NoteIntakeService(runtime)
    image = tmp_path / "source.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\nrecoverable-note-source")
    return data_root, runtime, app, service, image


def test_note_draft_survives_restart_with_groups_blocks_and_source(
    note_intake_bundle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root, runtime, app, service, image = note_intake_bundle
    subject = app.create_subject("高等数学")
    chapter = app.create_chapter(subject.id, "极限")
    intake = service.start_session(
        [image],
        classification_mode="ai",
        user_instruction="红笔内容整理为提示",
    )
    source_asset_id = intake.assets[0].id

    saved = service.save_extraction(
        intake.id,
        metadata={"title": "极限方法", "tags": ["易错"]},
        groups=[
            NoteDraftGroupInput(
                title="求极限",
                category_resolution="existing",
                subject_id=subject.id,
                chapter_id=chapter.id,
                proposal={"reason": "匹配现有完整路径"},
                blocks=(
                    NoteDraftBlockInput(
                        block_type="concept",
                        content_markdown="等价无穷小需要满足自变量趋于零。",
                        source_asset_id=source_asset_id,
                        source_region={
                            "x": 0.1,
                            "y": 0.2,
                            "width": 0.4,
                            "height": 0.3,
                        },
                        uncertain_fields=[{"field": "content", "reason": "字迹浅"}],
                    ),
                    NoteDraftBlockInput(
                        block_type="formula",
                        content_latex=r"\sin x \sim x",
                        source_asset_id=source_asset_id,
                    ),
                ),
            ),
            NoteDraftGroupInput(title="待分类空组"),
        ],
    )
    assert saved.status == "review"
    assert [group.sort_order for group in saved.groups] == [0, 1]
    assert [block.sort_order for block in saved.groups[0].blocks] == [0, 1]
    with runtime.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(NoteDocument)) == 0

    image.unlink()
    runtime.engine.dispose()
    monkeypatch.setenv("YANCUO_DATA_ROOT", str(data_root))
    restarted = bootstrap_runtime()
    recovered = NoteIntakeService(restarted).get_session(intake.id)

    assert recovered is not None
    assert recovered.user_instruction == "红笔内容整理为提示"
    assert json.loads(recovered.draft_meta_json)["tags"] == ["易错"]
    assert [group.title for group in recovered.groups] == ["求极限", "待分类空组"]
    first = recovered.groups[0].blocks[0]
    assert first.content_markdown.startswith("等价无穷小")
    assert json.loads(first.source_region_json)["width"] == 0.4
    assert json.loads(first.uncertain_json)[0]["reason"] == "字迹浅"
    assert NoteIntakeService(restarted).resolve_source_path(recovered.assets[0]).is_file()


def test_flat_ai_draft_can_be_saved_as_one_unresolved_group(
    note_intake_bundle,
) -> None:
    _data_root, runtime, _app, service, image = note_intake_bundle
    runtime.settings.ai.enabled = True
    runtime.settings.ai.default_provider = "mock"
    extraction = NoteAiService(runtime).extract_from_image(image)
    intake = service.start_session([image], classification_mode="custom")

    saved = service.save_flat_draft(intake.id, extraction)

    assert saved.status == "review"
    assert len(saved.groups) == 1
    assert saved.groups[0].category_resolution == "unresolved"
    assert json.loads(saved.groups[0].proposed_tags_json) == extraction.tags
    assert [block.block_type for block in saved.groups[0].blocks] == [
        block.block_type for block in extraction.blocks
    ]


def test_ai_mode_persists_multiple_non_authoritative_groups(note_intake_bundle) -> None:
    _data_root, _runtime, _app, service, image = note_intake_bundle
    intake = service.start_session([image], classification_mode="ai")
    draft = NoteExtractionDraft(
        source_path=image,
        title="导数与极限",
        summary="",
        blocks=[],
        groups=[
            NoteDraftGroupDraft(
                title="极限",
                tags=["基础"],
                reason="同一求极限方法",
                blocks=[NoteBlockDraft("concept", content_markdown="等价无穷小")],
            ),
            NoteDraftGroupDraft(
                title="导数",
                blocks=[NoteBlockDraft("formula", content_latex=r"(x^n)'=nx^{n-1}")],
            ),
        ],
    )

    saved = service.save_grouped_draft(intake.id, draft)

    assert saved.status == "review"
    assert [group.title for group in saved.groups] == ["极限", "导数"]
    assert all(group.category_resolution == "unresolved" for group in saved.groups)
    assert all(group.target_status == "inbox" for group in saved.groups)
    assert json.loads(saved.groups[0].proposal_json) == {"reason": "同一求极限方法"}


def test_custom_mode_ignores_ai_groups_and_keeps_one_flat_group(note_intake_bundle) -> None:
    _data_root, _runtime, _app, service, image = note_intake_bundle
    intake = service.start_session([image], classification_mode="custom")
    draft = NoteExtractionDraft(
        source_path=image,
        title="人工分类",
        summary="",
        blocks=[NoteBlockDraft("concept", content_markdown="由用户决定分类")],
        groups=[
            NoteDraftGroupDraft(
                title="模型不应决定的分类",
                blocks=[NoteBlockDraft("text", content_markdown="忽略")],
            )
        ],
    )

    saved = service.save_grouped_draft(intake.id, draft)

    assert len(saved.groups) == 1
    assert saved.groups[0].category_resolution == "unresolved"
    assert saved.groups[0].target_status == "inbox"
    assert [block.content_markdown for block in saved.groups[0].blocks] == ["由用户决定分类"]


def test_invalid_snapshot_keeps_previous_groups_unchanged(note_intake_bundle) -> None:
    _data_root, _runtime, _app, service, image = note_intake_bundle
    first = service.start_session([image], classification_mode="custom")
    second = service.start_session([image], classification_mode="custom")
    service.save_extraction(
        first.id,
        metadata={"version": 1},
        groups=[
            NoteDraftGroupInput(
                title="原草稿",
                blocks=(
                    NoteDraftBlockInput(
                        block_type="text",
                        content_markdown="不能丢失",
                        source_asset_id=first.assets[0].id,
                    ),
                ),
            )
        ],
    )

    with pytest.raises(DomainError, match="其他会话"):
        service.save_extraction(
            first.id,
            metadata={"version": 2},
            groups=[
                NoteDraftGroupInput(
                    title="非法替换",
                    blocks=(
                        NoteDraftBlockInput(
                            block_type="text",
                            source_asset_id=second.assets[0].id,
                        ),
                    ),
                )
            ],
        )

    recovered = service.get_session(first.id)
    assert recovered is not None
    assert json.loads(recovered.draft_meta_json) == {"version": 1}
    assert recovered.groups[0].title == "原草稿"
    assert recovered.groups[0].blocks[0].content_markdown == "不能丢失"


def test_interrupted_and_terminal_sessions_have_explicit_lifecycle(
    note_intake_bundle,
) -> None:
    _data_root, _runtime, _app, service, image = note_intake_bundle
    interrupted = service.start_session([image], classification_mode="ai")
    completed = service.start_session([image], classification_mode="custom")
    cancelled = service.start_session([image], classification_mode="custom")

    service.mark_processing(interrupted.id)
    service.runtime.engine.dispose()
    restarted = bootstrap_runtime()
    restarted_service = NoteIntakeService(restarted)
    assert restarted_service.get_session(interrupted.id).status == "failed"
    assert "中断" in restarted_service.get_session(interrupted.id).error_message

    service.save_extraction(
        completed.id,
        metadata={},
        groups=[NoteDraftGroupInput(title="空白自定义组")],
    )
    service.mark_completed(completed.id)
    service.abandon_session(cancelled.id)
    service.abandon_session(cancelled.id)

    resumable_ids = {item.id for item in restarted_service.list_resumable_sessions()}
    assert interrupted.id in resumable_ids
    assert completed.id not in resumable_ids
    assert cancelled.id not in resumable_ids
    with pytest.raises(DomainError, match="不能放弃"):
        service.abandon_session(completed.id)


def test_shared_object_survives_problem_purge_when_note_draft_references_it(
    note_intake_bundle,
) -> None:
    _data_root, _runtime, app, service, image = note_intake_bundle
    problem_id = app.import_images([image])["created"][0]
    problem = app.get_problem(problem_id)
    intake = service.start_session([image], classification_mode="custom")
    object_path = service.resolve_source_path(intake.assets[0])

    assert problem.assets[0].relative_path == intake.assets[0].relative_path
    app.trash_problem(problem_id)
    assert app.purge_trashed() == 1
    assert object_path.is_file()


def test_cancelled_draft_cleanup_preserves_shared_objects_until_last_reference(
    note_intake_bundle,
) -> None:
    _data_root, _runtime, _app, service, image = note_intake_bundle
    first = service.start_session([image], classification_mode="custom")
    second = service.start_session([image], classification_mode="custom")
    object_path = service.resolve_source_path(first.assets[0])

    service.abandon_session(first.id)
    assert service.purge_cancelled_sessions() == 1
    assert service.get_session(first.id) is None
    assert object_path.is_file()

    service.abandon_session(second.id)
    assert service.purge_cancelled_sessions() == 1
    assert not object_path.exists()


def test_note_intake_validates_modes_categories_and_block_types(
    note_intake_bundle,
) -> None:
    _data_root, _runtime, _app, service, image = note_intake_bundle
    with pytest.raises(DomainError, match="分类方式"):
        service.start_session([image], classification_mode="automatic")

    intake = service.start_session([image], classification_mode="custom")
    with pytest.raises(DomainError, match="笔记块类型"):
        service.save_extraction(
            intake.id,
            metadata={},
            groups=[
                NoteDraftGroupInput(
                    blocks=(NoteDraftBlockInput(block_type="answer"),)
                )
            ],
        )
    with pytest.raises(DomainError, match="已有分类"):
        service.save_extraction(
            intake.id,
            metadata={},
            groups=[NoteDraftGroupInput(category_resolution="existing")],
        )
    with pytest.raises(DomainError, match="待整理"):
        service.save_extraction(
            intake.id,
            metadata={},
            groups=[NoteDraftGroupInput(target_status="active")],
        )
    with pytest.raises(DomainError, match="不存在的标签"):
        service.save_extraction(
            intake.id,
            metadata={},
            groups=[NoteDraftGroupInput(tag_ids=("tag_missing",))],
        )
