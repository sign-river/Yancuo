"""阶段 C：AI 识别、审核、撤销与原图保护。"""

from __future__ import annotations

from pathlib import Path

import pytest

from yancuo_win.ai.base import JsonCompletionResult, StructuredCandidate, StructuredResult
from yancuo_win.application.ai_service import (
    AIService,
    _recognition_cache_payload,
    _structured_result_from_cache,
)
from yancuo_win.application.bootstrap import bootstrap_runtime
from yancuo_win.application.services import AppServices
from yancuo_win.config.settings import default_toml_path
from yancuo_win.data.migrate import get_schema_version, verify_core_tables
from yancuo_win.domain.rules import DomainError
from yancuo_win.domain.identity import SCHEMA_VERSION
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
    assert get_schema_version(runtime.engine) == SCHEMA_VERSION
    assert verify_core_tables(runtime.engine) == []


def test_recognition_cache_envelope_preserves_candidate_context() -> None:
    result = StructuredResult(
        fields={"title": "第一题"},
        candidates=[
            StructuredCandidate(
                fields={
                    "title": "第一题",
                    "subject_id": "sub_math",
                    "chapter_id": "ch_limit",
                },
                uncertain_fields=[
                    {"field": "chapter_id", "reason": "章节边界需确认"}
                ],
                region={"x": 0.0, "y": 0.0, "width": 1.0, "height": 0.5},
            )
        ],
        diagnostics={
            "structure_suggestion": {
                "layout_kind": "single",
                "subquestion_count": 1,
                "confidence": 0.9,
                "rationale": "单题",
                "signals": ["单一区域"],
            },
            "provider_private_detail": "must not persist",
        },
    )
    proposals = [
        (
            dict(result.candidates[0].fields),
            list(result.candidates[0].uncertain_fields),
            dict(result.candidates[0].region),
        )
    ]

    restored = _structured_result_from_cache(
        _recognition_cache_payload(proposals, result),
        '{"raw":"kept separately"}',
    )

    assert restored is not None
    assert restored.candidates[0].fields == result.candidates[0].fields
    assert (
        restored.candidates[0].uncertain_fields
        == result.candidates[0].uncertain_fields
    )
    assert restored.candidates[0].region == result.candidates[0].region
    assert restored.diagnostics["structure_suggestion"]["confidence"] == 0.9
    assert "provider_private_detail" not in restored.diagnostics


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


def test_review_decisions_wait_for_final_apply_and_return_safe_presentation(
    services: AppServices, ai: AIService, tmp_path: Path
) -> None:
    image = tmp_path / "final-apply.jpg"
    image.write_bytes(b"\xff\xd8\xfffinal-apply")
    problem_id = services.import_images([image])["created"][0]
    before = services.get_problem(problem_id)
    assert before is not None

    job = ai.create_structure_job([problem_id])
    ai.run_job(job.id)
    item = ai.list_review_items_for_job(job.id)[0]
    card = ai.review_presentation(item.id)
    assert card["source"] == "AI 补全建议"
    assert problem_id not in str(card)
    assert "relative_path" not in str(card)

    # Recording a decision itself must leave the formal problem untouched.
    assert services.get_problem(problem_id).revision == before.revision  # type: ignore[union-attr]
    result = ai.apply_review_decisions({item.id: "accept"})
    assert result["accepted_problem_ids"] == [problem_id]
    assert services.get_problem(problem_id).revision == before.revision + 1  # type: ignore[union-attr]

    assert ai.undo_review_accepts(result["accepted_problem_ids"]) == 1
    assert services.get_problem(problem_id).revision == before.revision + 2  # type: ignore[union-attr]


def test_review_applies_only_fields_explicitly_accepted(
    services: AppServices, ai: AIService, tmp_path: Path
) -> None:
    image = tmp_path / "partial-apply.jpg"
    image.write_bytes(b"\xff\xd8\xffpartial-apply")
    problem_id = services.import_images([image])["created"][0]
    before = services.get_problem(problem_id)
    assert before is not None
    job = ai.create_structure_job([problem_id])
    ai.run_job(job.id)
    item = ai.list_review_items_for_job(job.id)[0]
    diffs = ai.review_presentation(item.id)["diffs"]
    assert any(diff["field"] == "question_markdown" for diff in diffs)

    decisions = {
        str(diff["field"]): {
            "decision": "accept"
            if diff["field"] == "question_markdown"
            else "reject"
        }
        for diff in diffs
    }
    ai.apply_review_decisions({item.id: decisions})

    after = services.get_problem(problem_id)
    assert after is not None
    assert "Mock" in after.question_markdown
    assert after.title == before.title


