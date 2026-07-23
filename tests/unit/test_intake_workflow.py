"""Task-oriented manual and AI intake workflow."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import select

from yancuo_win.ai.base import StructuredCandidate, StructuredResult
from yancuo_win.application.bootstrap import bootstrap_runtime
from yancuo_win.application.intake_service import ProblemIntakeService
from yancuo_win.config.settings import default_toml_path
from yancuo_win.data.models import (
    IntakeCandidateRecord,
    IntakeSession,
    ReviewItem,
    SyncOperation,
    Version,
)
from yancuo_win.domain.rules import DomainError


@pytest.fixture()
def intake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ProblemIntakeService:
    monkeypatch.setenv("YANCUO_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("YANCUO_CONFIG_FILE", str(default_toml_path()))
    monkeypatch.setenv("YANCUO_AI__DEFAULT_PROVIDER", "mock")
    return ProblemIntakeService(bootstrap_runtime())


def test_manual_intake_commits_one_complete_problem(
    intake: ProblemIntakeService, tmp_path: Path
) -> None:
    subject = intake.app.create_subject("高等数学")
    chapter = intake.app.create_chapter(subject.id, "极限")
    image = tmp_path / "manual.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\nmanual-intake")

    problem = intake.commit_manual(
        {
            "title": "手动录入题",
            "subject_id": subject.id,
            "chapter_id": chapter.id,
            "problem_type": "计算题",
            "priority": 5,
            "question_markdown": "求极限。",
            "correct_answer": "1",
            "solution_markdown": "使用等价无穷小。",
        },
        tag_names=["高频", "极限", "高频"],
        image_paths=[image],
    )

    assert problem.status == "active"
    assert problem.human_confirmed is True
    assert problem.subject_id == subject.id
    assert problem.chapter_id == chapter.id
    assert {tag.name for tag in problem.tags} == {"高频", "极限"}
    assert len(problem.assets) == 1
    assert problem.assets[0].is_immutable is True

    with intake.runtime.session_factory() as session:
        version = session.scalar(
            select(Version).where(Version.problem_id == problem.id)
        )
        operation = session.scalar(
            select(SyncOperation).where(SyncOperation.entity_id == problem.id)
        )
        assert version is not None
        assert version.source == "manual"
        assert operation is not None
        assert operation.operation == "create"


def test_manual_draft_survives_service_restart_with_internal_image(
    intake: ProblemIntakeService, tmp_path: Path
) -> None:
    image = tmp_path / "draft.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\nmanual-draft")
    fields = {
        "title": "未完成草稿",
        "question_markdown": "稍后继续填写",
        "priority": 4,
    }

    intake.save_manual_draft(
        fields,
        tag_names=["待整理"],
        image_paths=[image],
    )
    resumed = ProblemIntakeService(intake.runtime)
    draft = resumed.load_manual_draft()

    assert draft is not None
    assert draft.fields["title"] == "未完成草稿"
    assert draft.tag_names == ["待整理"]
    assert len(draft.image_paths) == 1
    assert draft.image_paths[0].is_file()
    assert draft.image_paths[0] != image
    assert resumed.app.count_problems() == 0

    resumed.clear_manual_draft()
    assert resumed.load_manual_draft() is None


def test_ai_intake_stays_job_scoped_and_commits_candidate(
    intake: ProblemIntakeService, tmp_path: Path
) -> None:
    subject = intake.app.create_subject("线性代数")
    chapter = intake.app.create_chapter(subject.id, "矩阵")
    image = tmp_path / "circled-question.jpg"
    image.write_bytes(b"\xff\xd8\xffai-intake-image")

    started = intake.start_ai([image], user_instruction="只提取画红圈的题目")
    assert intake.app.count_problems() == 0
    job = intake.ai.get_job(started.job_id)
    assert job is not None
    prompt = intake.ai.get_prompt(job.prompt_key)
    assert "只提取画红圈的题目" in prompt.body
    assert "subject_name" in prompt.body
    assert "Markdown 字段中的公式必须使用 $...$ 或 $$...$$ 定界" in prompt.body
    assert "question_latex 只写裸 LaTeX" in prompt.body

    intake.ai.run_job(started.job_id)
    progress = intake.progress(started.job_id)
    assert progress.status == "completed"
    assert progress.done == 1

    candidates = intake.list_candidates(started.job_id)
    assert len(candidates) == 1
    resumed = ProblemIntakeService(intake.runtime)
    assert resumed.latest_resumable_ai_job() == started.job_id
    candidate = candidates[0]
    assert candidate.problem_id == ""
    fields = dict(candidate.fields)
    fields.update(
        {
            "subject_id": subject.id,
            "chapter_id": chapter.id,
            "problem_type": "选择题",
            "priority": 4,
        }
    )
    committed = intake.commit_ai_candidate(
        candidate.review_item_id,
        fields,
        tag_names=["AI整理", "矩阵"],
    )

    assert committed.status == "active"
    assert committed.human_confirmed is True
    assert committed.subject_id == subject.id
    assert committed.chapter_id == chapter.id
    assert committed.problem_type == "选择题"
    assert "Mock" in committed.question_markdown
    assert {tag.name for tag in committed.tags} == {"AI整理", "矩阵"}
    with intake.runtime.session_factory() as session:
        item = session.get(ReviewItem, candidate.review_item_id)
        assert item is None
        intake_item = session.get(
            IntakeCandidateRecord, candidate.review_item_id
        )
        assert intake_item is not None
        assert intake_item.status == "committed"
        assert intake_item.problem_id == committed.id
        proposal = json.loads(intake_item.fields_json)
        assert proposal["subject_id"] == subject.id
    assert resumed.latest_resumable_ai_job() is None


def test_one_image_can_create_multiple_independent_candidates(
    intake: ProblemIntakeService,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image = tmp_path / "two-problems.jpg"
    image.write_bytes(b"\xff\xd8\xffmulti-candidate-image")
    imported = intake.app.import_images([image], into_status="inbox")
    staging_id = imported["created"][0]
    job = intake.ai.create_structure_job(
        [staging_id],
        user_instruction=intake._taxonomy_instruction(),
    )

    class MultiProvider:
        def structure_from_image(self, **_kwargs) -> StructuredResult:
            candidates = [
                StructuredCandidate(
                    fields={
                        "title": "同图第一题",
                        "question_markdown": "第一题题干",
                        "correct_answer": "1",
                    },
                    region={"x": 0.05, "y": 0.1, "width": 0.9, "height": 0.3},
                ),
                StructuredCandidate(
                    fields={
                        "title": "同图第二题",
                        "question_markdown": "第二题题干",
                        "correct_answer": "2",
                    },
                    uncertain_fields=[
                        {
                            "field": "question_markdown",
                            "content": "第二题题干",
                            "reason": "边缘略模糊",
                        }
                    ],
                    region={"x": 0.05, "y": 0.5, "width": 0.9, "height": 0.4},
                ),
            ]
            return StructuredResult(
                fields=candidates[0].fields,
                candidates=candidates,
                raw_text='{"problems": [...]}',
                model="multi-test",
            )

    monkeypatch.setattr(
        "yancuo_win.application.ai_service.get_provider",
        lambda _settings: MultiProvider(),
    )
    intake.ai.run_job(job.id)

    candidates = intake.list_candidates(job.id)
    assert len(candidates) == 2
    assert len({candidate.problem_id for candidate in candidates}) == 2
    assert {candidate.fields["title"] for candidate in candidates} == {
        "同图第一题",
        "同图第二题",
    }
    assert any(candidate.uncertain for candidate in candidates)
    assert candidates[0].region["height"] == pytest.approx(0.3)
    assert candidates[1].region["y"] == pytest.approx(0.5)

    staged = [intake.app.get_problem(candidate.problem_id) for candidate in candidates]
    assert all(problem is not None for problem in staged)
    relative_paths = {
        problem.assets[0].relative_path
        for problem in staged
        if problem is not None
    }
    assert len(relative_paths) == 1

    committed = [
        intake.commit_ai_candidate(
            candidate.review_item_id,
            candidate.fields,
            tag_names=["一图多题"],
        )
        for candidate in candidates
    ]
    assert {problem.status for problem in committed} == {"active"}
    assert {problem.title for problem in committed} == {"同图第一题", "同图第二题"}


def test_candidate_can_be_split_and_merged_without_duplicating_image(
    intake: ProblemIntakeService, tmp_path: Path
) -> None:
    image = tmp_path / "split-merge.jpg"
    image.write_bytes(b"\xff\xd8\xffsplit-merge-image")
    started = intake.start_ai([image])
    intake.ai.run_job(started.job_id)
    original = intake.list_candidates(started.job_id)[0]

    intake.split_ai_candidate(
        original.review_item_id,
        original.fields,
        tag_names=["人工拆分"],
    )
    split = [
        candidate
        for candidate in intake.list_candidates(started.job_id)
        if candidate.status in {"pending", "conflict"}
    ]
    assert len(split) == 2
    assert split[0].region == {
        "x": 0.0,
        "y": 0.0,
        "width": 1.0,
        "height": 0.5,
    }
    assert split[1].region == {
        "x": 0.0,
        "y": 0.5,
        "width": 1.0,
        "height": 0.5,
    }
    changed_region = {
        "x": 0.1,
        "y": 0.15,
        "width": 0.7,
        "height": 0.25,
    }
    assert (
        intake.update_ai_candidate_region(
            split[0].review_item_id, changed_region
        )
        == changed_region
    )
    refreshed = {
        candidate.review_item_id: candidate
        for candidate in intake.list_candidates(started.job_id)
    }
    assert refreshed[split[0].review_item_id].region == changed_region
    intake.update_ai_candidate_region(
        split[0].review_item_id,
        {"x": 0.0, "y": 0.0, "width": 1.0, "height": 0.5},
    )
    assert intake.app.count_problems() == 0
    with intake.runtime.session_factory() as session:
        records = [
            session.get(IntakeCandidateRecord, candidate.review_item_id)
            for candidate in split
        ]
        assert all(record is not None for record in records)
        assert len(
            {
                record.intake_asset_id
                for record in records
                if record is not None
            }
        ) == 1

    intake.merge_ai_candidates(
        split[0].review_item_id,
        split[1].review_item_id,
        split[0].fields,
        tag_names=["人工拆分"],
    )
    merged = [
        candidate
        for candidate in intake.list_candidates(started.job_id)
        if candidate.status in {"pending", "conflict"}
    ]
    assert len(merged) == 1
    assert merged[0].region == {
        "x": 0.0,
        "y": 0.0,
        "width": 1.0,
        "height": 1.0,
    }
    assert merged[0].fields["question_markdown"] == original.fields["question_markdown"]
    assert intake.app.count_problems() == 0
    with intake.runtime.session_factory() as session:
        rejected = session.get(
            IntakeCandidateRecord, split[1].review_item_id
        )
        assert rejected is not None
        assert rejected.status == "rejected"


def test_reject_ai_candidate_does_not_create_or_trash_formal_problem(
    intake: ProblemIntakeService, tmp_path: Path
) -> None:
    image = tmp_path / "reject.jpg"
    image.write_bytes(b"\xff\xd8\xffreject-intake-image")
    started = intake.start_ai([image])
    intake.ai.run_job(started.job_id)
    candidate = intake.list_candidates(started.job_id)[0]

    intake.reject_ai_candidate(candidate.review_item_id)

    assert intake.app.count_problems() == 0
    with intake.runtime.session_factory() as session:
        record = session.get(
            IntakeCandidateRecord, candidate.review_item_id
        )
        intake_session = session.get(
            IntakeSession, started.intake_session_id
        )
        assert record is not None
        assert record.status == "rejected"
        assert intake_session is not None
        assert intake_session.status == "completed"


def test_missing_real_api_key_does_not_create_staging_problem(
    intake: ProblemIntakeService,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image = tmp_path / "no-key.jpg"
    image.write_bytes(b"\xff\xd8\xffmissing-key")
    intake.runtime.settings.ai.default_provider = "openai_compatible"
    monkeypatch.delenv("FARO_API_KEY", raising=False)
    monkeypatch.setattr(
        "yancuo_win.ai.openai_compatible.get_secret",
        lambda _key: None,
    )

    before = intake.app.count_problems()
    with pytest.raises(DomainError, match="未配置 AI 密钥"):
        intake.start_ai([image])
    assert intake.app.count_problems() == before
