"""Task-oriented manual and AI intake workflow."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import select

from yancuo_win.application.bootstrap import bootstrap_runtime
from yancuo_win.application.intake_service import ProblemIntakeService
from yancuo_win.config.settings import default_toml_path
from yancuo_win.data.models import ReviewItem, SyncOperation, Version
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


def test_ai_intake_stays_job_scoped_and_commits_candidate(
    intake: ProblemIntakeService, tmp_path: Path
) -> None:
    subject = intake.app.create_subject("线性代数")
    chapter = intake.app.create_chapter(subject.id, "矩阵")
    image = tmp_path / "circled-question.jpg"
    image.write_bytes(b"\xff\xd8\xffai-intake-image")

    started = intake.start_ai([image], user_instruction="只提取画红圈的题目")
    job = intake.ai.get_job(started.job_id)
    assert job is not None
    prompt = intake.ai.get_prompt(job.prompt_key)
    assert "只提取画红圈的题目" in prompt.body
    assert "subject_name" in prompt.body

    intake.ai.run_job(started.job_id)
    progress = intake.progress(started.job_id)
    assert progress.status == "completed"
    assert progress.done == 1

    candidates = intake.list_candidates(started.job_id)
    assert len(candidates) == 1
    resumed = ProblemIntakeService(intake.runtime)
    assert resumed.latest_resumable_ai_job() == started.job_id
    candidate = candidates[0]
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
        assert item is not None
        assert item.status == "accepted"
        proposal = json.loads(item.proposed_json)
        assert proposal["subject_id"] == subject.id
    assert resumed.latest_resumable_ai_job() is None


def test_reject_ai_candidate_moves_staging_problem_to_trash(
    intake: ProblemIntakeService, tmp_path: Path
) -> None:
    image = tmp_path / "reject.jpg"
    image.write_bytes(b"\xff\xd8\xffreject-intake-image")
    started = intake.start_ai([image])
    intake.ai.run_job(started.job_id)
    candidate = intake.list_candidates(started.job_id)[0]

    intake.reject_ai_candidate(candidate.review_item_id)

    problem = intake.app.get_problem(candidate.problem_id)
    assert problem is not None
    assert problem.status == "trashed"


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
