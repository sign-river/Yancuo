"""Problem-scoped conversations persist locally and remain tied to a revision."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtGui import QColor, QImage

import yancuo_win.application.problem_chat_service as chat_module
from yancuo_win.ai.base import (
    ChatCompletionResult,
    ProviderCapabilities,
    StructuredResult,
)
from yancuo_win.application.bootstrap import bootstrap_runtime
from yancuo_win.application.problem_chat_service import (
    ProblemChatService,
    ProblemReference,
)
from yancuo_win.application.services import AppServices
from yancuo_win.config.settings import default_toml_path
from yancuo_win.data.models import Asset
from yancuo_win.domain.rules import DomainError


class _CapturingChatProvider:
    capabilities = ProviderCapabilities(supports_chat=True, supports_chat_images=True)

    def __init__(self, *, fail_count: int = 0) -> None:
        self.fail_count = fail_count
        self.requests: list[list[dict[str, Any]]] = []

    def complete_chat(
        self,
        *,
        messages: list[dict[str, Any]],
        model: str,
        timeout_seconds: int,
        on_text_delta: Callable[[str], None] | None = None,
    ) -> ChatCompletionResult:
        del timeout_seconds
        self.requests.append(messages)
        if self.fail_count:
            self.fail_count -= 1
            raise RuntimeError("temporary provider failure")
        return ChatCompletionResult(content_markdown="回答", model=model)

    def structure_from_image(
        self,
        *,
        image_path: str,
        prompt: str,
        model: str,
        timeout_seconds: int,
    ) -> StructuredResult:
        raise NotImplementedError


@pytest.fixture()
def chat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[AppServices, ProblemChatService]:
    monkeypatch.setenv("YANCUO_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("YANCUO_CONFIG_FILE", str(default_toml_path()))
    runtime = bootstrap_runtime()
    runtime.settings.ai.default_provider = "mock"
    runtime.settings.ai.default_text_model = "mock-chat"
    return AppServices(runtime), ProblemChatService(runtime)


def test_problem_chat_persists_snapshot_messages_and_export(
    chat: tuple[AppServices, ProblemChatService], tmp_path: Path
) -> None:
    services, problem_chat = chat
    problem = services.create_problem(
        title="积分题", status="active", question_markdown="计算 $\\int_0^1 x dx$"
    )
    conversation = problem_chat.create_conversation(problem.id, title="积分讨论")
    assert conversation.problem_revision == problem.revision
    reply = problem_chat.send_message(conversation.id, "为什么答案是 1/2？")
    assert reply.role == "assistant"
    loaded = problem_chat.get_conversation(conversation.id)
    assert loaded is not None
    assert [message.role for message in loaded.messages] == ["user", "assistant"]
    assert loaded.messages[0].status == "complete"
    problem_chat.save_conversation(conversation.id)
    assert problem_chat.get_conversation(conversation.id).status == "saved"  # type: ignore[union-attr]
    export = problem_chat.export_conversation_markdown(
        conversation.id, tmp_path / "chat.md"
    )
    assert "积分讨论" in export.read_text(encoding="utf-8")


def test_failed_chat_keeps_pending_user_message_as_failed(
    chat: tuple[AppServices, ProblemChatService],
) -> None:
    services, problem_chat = chat
    problem = services.create_problem(title="失败题", status="active")
    conversation = problem_chat.create_conversation(problem.id)
    with problem_chat._session() as session:
        row = session.get(type(conversation), conversation.id)
        row.provider = "unknown"
        session.commit()
    reply = problem_chat.send_message(conversation.id, "测试失败持久化")
    assert reply.role == "user"
    assert reply.status == "failed"
    assert reply.error_message


def test_reference_sources_follow_derived_figure_block_order(
    chat: tuple[AppServices, ProblemChatService],
    tmp_path: Path,
) -> None:
    services, problem_chat = chat
    problem = services.create_problem(title="多图题", status="active")
    sources = []
    for name, payload in (
        ("later.png", b"later"),
        ("original.png", b"original"),
        ("first.png", b"first"),
    ):
        source = tmp_path / name
        source.write_bytes(payload)
        sources.append(services.store.store_copy(source, role="derived_figure"))
    with problem_chat._session() as session:
        session.add_all(
            [
                Asset(
                    id="asset_later",
                    problem_id=problem.id,
                    role="derived_figure",
                    sha256=sources[0].sha256,
                    relative_path=sources[0].relative_path,
                    mime_type="image/png",
                ),
                Asset(
                    id="asset_original",
                    problem_id=problem.id,
                    role="original",
                    sha256=sources[1].sha256,
                    relative_path=sources[1].relative_path,
                    mime_type="image/png",
                ),
                Asset(
                    id="asset_first",
                    problem_id=problem.id,
                    role="derived_figure",
                    sha256=sources[2].sha256,
                    relative_path=sources[2].relative_path,
                    mime_type="image/png",
                ),
            ]
        )
        stored_problem = session.get(type(problem), problem.id)
        stored_problem.question_content_json = json.dumps(
            [
                {"type": "figure", "derived_asset_id": "asset_first"},
                {"type": "figure", "derived_asset_id": "asset_later"},
            ]
        )
        session.commit()

    sources_result = problem_chat.list_reference_sources(problem.id)

    assert [item["asset_id"] for item in sources_result] == [
        "asset_first",
        "asset_later",
    ]


def test_legacy_include_original_flag_is_ignored(
    chat: tuple[AppServices, ProblemChatService],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    services, problem_chat = chat
    problem = services.create_problem(title="无图题", status="active")
    provider = _CapturingChatProvider()
    monkeypatch.setattr(chat_module, "get_provider", lambda *_args: provider)
    conversation = problem_chat.create_conversation(
        problem.id,
        include_original_image=True,
    )

    assert problem_chat.send_message(conversation.id, "解释题目").status == "complete"
    assert conversation.include_original_image is False
    assert all(isinstance(message["content"], str) for message in provider.requests[0])


def test_legacy_original_asset_is_never_sent_to_ai(
    chat: tuple[AppServices, ProblemChatService],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    services, problem_chat = chat
    problem = services.create_problem(title="大图题", status="active")
    source = tmp_path / "large.png"
    source.write_bytes(b"12345")
    stored = services.store.store_copy(source, role="original")
    with problem_chat._session() as session:
        session.add(
            Asset(
                id="asset_large",
                problem_id=problem.id,
                role="original",
                sha256=stored.sha256,
                relative_path=stored.relative_path,
                mime_type="image/png",
            )
        )
        session.commit()
    provider = _CapturingChatProvider()
    monkeypatch.setattr(chat_module, "get_provider", lambda *_args: provider)
    conversation = problem_chat.create_conversation(
        problem.id, include_original_image=True
    )

    reply = problem_chat.send_message(conversation.id, "解释大图")

    assert reply.status == "complete"
    assert all(isinstance(message["content"], str) for message in provider.requests[0])


def test_retry_reuses_failed_user_message_without_duplicate_prompt(
    chat: tuple[AppServices, ProblemChatService],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    services, problem_chat = chat
    problem = services.create_problem(title="重试题", status="active")
    provider = _CapturingChatProvider(fail_count=1)
    monkeypatch.setattr(chat_module, "get_provider", lambda *_args: provider)
    conversation = problem_chat.create_conversation(problem.id)

    failed = problem_chat.send_message(conversation.id, "保留这条问题")
    assert failed.role == "user"
    assert failed.status == "failed"

    reply = problem_chat.send_message(conversation.id, "保留这条问题")
    assert reply.role == "assistant"
    loaded = problem_chat.get_conversation(conversation.id)
    assert loaded is not None
    assert [
        (message.role, message.status, message.sequence, message.content_markdown)
        for message in loaded.messages
    ] == [
        ("user", "complete", 1, "保留这条问题"),
        ("assistant", "complete", 2, "回答"),
    ]
    successful_user_prompts = [
        message
        for message in provider.requests[-1]
        if message["role"] == "user" and message["content"] == "保留这条问题"
    ]
    assert len(successful_user_prompts) == 1


def test_visual_reference_snapshot_is_ordered_and_immutable(
    chat: tuple[AppServices, ProblemChatService],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    services, problem_chat = chat
    problem = services.create_problem(title="框选题", status="active")
    image_path = tmp_path / "source.png"
    image = QImage(40, 20, QImage.Format.Format_RGB32)
    image.fill(QColor("#FF0000"))
    assert image.save(str(image_path), "PNG")
    stored = services.store.store_copy(image_path, role="derived_figure")
    with problem_chat._session() as session:
        session.add(
            Asset(
                id="asset_reference",
                problem_id=problem.id,
                role="derived_figure",
                sha256=stored.sha256,
                relative_path=stored.relative_path,
                mime_type="image/png",
            )
        )
        stored_problem = session.get(type(problem), problem.id)
        stored_problem.question_content_json = json.dumps(
            [{"type": "figure", "derived_asset_id": "asset_reference"}]
        )
        session.commit()
    provider = _CapturingChatProvider()
    monkeypatch.setattr(chat_module, "get_provider", lambda *_args: provider)
    conversation = problem_chat.create_conversation(problem.id)
    reference = ProblemReference("asset_reference", 0, 0.25, 0.0, 0.5, 1.0)

    assert (
        problem_chat.send_message(conversation.id, "只解释中间部分", [reference]).status
        == "complete"
    )
    loaded = problem_chat.get_conversation(conversation.id)
    assert loaded is not None
    assert (
        loaded.messages[0].reference_snapshot_json
        == '[{"asset_id": "asset_reference", "page_index": 0, "x": 0.25, "y": 0.0, "width": 0.5, "height": 1.0}]'
    )
    payload = provider.requests[0][1]["content"]
    assert payload[0]["type"] == "text"
    assert payload[2]["type"] == "image_url"
    assert payload[2]["image_url"]["url"].startswith("data:image/png;base64,")
    crop = QImage.fromData(
        base64.b64decode(payload[2]["image_url"]["url"].split(",", 1)[1])
    )
    assert crop.size().width() == 20
    assert crop.size().height() == 20

    with pytest.raises(DomainError, match="不属于当前题目"):
        problem_chat.send_message(
            conversation.id,
            "伪造引用",
            [ProblemReference("asset_reference", 1, 0.0, 0.0, 1.0, 1.0)],
        )


def test_visual_references_reject_excessive_count_before_persisting(
    chat: tuple[AppServices, ProblemChatService],
) -> None:
    services, problem_chat = chat
    problem = services.create_problem(title="引用数量题", status="active")
    conversation = problem_chat.create_conversation(problem.id)
    reference = ProblemReference("missing", 0, 0.0, 0.0, 1.0, 1.0)

    with pytest.raises(DomainError, match="20"):
        problem_chat.send_message(
            conversation.id,
            "过多引用",
            [reference] * (chat_module._MAX_CHAT_REFERENCE_COUNT + 1),
        )

    loaded = problem_chat.get_conversation(conversation.id)
    assert loaded is not None
    assert loaded.messages == []


def test_visual_reference_rejects_decompression_bomb_before_ai(
    chat: tuple[AppServices, ProblemChatService],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    services, problem_chat = chat
    problem = services.create_problem(title="超大像素引用", status="active")
    image_path = tmp_path / "large-pixels.png"
    image = QImage(40, 20, QImage.Format.Format_RGB32)
    image.fill(QColor("#FF0000"))
    assert image.save(str(image_path), "PNG")
    stored = services.store.store_copy(image_path, role="derived_figure")
    with problem_chat._session() as session:
        session.add(
            Asset(
                id="asset_large_pixels",
                problem_id=problem.id,
                role="derived_figure",
                sha256=stored.sha256,
                relative_path=stored.relative_path,
                mime_type="image/png",
            )
        )
        stored_problem = session.get(type(problem), problem.id)
        stored_problem.question_content_json = json.dumps(
            [{"type": "figure", "derived_asset_id": "asset_large_pixels"}]
        )
        session.commit()
    monkeypatch.setattr(chat_module, "_MAX_CHAT_REFERENCE_SOURCE_PIXELS", 100)
    provider = _CapturingChatProvider()
    monkeypatch.setattr(chat_module, "get_provider", lambda *_args: provider)
    conversation = problem_chat.create_conversation(problem.id)

    failed = problem_chat.send_message(
        conversation.id,
        "解释选区",
        [ProblemReference("asset_large_pixels", 0, 0.0, 0.0, 1.0, 1.0)],
    )

    assert failed.status == "failed"
    assert "像素" in failed.error_message
    assert provider.requests == []
