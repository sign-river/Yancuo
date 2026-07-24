"""AI note extraction stays independent from problem fields."""

from __future__ import annotations

from pathlib import Path

import pytest

from yancuo_win.application.bootstrap import bootstrap_runtime
from yancuo_win.application.note_ai_service import NoteAiService, NoteBlockDraft
from yancuo_win.config.settings import default_toml_path


@pytest.fixture()
def note_ai(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[NoteAiService, Path]:
    monkeypatch.setenv("YANCUO_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("YANCUO_CONFIG_FILE", str(default_toml_path()))
    runtime = bootstrap_runtime()
    runtime.settings.ai.enabled = True
    runtime.settings.ai.default_provider = "mock"
    image = tmp_path / "note.png"
    image.write_bytes(b"fake image bytes")
    return NoteAiService(runtime), image


def test_mock_image_becomes_note_blocks_without_problem_answer_fields(note_ai) -> None:
    service, image = note_ai

    draft = service.extract_from_image(image, instruction="每个公式单独成块")

    assert draft.title.startswith("识别题目-")
    assert draft.blocks
    assert any(block.block_type == "formula" for block in draft.blocks)
    assert all(not hasattr(block, "correct_answer") for block in draft.blocks)


def test_confirmed_draft_stores_immutable_source_asset(note_ai) -> None:
    service, image = note_ai
    draft = service.extract_from_image(image)

    note = service.commit_draft(
        draft,
        title="我的公式笔记",
    )

    loaded = service.notes.get_note(note.id)
    assert loaded is not None
    assert loaded.title == "我的公式笔记"
    assert loaded.assets
    assert loaded.assets[0].is_immutable is True
    assert loaded.blocks


def test_confirmed_draft_can_use_user_edited_blocks(note_ai) -> None:
    service, image = note_ai
    draft = service.extract_from_image(image)
    edited = NoteBlockDraft(block_type="text", content_markdown="用户确认后的概念")
    confirmed = type(draft)(
        source_path=draft.source_path,
        title=draft.title,
        summary=draft.summary,
        blocks=[edited],
        subject_suggestion=draft.subject_suggestion,
        chapter_suggestion=draft.chapter_suggestion,
        tags=draft.tags,
        uncertain_fields=draft.uncertain_fields,
        model=draft.model,
        cost_estimate=draft.cost_estimate,
    )

    note = service.commit_draft(confirmed)

    assert [block.content_markdown for block in note.blocks] == ["用户确认后的概念"]