def test_completion_review_overview_is_resumable_without_problem_details(
    services: AppServices, ai: AIService, tmp_path: Path
) -> None:
    image = tmp_path / "overview.jpg"
    image.write_bytes(b"\xff\xd8\xffoverview")
    problem_id = services.import_images([image])["created"][0]
    job = ai.create_structure_job([problem_id])

    overview = ai.completion_review_overview()
    entry = next(value for value in overview if value["job_id"] == job.id)
    assert entry["label"] == "等待开始"
    assert entry["review_count"] == 0
    assert problem_id not in str({key: value for key, value in entry.items() if key != "job_id"})


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
        name = "flaky"

        def complete_json(self, **_kwargs) -> JsonCompletionResult:
            if self.should_fail:
                raise DomainError("temporary disconnect")
            return JsonCompletionResult(
                raw_text=(
                    '{"title":"重试成功","question_markdown":"题目",'
                    '"uncertain_fields":[]}'
                ),
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
    ai.record_ui_delivery_timings(
        job.id,
        ui_wait_ms=1.25,
        classification_match_ms=2.75,
    )
    delivered_diagnostics = ai.get_job_diagnostics(job.id)
    assert delivered_diagnostics["timings_ms"]["ui_wait"] == pytest.approx(1.2)
    assert delivered_diagnostics["timings_ms"]["classification_match"] == pytest.approx(2.8)
    assert services.count_problems() == original_count
    assert len(ai.list_review_items_for_job(job.id)) == 1


def test_formal_completion_is_text_only_and_never_clones_problem(
    services: AppServices,
    ai: AIService,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image = tmp_path / "two-problems.jpg"
    image.write_bytes(b"\xff\xd8\xffmulti-cache-image")
    problem_id = services.import_images([image])["created"][0]

    class TextProvider:
        calls = 0
        name = "text"

        def complete_json(self, **kwargs) -> JsonCompletionResult:
            self.calls += 1
            request_text = str(kwargs["request"])
            assert "relative_path" not in request_text
            assert "original" not in request_text
            return JsonCompletionResult(
                raw_text=(
                    '{"title":"文本建议","question_markdown":"题干建议",'
                    '"uncertain_fields":[]}'
                )
            )

    provider = TextProvider()
    monkeypatch.setattr(
        "yancuo_win.application.ai_service.get_provider",
        lambda _settings: provider,
    )

    first = ai.create_structure_job([problem_id])
    ai.run_job(first.id)
    second = ai.create_structure_job([problem_id])
    ai.run_job(second.id)

    assert provider.calls == 2
    assert len(ai.list_review_items_for_job(first.id)) == 1
    assert len(ai.list_review_items_for_job(second.id)) == 1
    assert services.count_problems() == 1
    assert ai.get_job_diagnostics(second.id)["cache_hits"] == 0


def test_ai_job_events_are_ordered_and_interrupted_jobs_requeue(ai: AIService) -> None:
    job = ai.create_background_job(
        domain="note_intake",
        context_id="nintake_test",
        job_type="note_extract",
    )
    ai.append_job_event(
        job.id, "text_delta", text_value="第一段", append_response=True
    )
    ai.append_job_event(
        job.id, "text_delta", text_value="第二段", append_response=True
    )
    ai.start_background_job(job.id)

    assert ai.recover_interrupted_jobs() == [job.id]
    recovered = ai.get_job(job.id)
    assert recovered is not None
    assert recovered.status == "pending"
    assert recovered.response_text == "第一段第二段"
    events = ai.list_job_events(job.id)
    assert [event.sequence for event in events] == list(range(1, len(events) + 1))
    assert [event.text for event in events if event.event_type == "text_delta"] == [
        "第一段",
        "第二段",
    ]
