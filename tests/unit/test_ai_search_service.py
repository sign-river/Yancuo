"""Bounded candidate recall, disclosure, and AI rerank validation tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from yancuo_win.ai.base import AIProvider, JsonCompletionResult, StructuredResult
from yancuo_win.ai.openai_compatible import OpenAICompatibleProvider
from yancuo_win.application.ai_search_service import (
    AiSearchDisclosure,
    AiSearchService,
    LocalSearchCandidate,
    build_rerank_request,
    parse_rerank_response,
    rerank_response_json_schema,
    validate_rerank_matches,
)
from yancuo_win.application.bootstrap import bootstrap_runtime
from yancuo_win.application.search_service import SearchIndexService
from yancuo_win.application.search_spec import (
    SearchBoundary,
    SearchSpecCompiler,
    parse_search_spec,
)
from yancuo_win.application.services import AppServices, KnowledgeScope
from yancuo_win.config.settings import default_toml_path
from yancuo_win.domain.rules import DomainError


class QueueProvider(AIProvider):
    name = "queue"

    def __init__(self, responses: list[str | Exception]) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, Any]] = []
        self.validated = False

    def validate_configuration(self) -> None:
        self.validated = True

    def structure_from_image(
        self,
        *,
        image_path: str,
        prompt: str,
        model: str,
        timeout_seconds: int,
    ) -> StructuredResult:
        raise NotImplementedError

    def complete_json(
        self,
        *,
        request: dict[str, Any],
        model: str,
        timeout_seconds: int,
    ) -> JsonCompletionResult:
        self.requests.append(
            {
                "request": request,
                "model": model,
                "timeout_seconds": timeout_seconds,
            }
        )
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return JsonCompletionResult(raw_text=response, model=model, total_tokens=12)


@pytest.fixture()
def ai_search_bundle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("YANCUO_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("YANCUO_CONFIG_FILE", str(default_toml_path()))
    runtime = bootstrap_runtime()
    app = AppServices(runtime)
    math = app.create_subject("高等数学")
    limit_chapter = app.create_chapter(math.id, "极限")
    derivative = app.create_chapter(math.id, "导数")
    target = app.create_problem(
        title="泰勒展开判断等价无穷小",
        question_markdown="用泰勒展开计算目标极限",
        status="active",
        subject_id=math.id,
        chapter_id=limit_chapter.id,
        priority=5,
    )
    app.update_problem(
        target.id,
        {
            "correct_answer": "敏感正确答案",
            "solution_markdown": "敏感解析",
            "user_answer": "我的敏感作答",
            "error_analysis": "我的敏感错因",
            "notes": "我的私人备注",
            "problem_type": "计算题",
            "is_favorite": True,
        },
    )
    tag = app.create_tag("泰勒展开")
    app.set_problem_tags(target.id, [tag.id])
    partial = app.create_problem(
        title="泰勒公式练习",
        question_markdown="只考查泰勒展开",
        status="active",
        subject_id=math.id,
        chapter_id=limit_chapter.id,
        priority=3,
    )
    app.create_problem(
        title="泰勒展开待整理",
        question_markdown="不应越过状态边界",
        status="inbox",
        subject_id=math.id,
        chapter_id=limit_chapter.id,
    )
    app.create_problem(
        title="泰勒展开求导",
        question_markdown="不应越过章节范围",
        status="active",
        subject_id=math.id,
        chapter_id=derivative.id,
    )
    SearchIndexService(runtime).rebuild()
    scope = KnowledgeScope(
        key=f"chapter:{limit_chapter.id}",
        label="高等数学 / 极限",
        subject_id=math.id,
        chapter_id=limit_chapter.id,
        include_descendants=True,
    )
    return runtime, app, target, partial, scope


def test_ai_search_only_sends_bounded_local_candidates(ai_search_bundle) -> None:
    runtime, _app, target, _partial, scope = ai_search_bundle
    provider = QueueProvider(
        [
            json.dumps(
                {
                    "keywords": ["泰勒展开", "等价无穷小"],
                    "match_mode": "all",
                    "filters": [
                        {"field": "priority", "operator": "gte", "value": 4}
                    ],
                    "limit": 5,
                    "semantic_intent": "寻找等价阶数判断错误",
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {
                    "matches": [
                        {
                            "id": target.id,
                            "score": 0.95,
                            "reason": "同时涉及泰勒展开与等价无穷小",
                        },
                        {
                            "id": "problem_fabricated",
                            "score": 0.9,
                            "reason": "模型虚构",
                        },
                        {
                            "id": target.id,
                            "score": 0.8,
                            "reason": "重复候选",
                        },
                    ]
                },
                ensure_ascii=False,
            ),
        ]
    )
    stages: list[str] = []
    result = AiSearchService(runtime, provider=provider).search(
        "找出用泰勒展开判断等价无穷小的题",
        boundary=SearchBoundary(
            scope=scope,
            statuses=("active",),
            max_candidates=10,
            max_results=5,
        ),
        progress=stages.append,
    )

    assert provider.validated is True
    assert len(provider.requests) == 2
    assert result.candidates_considered == 1
    assert result.candidates_sent == 1
    assert [item.problem.id for item in result.matches] == [target.id]
    assert [item.reason for item in result.rejected_matches] == [
        "ID 不属于本轮本地候选",
        "模型重复返回同一候选 ID",
    ]
    intent_user = provider.requests[0]["request"]["messages"][1]["content"]
    rerank_user = provider.requests[1]["request"]["messages"][1]["content"]
    assert target.id not in intent_user
    assert target.id in rerank_user
    assert "敏感正确答案" not in rerank_user
    assert "我的敏感作答" not in rerank_user
    assert "我的敏感错因" not in rerank_user
    assert "我的私人备注" not in rerank_user
    assert "原图" not in rerank_user
    assert stages == ["intent", "local_recall", "rerank", "complete"]
    assert set(result.diagnostics.stages_ms) == {
        "intent",
        "local_recall",
        "rerank",
        "total",
    }
    assert result.diagnostics.candidates_sent == 1
    assert result.diagnostics.total_tokens == 24
    assert result.diagnostics.request_attempts == 2
    assert result.diagnostics.disclosed_fields == (
        "ID",
        "标题",
        "题干",
        "知识路径",
        "标签",
        "更新时间",
    )
    assert result.diagnostics.payload_bytes == len(rerank_user.encode("utf-8"))


def test_keyword_all_any_and_boundary_recall(ai_search_bundle) -> None:
    runtime, _app, target, partial, scope = ai_search_bundle
    service = AiSearchService(runtime, provider=QueueProvider([]))
    boundary = SearchBoundary(
        scope=scope,
        statuses=("active",),
        max_candidates=20,
        max_results=10,
    )
    all_plan = SearchSpecCompiler.compile(
        parse_search_spec(
            {
                "keywords": ["泰勒展开", "等价无穷小"],
                "match_mode": "all",
            }
        ),
        boundary,
    )
    any_plan = SearchSpecCompiler.compile(
        parse_search_spec(
            {
                "keywords": ["泰勒展开", "等价无穷小"],
                "match_mode": "any",
            }
        ),
        boundary,
    )

    assert [item.problem.id for item in service.recall(all_plan)] == [target.id]
    assert {item.problem.id for item in service.recall(any_plan)} == {
        target.id,
        partial.id,
    }


def test_filter_only_intent_uses_local_scope_browse(ai_search_bundle) -> None:
    runtime, _app, target, _partial, scope = ai_search_bundle
    plan = SearchSpecCompiler.compile(
        parse_search_spec(
            {
                "filters": [
                    {"field": "is_favorite", "operator": "eq", "value": True},
                    {"field": "problem_type", "operator": "eq", "value": "计算题"},
                ]
            }
        ),
        SearchBoundary(scope=scope, statuses=("active",), max_candidates=5),
    )

    candidates = AiSearchService(
        runtime,
        provider=QueueProvider([]),
    ).recall(plan)
    assert [item.problem.id for item in candidates] == [target.id]


def test_program_owned_allowed_ids_limit_recall(ai_search_bundle) -> None:
    runtime, _app, target, partial, scope = ai_search_bundle
    plan = SearchSpecCompiler.compile(
        parse_search_spec({"keywords": ["泰勒展开"], "match_mode": "any"}),
        SearchBoundary(
            scope=scope,
            statuses=("active",),
            allowed_problem_ids=frozenset({partial.id}),
        ),
    )

    candidates = AiSearchService(
        runtime,
        provider=QueueProvider([]),
    ).recall(plan)
    assert [item.problem.id for item in candidates] == [partial.id]
    assert target.id not in {item.problem.id for item in candidates}


def test_no_local_candidates_skips_second_ai_request(ai_search_bundle) -> None:
    runtime, _app, _target, _partial, scope = ai_search_bundle
    provider = QueueProvider(
        [
            json.dumps(
                {"keywords": ["绝对不存在的关键词"], "semantic_intent": "不存在"},
                ensure_ascii=False,
            )
        ]
    )
    result = AiSearchService(runtime, provider=provider).search(
        "不存在",
        boundary=SearchBoundary(scope=scope, statuses=("active",)),
    )

    assert len(provider.requests) == 1
    assert result.candidates_sent == 0
    assert result.matches == ()
    assert result.rerank_completion is None


def test_disclosure_policy_controls_optional_private_fields(ai_search_bundle) -> None:
    runtime, app, target, _partial, scope = ai_search_bundle
    problem = app.get_problem(target.id)
    candidate = LocalSearchCandidate(
        problem=problem,
        knowledge_path=scope.label,
        snippet="",
        local_score=1.0,
        matched_keywords=("泰勒展开",),
    )
    plan = SearchSpecCompiler.compile(
        parse_search_spec({"keywords": ["泰勒展开"]}),
        SearchBoundary(scope=scope, statuses=("active",)),
    )

    default_request, default_sent = build_rerank_request(
        "泰勒",
        plan=plan,
        candidates=(candidate,),
        disclosure=AiSearchDisclosure(),
    )
    private_request, private_sent = build_rerank_request(
        "泰勒",
        plan=plan,
        candidates=(candidate,),
        disclosure=AiSearchDisclosure(
            include_answers=True,
            include_personal_content=True,
        ),
    )

    assert len(default_sent) == len(private_sent) == 1
    default_text = default_request["messages"][1]["content"]
    private_text = private_request["messages"][1]["content"]
    assert "敏感正确答案" not in default_text
    assert "我的敏感作答" not in default_text
    assert "敏感正确答案" in private_text
    assert "我的敏感作答" in private_text


def test_candidate_count_and_payload_budget_are_hard_caps(ai_search_bundle) -> None:
    runtime, app, target, _partial, scope = ai_search_bundle
    problem = app.get_problem(target.id)
    candidates = tuple(
        LocalSearchCandidate(
            problem=problem,
            knowledge_path=scope.label,
            snippet="",
            local_score=float(index),
            matched_keywords=("泰勒展开",),
        )
        for index in range(25)
    )
    plan = SearchSpecCompiler.compile(
        parse_search_spec({"keywords": ["泰勒展开"]}),
        SearchBoundary(scope=scope, statuses=("active",)),
    )
    request, sent = build_rerank_request(
        "泰勒",
        plan=plan,
        candidates=candidates,
        disclosure=AiSearchDisclosure(max_candidates=3, max_payload_bytes=1024),
    )

    assert len(sent) <= 3
    content = request["messages"][1]["content"]
    assert len(content.encode("utf-8")) <= 1024


def test_rerank_schema_and_strict_parser_match_protocol() -> None:
    root = Path(__file__).parents[2]
    expected = rerank_response_json_schema()
    for path in (
        root / "protocol" / "schemas" / "search-rerank.schema.json",
        root
        / "apps"
        / "windows"
        / "src"
        / "yancuo_win"
        / "resources"
        / "protocol"
        / "schemas"
        / "search-rerank.schema.json",
    ):
        assert json.loads(path.read_text(encoding="utf-8")) == expected
    assert expected["additionalProperties"] is False
    assert expected["$defs"]["RerankMatchSpec"]["additionalProperties"] is False

    with pytest.raises(DomainError):
        parse_rerank_response(
            {
                "matches": [
                    {"id": "problem_1", "score": 0.5, "reason": "匹配", "sql": "DROP"}
                ]
            }
        )
    with pytest.raises(DomainError):
        parse_rerank_response(
            {"matches": [{"id": "problem_1", "score": 1.1, "reason": "越界"}]}
        )
    with pytest.raises(DomainError):
        parse_rerank_response("```json\n{\"matches\":[]}\n```")


def test_result_limit_and_score_order_are_enforced(ai_search_bundle) -> None:
    _runtime, app, target, partial, scope = ai_search_bundle
    candidates = tuple(
        LocalSearchCandidate(
            problem=app.get_problem(problem_id),
            knowledge_path=scope.label,
            snippet="",
            local_score=0,
            matched_keywords=(),
        )
        for problem_id in (target.id, partial.id)
    )
    response = parse_rerank_response(
        {
            "matches": [
                {"id": target.id, "score": 0.2, "reason": "较低"},
                {"id": partial.id, "score": 0.9, "reason": "较高"},
            ]
        }
    )
    matches, rejected = validate_rerank_matches(
        response,
        candidates=candidates,
        result_limit=1,
    )

    assert [item.problem.id for item in matches] == [partial.id]
    assert rejected[0].candidate_id == target.id
    assert rejected[0].reason == "超过本地结果数量上限"


def test_provider_network_error_is_not_replaced_with_unbounded_fallback(
    ai_search_bundle,
) -> None:
    runtime, _app, _target, _partial, scope = ai_search_bundle
    provider = QueueProvider([DomainError("网络中断")])

    with pytest.raises(DomainError, match="网络中断"):
        AiSearchService(runtime, provider=provider).search(
            "泰勒展开",
            boundary=SearchBoundary(scope=scope, statuses=("active",)),
        )
    assert len(provider.requests) == 1


def test_cancellation_stops_before_candidate_rerank(ai_search_bundle) -> None:
    runtime, _app, _target, _partial, scope = ai_search_bundle
    provider = QueueProvider(
        [json.dumps({"keywords": ["泰勒展开"]}, ensure_ascii=False)]
    )
    checks = 0

    def should_cancel() -> bool:
        nonlocal checks
        checks += 1
        return checks >= 2

    with pytest.raises(DomainError, match="已取消"):
        AiSearchService(runtime, provider=provider).search(
            "泰勒展开",
            boundary=SearchBoundary(scope=scope, statuses=("active",)),
            should_cancel=should_cancel,
        )
    assert len(provider.requests) == 1


def test_openai_compatible_text_completion_preserves_schema_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = OpenAICompatibleProvider(
        base_url="https://example.invalid/v1",
        api_key_env="TEST_KEY",
    )
    captured: dict[str, Any] = {}

    def fake_request(
        endpoint: str,
        *,
        method: str,
        timeout_seconds: int,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        captured.update(
            {
                "endpoint": endpoint,
                "method": method,
                "timeout_seconds": timeout_seconds,
                "payload": payload,
            }
        )
        return {
            "model": "text-model",
            "choices": [{"message": {"content": "{\"matches\":[]}"}}],
            "usage": {
                "prompt_tokens": 7,
                "completion_tokens": 3,
                "total_tokens": 10,
            },
        }

    monkeypatch.setattr(provider, "_request_json", fake_request)
    request = {
        "messages": [{"role": "user", "content": "x"}],
        "response_format": {"type": "json_schema"},
    }
    result = provider.complete_json(
        request=request,
        model="text-model",
        timeout_seconds=15,
    )

    assert captured["endpoint"] == "/chat/completions"
    assert captured["method"] == "POST"
    assert captured["payload"]["model"] == "text-model"
    assert captured["payload"]["response_format"] == request["response_format"]
    assert result.raw_text == "{\"matches\":[]}"
    assert result.total_tokens == 10
