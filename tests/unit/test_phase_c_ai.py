"""阶段 C：AI 识别、审核、撤销与原图保护。"""

from __future__ import annotations

from pathlib import Path

import pytest

from yancuo_win.ai.base import StructuredResult
from yancuo_win.application.ai_service import AIService
from yancuo_win.application.bootstrap import bootstrap_runtime
from yancuo_win.application.services import AppServices
from yancuo_win.config.settings import default_toml_path
from yancuo_win.data.migrate import get_schema_version, verify_core_tables
from yancuo_win.domain.rules import DomainError
from yancuo_win.review.changeset import validate_and_filter_proposal


@pytest.fixture()
def runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("YANCUO_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("YANCUO_CONFIG_FILE", str(default_toml_path()))
    monkeypatch.setenv("YANCUO_AI__DEFAULT_PROVIDER", "mock")
    return bootstrap_runtime()


@pytest.fixture()
def services(runtime) -> AppServices:
    return AppServices(runtime)


@pytest.fixture()
def ai(runtime) -> AIService:
    return AIService(runtime)


def test_schema_v2_tables(runtime) -> None:
    assert get_schema_version(runtime.engine) == 7
    assert verify_core_tables(runtime.engine) == []


def test_mock_recognize_review_accept_reject_undo(
    services: AppServices, ai: AIService, tmp_path: Path
) -> None:
    img = tmp_path / "q1.jpg"
    img.write_bytes(b"\xff\xd8\xff" + b"phase-c-image")
    pid = services.import_images([img])["created"][0]

    job = ai.create_structure_job([pid])
    ai.run_job(job.id)
    job2 = ai.get_job(job.id)
    assert job2 is not None
    assert job2.status == "completed"
    assert job2.done_items == 1

    pending = ai.list_open_review_items()
    assert len(pending) == 1
    rid = pending[0].id

    # 拒绝路径：不写正式字段
    before = services.get_problem(pid)
    assert before is not None
    old_q = before.question_markdown
    ai.reject_review_item(rid)
    after_reject = services.get_problem(pid)
    assert after_reject is not None
    assert after_reject.question_markdown == old_q

    # 再跑一次并接受
    job_b = ai.create_structure_job([pid])
    ai.run_job(job_b.id)
    rid2 = ai.list_open_review_items()[0].id
    ai.accept_review_item(rid2)
    accepted = services.get_problem(pid)
    assert accepted is not None
    assert "Mock" in (accepted.question_markdown or "")
    assert accepted.revision >= 2
    ai.assert_original_untouched(pid)

    # 撤销
    ai.undo_last_ai_accept(pid)
    undone = services.get_problem(pid)
    assert undone is not None
    assert undone.question_markdown == old_q


def test_ai_cannot_delete_and_filters_forbidden_fields() -> None:
    with pytest.raises(DomainError):
        validate_and_filter_proposal(
            {"delete_problem": True, "title": "x"},
            allowed_fields={"title"},
            allow_delete=False,
        )
    filtered, _ = validate_and_filter_proposal(
        {"title": "t", "id": "hack", "revision": 99, "question_markdown": "q"},
        allowed_fields={"title", "question_markdown"},
    )
    assert "id" not in filtered
    assert "revision" not in filtered
    assert filtered["title"] == "t"


def test_prompt_not_hardcoded_only(ai: AIService) -> None:
    prompt = ai.get_prompt("structure_recognize")
    assert "JSON" in prompt.body
    assert prompt.is_builtin is True


def test_failed_ai_item_can_retry_in_same_job_without_duplicate_problem(
    services: AppServices,
    ai: AIService,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image = tmp_path / "retry.jpg"
    image.write_bytes(b"\xff\xd8\xffretry-same-job")
    problem_id = services.import_images([image])["created"][0]
    original_count = services.count_problems()

    class FlakyProvider:
        should_fail = True

        def structure_from_image(self, **_kwargs) -> StructuredResult:
            if self.should_fail:
                raise DomainError("temporary disconnect")
            return StructuredResult(
                fields={"title": "重试成功", "question_markdown": "题目"},
                raw_text="{}",
                model="test-model",
            )

    provider = FlakyProvider()
    monkeypatch.setattr(
        "yancuo_win.application.ai_service.get_provider",
        lambda _settings: provider,
    )
    job = ai.create_structure_job([problem_id])

    ai.run_job(job.id)
    first = ai.get_job(job.id)
    assert first is not None
    assert first.done_items == 0
    assert first.failed_items == 1
    failed_diagnostics = ai.get_job_diagnostics(job.id)
    assert failed_diagnostics["stage"] == "failed"
    assert failed_diagnostics["timing_samples"] == 0

    provider.should_fail = False
    ai.run_job(job.id)
    second = ai.get_job(job.id)
    assert second is not None
    assert second.done_items == 1
    assert second.failed_items == 0
    completed_diagnostics = ai.get_job_diagnostics(job.id)
    assert completed_diagnostics["stage"] == "completed"
    assert completed_diagnostics["timing_samples"] == 1
    assert completed_diagnostics["timings_ms"]["total"] >= 0
    assert services.count_problems() == original_count
    assert len(ai.list_review_items_for_job(job.id)) == 1
