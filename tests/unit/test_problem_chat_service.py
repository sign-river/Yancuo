"""Problem-scoped conversations persist locally and remain tied to a revision."""

from __future__ import annotations

import base64
from datetime import datetime, timezone
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
from yancuo_win.application.problem_chat_service import ProblemChatService, ProblemReference
from yancuo_win.application.services import AppServices
from yancuo_win.config.settings import default_toml_path
from yancuo_win.data.models import Asset


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
def chat(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[AppServices, ProblemChatService]:
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
    export = problem_chat.export_conversation_markdown(conversation.id, tmp_path / "chat.md")
    assert "积分讨论" in export.read_text(encoding="utf-8")


def test_failed_chat_keeps_pending_user_message_as_failed(
    chat: tuple[AppServices, ProblemChatService]
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


def test_original_images_use_stable_created_order_and_ignore_other_roles(
    chat: tuple[AppServices, ProblemChatService],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    services, problem_chat = chat
    problem = services.create_problem(title="多图题", status="active")
    sources = []
    for name, payload in (
        ("later.png", b"later"),
        ("processed.png", b"processed"),
        ("first.png", b"first"),
    ):
        source = tmp_path / name
        source.write_bytes(payload)
        sources.append(services.store.store_copy(source, role="original"))
    with problem_chat._session() as session:
        session.add_all(
            [
                Asset(
                    id="asset_later",
                    problem_id=problem.id,
                    role="original",
                    sha256=sources[0].sha256,
                    relative_path=sources[0].relative_path,
                    mime_type="image/png",
                    created_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
                ),
                Asset(
                    id="asset_processed",
                    problem_id=problem.id,
                    role="processed",
                    sha256=sources[1].sha256,
                    relative_path=sources[1].relative_path,
                    mime_type="image/png",
                    created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                ),
                Asset(
                    id="asset_first",
                    problem_id=problem.id,
                    role="original",
                    sha256=sources[2].sha256,
                    relative_path=sources[2].relative_path,
                    mime_type="image/png",
                    created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                ),
            ]
        )
        session.commit()

    provider = _CapturingChatProvider()
    monkeypatch.setattr(chat_module, "get_provider", lambda *_args: provider)
    conversation = problem_chat.create_conversation(
        problem.id,
        include_original_image=True,
    )
    reply = problem_chat.send_message(conversation.id, "按图片顺序解释")

    assert reply.status == "complete"
    image_content = provider.requests[0][-1]["content"]
    encoded_images = [
        item["image_url"]["url"].split(",", 1)[1]
        for item in image_content
        if item["type"] == "image_url"
    ]
    assert [base64.b64decode(value) for value in encoded_images] == [
        b"first",
        b"later",
    ]
    assert problem_chat._original_image_context(problem.id) == image_content


def test_missing_original_image_safely_falls_back_to_text_chat(
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
    stored = services.store.store_copy(image_path, role="original")
    with problem_chat._session() as session:
        session.add(Asset(id="asset_reference", problem_id=problem.id, role="original", sha256=stored.sha256, relative_path=stored.relative_path, mime_type="image/png"))
        session.commit()
    provider = _CapturingChatProvider()
    monkeypatch.setattr(chat_module, "get_provider", lambda *_args: provider)
    conversation = problem_chat.create_conversation(problem.id)
    reference = ProblemReference("asset_reference", 0, 0.25, 0.0, 0.5, 1.0)

    assert problem_chat.send_message(conversation.id, "只解释中间部分", [reference]).status == "complete"
    loaded = problem_chat.get_conversation(conversation.id)
    assert loaded is not None
    assert loaded.messages[0].reference_snapshot_json == '[{"asset_id": "asset_reference", "page_index": 0, "x": 0.25, "y": 0.0, "width": 0.5, "height": 1.0}]'
    payload = provider.requests[0][1]["content"]
    assert payload[0]["type"] == "text"
    assert payload[2]["type"] == "image_url"
